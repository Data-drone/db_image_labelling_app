"""In-app Cluster Map projection: cache, background UMAP, and GET helpers.

UMAP never runs on the HTTP request. GET either returns cached points or 202.
The projection itself runs in a spawned subprocess so a timeout can kill it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import multiprocessing
import os
import threading
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from .models import Annotation, ClusterMapPointCache, ClusterMapRun, ProjectSample
from .schemas import ClusterMapPoint, ClusterMapResult

log = logging.getLogger(__name__)

WORKER_TIMEOUT_SEC = float(os.environ.get("CLUSTER_MAP_TIMEOUT_SEC", "600"))
# Covers embedding load, subprocess start, and persisting points around the projection.
STALE_GRACE_SEC = 120
MIN_SAMPLES = 3

_locks_guard = threading.Lock()
_project_locks: dict[int, threading.Lock] = {}
_active_runs: set[int] = set()


class ProjectionTimeout(Exception):
    pass


def _project_lock(project_id: int) -> threading.Lock:
    with _locks_guard:
        return _project_locks.setdefault(project_id, threading.Lock())


def fingerprint_ids(sample_ids: list[int]) -> str:
    payload = ",".join(str(i) for i in sample_ids)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{len(sample_ids)}:{digest}"


def embedded_sample_ids(db: Session, project_id: int) -> list[int]:
    rows = (
        db.query(ProjectSample.id)
        .filter(
            ProjectSample.project_id == project_id,
            ProjectSample.embedding.isnot(None),
        )
        .order_by(ProjectSample.id)
        .all()
    )
    return [r.id for r in rows]


def compute_umap_projection(embeddings: list[list[float]]) -> list[tuple[float, float]]:
    """Reduce high-dimensional embeddings to 2D using UMAP (preferred) or t-SNE."""
    import numpy as np

    matrix = np.array(embeddings, dtype=np.float32)
    n = len(matrix)

    try:
        from umap import UMAP
        n_neighbors = min(15, max(2, n - 1))
        reducer = UMAP(
            n_components=2, n_neighbors=n_neighbors, min_dist=0.1,
            metric="cosine", random_state=42, n_jobs=1, low_memory=True,
        )
        coords = reducer.fit_transform(matrix)
    except ImportError:
        from sklearn.manifold import TSNE
        perplexity = min(30.0, max(1.0, (n - 1) / 3.0))
        reducer = TSNE(
            n_components=2, perplexity=perplexity, metric="cosine",
            random_state=42, init="pca",
        )
        coords = reducer.fit_transform(matrix)

    x_min, x_max = coords[:, 0].min(), coords[:, 0].max()
    y_min, y_max = coords[:, 1].min(), coords[:, 1].max()
    x_range = x_max - x_min if x_max != x_min else 1.0
    y_range = y_max - y_min if y_max != y_min else 1.0
    coords[:, 0] = (coords[:, 0] - x_min) / x_range
    coords[:, 1] = (coords[:, 1] - y_min) / y_range

    return [(float(coords[i, 0]), float(coords[i, 1])) for i in range(n)]


def load_cached_points(db: Session, project_id: int) -> dict[int, tuple[float, float]]:
    rows = (
        db.query(ClusterMapPointCache)
        .filter_by(project_id=project_id)
        .all()
    )
    return {r.sample_id: (r.x, r.y) for r in rows}


def _labels_for_samples(db: Session, project_id: int, sample_ids: list[int]) -> dict[int, list[str]]:
    if not sample_ids:
        return {}
    label_rows = (
        db.query(Annotation.sample_id, Annotation.label)
        .filter(
            Annotation.project_id == project_id,
            Annotation.sample_id.in_(sample_ids),
            Annotation.is_draft.is_(False),
        )
        .all()
    )
    labels_map: dict[int, list[str]] = {}
    for row in label_rows:
        labels_map.setdefault(row.sample_id, []).append(row.label)
    return labels_map


def build_result(
    db: Session,
    project_id: int,
    sample_ids: list[int],
    coords: dict[int, tuple[float, float]],
    cached: bool,
    recompute_error: str | None = None,
) -> ClusterMapResult:
    samples = (
        db.query(ProjectSample.id, ProjectSample.filename, ProjectSample.status)
        .filter(ProjectSample.id.in_(sample_ids))
        .all()
    )
    meta = {s.id: s for s in samples}
    labels_map = _labels_for_samples(db, project_id, sample_ids)
    points = []
    for sid in sample_ids:
        s = meta.get(sid)
        xy = coords.get(sid)
        if s is None or xy is None:
            continue
        points.append(
            ClusterMapPoint(
                sample_id=sid,
                filename=s.filename,
                x=xy[0],
                y=xy[1],
                status=s.status,
                labels=labels_map.get(sid, []),
            )
        )
    return ClusterMapResult(
        points=points, total=len(points), cached=cached, recompute_error=recompute_error,
    )


def latest_run(db: Session, project_id: int) -> ClusterMapRun | None:
    return (
        db.query(ClusterMapRun)
        .filter_by(project_id=project_id)
        .order_by(ClusterMapRun.id.desc())
        .first()
    )


def latest_ready_run(db: Session, project_id: int) -> ClusterMapRun | None:
    return (
        db.query(ClusterMapRun)
        .filter_by(project_id=project_id, status="ready")
        .order_by(ClusterMapRun.id.desc())
        .first()
    )


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def run_is_stale(run: ClusterMapRun, now: datetime | None = None) -> bool:
    if run.status != "running":
        return False
    with _locks_guard:
        if run.id in _active_runs:
            return False
    now = now or datetime.now(timezone.utc)
    created = _aware(run.created_at)
    if created is None:
        return True
    return (now - created).total_seconds() > WORKER_TIMEOUT_SEC + STALE_GRACE_SEC


def mark_run_failed(db: Session, run: ClusterMapRun, message: str) -> None:
    run.status = "failed"
    run.error_message = (message or "")[:4000]
    run.finished_at = datetime.now(timezone.utc)
    db.commit()


def _parse_embedding_text(raw) -> list[float] | None:
    try:
        if raw is None:
            return None
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        if isinstance(raw, str):
            if raw.startswith("["):
                emb = json.loads(raw)
            else:
                emb = [float(x) for x in raw.strip("[]").split(",")]
        elif isinstance(raw, (list, tuple)):
            emb = list(raw)
        else:
            return None
        if not emb:
            return None
        return [float(x) for x in emb]
    except Exception:
        return None


def load_embeddings(db: Session, project_id: int) -> tuple[list[int], list[list[float]]]:
    """Return aligned sample ids and embedding vectors for the project."""
    use_pgvec = False
    try:
        use_pgvec = db.bind.dialect.name == "postgresql"
    except Exception:
        use_pgvec = False

    # Same row filter as embedded_sample_ids; embedding_vec can lag the JSON
    # column until the startup backfill runs.
    if use_pgvec:
        try:
            emb_rows = db.execute(text(
                "SELECT id, COALESCE(embedding_vec::text, embedding::text) AS emb_text "
                "FROM project_samples "
                "WHERE project_id = :pid AND embedding IS NOT NULL "
                "ORDER BY id"
            ), {"pid": project_id}).fetchall()
        except Exception:
            db.rollback()
            use_pgvec = False
            emb_rows = []
    else:
        emb_rows = []

    if not use_pgvec:
        dialect = getattr(getattr(db.bind, "dialect", None), "name", "") or ""
        emb_sql = (
            "SELECT id, embedding::text AS emb_text "
            "FROM project_samples "
            "WHERE project_id = :pid AND embedding IS NOT NULL "
            "ORDER BY id"
            if dialect == "postgresql"
            else
            "SELECT id, embedding AS emb_text "
            "FROM project_samples "
            "WHERE project_id = :pid AND embedding IS NOT NULL "
            "ORDER BY id"
        )
        emb_rows = db.execute(text(emb_sql), {"pid": project_id}).fetchall()

    ids: list[int] = []
    embeddings: list[list[float]] = []
    for r in emb_rows:
        emb = _parse_embedding_text(r.emb_text)
        if emb is None:
            continue
        ids.append(r.id)
        embeddings.append(emb)
    return ids, embeddings


def persist_points(
    db: Session,
    project_id: int,
    sample_ids: list[int],
    coords: list[tuple[float, float]],
) -> None:
    """Replace the project's cached points. Caller commits."""
    db.query(ClusterMapPointCache).filter_by(project_id=project_id).delete(
        synchronize_session=False
    )
    db.add_all(
        [
            ClusterMapPointCache(
                project_id=project_id, sample_id=sid, x=float(x), y=float(y),
            )
            for sid, (x, y) in zip(sample_ids, coords)
        ]
    )


def _projection_child(fn, embeddings, conn) -> None:
    try:
        conn.send(("ok", fn(embeddings)))
    except BaseException as exc:
        conn.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        conn.close()


def project_embeddings(
    embeddings: list[list[float]], timeout: float,
) -> list[tuple[float, float]]:
    """Run compute_umap_projection in a killable subprocess."""
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    proc = ctx.Process(
        target=_projection_child,
        args=(compute_umap_projection, embeddings, sender),
        daemon=True,
        name="cluster-map-umap",
    )
    proc.start()
    sender.close()
    try:
        if not receiver.poll(timeout):
            raise ProjectionTimeout()
        try:
            status, payload = receiver.recv()
        except EOFError:
            proc.join(5)
            raise RuntimeError(
                f"Embedding projection process exited unexpectedly (exit code {proc.exitcode})."
            ) from None
        if status != "ok":
            raise RuntimeError(payload)
        return payload
    finally:
        if proc.is_alive():
            proc.terminate()
            proc.join(5)
            if proc.is_alive():
                proc.kill()
        proc.join(5)
        receiver.close()


def _timeout_message() -> str:
    return f"Embedding projection timed out after {WORKER_TIMEOUT_SEC:g}s. Retry to try again."


def _open_session():
    from .deps import get_session_factory, is_lakebase

    if is_lakebase():
        from .lakebase import get_session
        return get_session()
    return get_session_factory()()


def run_cluster_map_worker(run_id: int, project_id: int) -> None:
    with _locks_guard:
        _active_runs.add(run_id)
    try:
        _run_cluster_map_worker(run_id, project_id)
    finally:
        with _locks_guard:
            _active_runs.discard(run_id)


def _run_cluster_map_worker(run_id: int, project_id: int) -> None:
    import time as _time

    _time.sleep(0.15)

    gen_db = None
    for attempt in range(5):
        try:
            gen_db = _open_session()
            gen_db.execute(text("SELECT 1"))
            break
        except Exception as sess_err:
            log.warning("Cluster map worker session attempt %d failed: %s", attempt + 1, sess_err)
            if gen_db is not None:
                try:
                    gen_db.close()
                except Exception:
                    pass
                gen_db = None
            _time.sleep(0.2 * (attempt + 1))
    if gen_db is None:
        log.error("Could not open DB session for cluster map run %d", run_id)
        return

    try:
        run = gen_db.get(ClusterMapRun, run_id)
        if run is None:
            return

        sample_ids, embeddings = load_embeddings(gen_db, project_id)
        if len(sample_ids) < MIN_SAMPLES:
            mark_run_failed(
                gen_db, run,
                f"Need at least {MIN_SAMPLES} embedded samples for projection, found {len(sample_ids)}.",
            )
            return

        # The GET handler compares against embedded_sample_ids, so fingerprint
        # that set even when some rows have unparseable embeddings.
        run.sample_count = len(sample_ids)
        run.sample_fingerprint = fingerprint_ids(embedded_sample_ids(gen_db, project_id))
        gen_db.commit()

        try:
            coords = project_embeddings(embeddings, WORKER_TIMEOUT_SEC)
        except ProjectionTimeout:
            mark_run_failed(gen_db, run, _timeout_message())
            return
        except Exception as exc:
            log.exception("UMAP projection failed for project %s", project_id)
            mark_run_failed(gen_db, run, str(exc)[:4000])
            return

        persist_points(gen_db, project_id, sample_ids, coords)
        run.status = "ready"
        run.finished_at = datetime.now(timezone.utc)
        run.error_message = None
        gen_db.commit()
    except Exception as exc:
        log.exception("Cluster map worker failed for run %d", run_id)
        try:
            gen_db.rollback()
            run = gen_db.get(ClusterMapRun, run_id)
            if run:
                mark_run_failed(gen_db, run, str(exc)[:4000])
        except Exception:
            gen_db.rollback()
    finally:
        gen_db.close()


def start_cluster_map_worker(run_id: int, project_id: int) -> None:
    with _locks_guard:
        _active_runs.add(run_id)
    threading.Thread(
        target=run_cluster_map_worker,
        args=(run_id, project_id),
        daemon=True,
        name=f"cluster-map-{project_id}",
    ).start()


def enqueue_projection(db: Session, project_id: int, sample_ids: list[int]) -> ClusterMapRun:
    """Create a running row and start the worker. Caller must hold the project lock."""
    run = ClusterMapRun(
        project_id=project_id,
        status="running",
        sample_count=len(sample_ids),
        sample_fingerprint=fingerprint_ids(sample_ids),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    start_cluster_map_worker(run.id, project_id)
    return run


def ensure_projection_started(db: Session, project_id: int, sample_ids: list[int]) -> ClusterMapRun:
    with _project_lock(project_id):
        existing = (
            db.query(ClusterMapRun)
            .filter_by(project_id=project_id, status="running")
            .order_by(ClusterMapRun.id.desc())
            .first()
        )
        if existing and not run_is_stale(existing):
            return existing
        if existing and run_is_stale(existing):
            mark_run_failed(db, existing, _timeout_message())
        return enqueue_projection(db, project_id, sample_ids)
