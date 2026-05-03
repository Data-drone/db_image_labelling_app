"""
Pre-annotation routes — predict, batch pre-annotate, endpoint health.
"""

import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..deps import get_db
from ..inference import (
    resolve_endpoint,
    check_endpoint_health,
    predict_sample,
    get_default_endpoint,
    resolve_use_data_plane,
)
from ..inference_adapters import UnknownInferenceAdapterError
from ..models import LabelingProject, ProjectSample
from ..preannotate import run_preannotate_for_samples
from ..preannotate_triggers import resolve_preannotate_job_id
from ..schemas import (
    PredictionOut,
    PreAnnotateRequest,
    PreAnnotateProgress,
    EmbeddingGenerateRequest,
    EndpointStatus,
    InferenceDefaultsOut,
)
from ..volumes import read_image_bytes

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["inference"])


def _get_project(project_id: int, db: Session) -> LabelingProject:
    p = db.query(LabelingProject).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")
    return p


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _resolve_workspace_host() -> str | None:
    """Return the Databricks workspace URL (with https:// scheme)."""
    host = os.environ.get("DATABRICKS_HOST", "").strip().rstrip("/")
    if not host:
        try:
            from ..volumes import _get_workspace_client
            w = _get_workspace_client()
            host = (w.config.host or "").strip().rstrip("/")
        except Exception:
            return None
    if not host:
        return None
    if not host.startswith("https://") and not host.startswith("http://"):
        host = f"https://{host}"
    return host


@router.get("/endpoint-status", response_model=EndpointStatus)
def get_endpoint_status(project_id: int, db: Session = Depends(get_db)):
    """Check the health of the serving endpoint configured for this project."""
    project = _get_project(project_id, db)
    endpoint_name = resolve_endpoint(project)

    if not endpoint_name:
        return EndpointStatus(status="not_configured")

    try:
        result = check_endpoint_health(endpoint_name)
        return EndpointStatus(**result)
    except Exception as e:
        return EndpointStatus(
            status="error", endpoint=endpoint_name, error=str(e),
        )


@router.get(
    "/samples/{sample_id}/predict",
    response_model=list[PredictionOut],
)
def predict_single_sample(
    project_id: int,
    sample_id: int,
    db: Session = Depends(get_db),
):
    """Get model predictions for a single sample without saving them."""
    project = _get_project(project_id, db)
    endpoint_name = resolve_endpoint(project)
    if not endpoint_name:
        raise HTTPException(
            status_code=400,
            detail="No serving endpoint configured for this project.",
        )

    sample = (
        db.query(ProjectSample)
        .filter_by(id=sample_id, project_id=project_id)
        .first()
    )
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found.")

    image_bytes = read_image_bytes(sample.filepath)
    if not image_bytes:
        raise HTTPException(status_code=500, detail="Could not read image file.")

    try:
        predictions = predict_sample(
            endpoint_name=endpoint_name,
            image_bytes=image_bytes,
            task_type=project.task_type,
            class_list=list(project.class_list),
            endpoint_config=project.endpoint_config,
        )
    except UnknownInferenceAdapterError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.error("Prediction failed for sample %d: %s", sample_id, e)
        raise HTTPException(
            status_code=502,
            detail=f"Model endpoint error: {e}",
        )

    return [PredictionOut(**p) for p in predictions]


@router.post("/pre-annotate")
def pre_annotate_project(
    project_id: int,
    payload: PreAnnotateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Batch pre-annotate with SSE progress streaming.

    When the client sends ``Accept: text/event-stream`` the response is an SSE
    stream emitting ``progress`` events after each sample and a final ``done``
    event.  Otherwise falls back to the original JSON response.
    """
    project = _get_project(project_id, db)
    endpoint_name = resolve_endpoint(project)
    if not endpoint_name:
        raise HTTPException(
            status_code=400,
            detail="No serving endpoint configured for this project.",
        )

    health = check_endpoint_health(endpoint_name)
    if health["status"] != "ready":
        raise HTTPException(
            status_code=503,
            detail=f"Endpoint '{endpoint_name}' is not ready: {health.get('error') or health.get('state', 'unknown')}",
        )

    if payload.include_pre_labeled:
        query = (
            db.query(ProjectSample)
            .filter(
                ProjectSample.project_id == project_id,
                or_(
                    ProjectSample.status == "unlabeled",
                    ProjectSample.status == "pre_labeled",
                ),
            )
            .order_by(ProjectSample.id)
        )
    else:
        query = (
            db.query(ProjectSample)
            .filter_by(project_id=project_id, status="unlabeled")
            .order_by(ProjectSample.id)
        )

    if payload.max_samples and payload.max_samples > 0:
        query = query.limit(payload.max_samples)

    samples = query.all()

    wants_sse = "text/event-stream" in (request.headers.get("accept") or "")

    if not wants_sse:
        try:
            result = run_preannotate_for_samples(
                db, project, samples,
                min_confidence=payload.min_confidence,
                text_prompt=payload.text_prompt,
            )
        except UnknownInferenceAdapterError as e:
            db.rollback()
            raise HTTPException(status_code=400, detail=str(e)) from e
        db.commit()
        return PreAnnotateProgress(
            completed=result["completed"],
            failed=result["failed"],
            skipped=result["skipped"],
            total=result["total"],
        )

    def _sse_generator():
        from ..inference import predict_sample as _predict_sample, resolve_endpoint as _resolve_ep
        from ..models import Annotation
        from ..preannotate import (
            clear_model_drafts_for_sample,
            refresh_sample_status_after_annotation_change,
        )

        ep = _resolve_ep(project)
        ep_config = dict(project.endpoint_config or {})
        if payload.min_confidence is not None:
            ep_config["min_confidence"] = payload.min_confidence
        if payload.text_prompt:
            ep_config["sam_text_prompt"] = payload.text_prompt
        created_by = f"model:{ep}"

        total = len(samples)
        completed = failed = skipped = 0

        def _emit(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data)}\n\n"

        yield _emit("progress", {"completed": 0, "failed": 0, "skipped": 0, "total": total, "current": 0})

        for idx, sample in enumerate(samples, 1):
            img = read_image_bytes(sample.filepath)
            if not img:
                failed += 1
                refresh_sample_status_after_annotation_change(db, project.id, sample.id)
                yield _emit("progress", {"completed": completed, "failed": failed, "skipped": skipped, "total": total, "current": idx})
                continue

            clear_model_drafts_for_sample(db, project.id, sample.id)

            try:
                preds = _predict_sample(
                    endpoint_name=ep,
                    image_bytes=img,
                    task_type=project.task_type,
                    class_list=list(project.class_list),
                    endpoint_config=ep_config,
                )
            except Exception as exc:
                log.warning("Pre-annotate failed for sample %d: %s", sample.id, exc)
                failed += 1
                refresh_sample_status_after_annotation_change(db, project.id, sample.id)
                yield _emit("progress", {"completed": completed, "failed": failed, "skipped": skipped, "total": total, "current": idx})
                continue

            if not preds:
                skipped += 1
                refresh_sample_status_after_annotation_change(db, project.id, sample.id)
                yield _emit("progress", {"completed": completed, "failed": failed, "skipped": skipped, "total": total, "current": idx})
                continue

            for pred in preds:
                db.add(Annotation(
                    sample_id=sample.id,
                    project_id=project.id,
                    label=pred["label"],
                    ann_type=pred["ann_type"],
                    bbox_json=pred.get("bbox_json"),
                    is_draft=True,
                    created_by=created_by,
                ))
            sample.status = "pre_labeled"
            completed += 1

            if completed % 25 == 0:
                db.flush()

            yield _emit("progress", {"completed": completed, "failed": failed, "skipped": skipped, "total": total, "current": idx})

        db.commit()
        yield _emit("done", {"completed": completed, "failed": failed, "skipped": skipped, "total": total})

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/generate-embeddings")
def generate_embeddings(
    project_id: int,
    payload: EmbeddingGenerateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Start embedding generation. Creates a persistent EmbeddingRun row and
    runs the work in a background thread so progress survives page reloads."""
    import threading
    from ..embeddings import resolve_embedding_endpoint
    from ..models import EmbeddingRun
    from ..schemas import EmbeddingRunOut

    project = _get_project(project_id, db)
    endpoint_name = resolve_embedding_endpoint(project)
    if not endpoint_name:
        raise HTTPException(status_code=400, detail="No embedding endpoint configured.")

    health = check_endpoint_health(endpoint_name)
    if health["status"] != "ready":
        raise HTTPException(
            status_code=503,
            detail=f"Embedding endpoint '{endpoint_name}' is not ready: {health.get('error') or health.get('state', 'unknown')}",
        )

    active = db.query(EmbeddingRun).filter_by(project_id=project_id, status="running").first()
    if active:
        return EmbeddingRunOut.model_validate(active)

    query = (
        db.query(ProjectSample)
        .filter_by(project_id=project_id)
        .order_by(ProjectSample.id)
    )
    if not payload.force:
        query = query.filter(ProjectSample.embedding.is_(None))
    if payload.max_samples and payload.max_samples > 0:
        query = query.limit(payload.max_samples)

    sample_ids = [s.id for s in query.all()]

    user_email = ""
    try:
        from ..deps import get_user_email
        user_email = get_user_email(request)
    except Exception:
        pass

    run_row = EmbeddingRun(
        project_id=project_id,
        status="running",
        total_planned=len(sample_ids),
        force=payload.force,
        created_by=user_email,
    )
    db.add(run_row)
    db.commit()
    db.refresh(run_row)

    run_id = run_row.id
    force = payload.force
    ep_config = dict(project.endpoint_config or {})

    def _background_worker():
        import time as _time
        from datetime import datetime, timezone
        from ..deps import is_lakebase, get_session_factory
        from ..embeddings import set_sample_embedding, _prefetch_images
        from ..inference_adapters import get_embedding_adapter as _get_embedding_adapter
        from ..inference_adapters.dinov3 import BATCH_SIZE
        from ..models import EmbeddingRun as ER

        # Brief pause so the request's DB session can fully commit and
        # return its connection to the pool before we grab one.
        _time.sleep(0.5)

        def _open_session():
            if is_lakebase():
                from ..lakebase import get_session
                return get_session()
            return get_session_factory()()

        # Retry session acquisition — Lakebase may still be provisioning
        # a new pooled connection (ISCE error).
        gen_db = None
        for attempt in range(5):
            try:
                gen_db = _open_session()
                gen_db.execute(__import__("sqlalchemy").text("SELECT 1"))
                break
            except Exception as sess_err:
                log.warning("Embedding worker session attempt %d failed: %s", attempt + 1, sess_err)
                if gen_db is not None:
                    try:
                        gen_db.close()
                    except Exception:
                        pass
                    gen_db = None
                _time.sleep(2 * (attempt + 1))
        if gen_db is None:
            log.error("Could not open DB session for embedding run %d after retries", run_id)
            # Last-ditch attempt to mark the run as failed
            try:
                s = _open_session()
                r = s.get(ER, run_id)
                if r:
                    r.status = "failed"
                    r.error_message = "Could not acquire database connection"
                    r.finished_at = datetime.now(timezone.utc)
                    s.commit()
                s.close()
            except Exception:
                pass
            return

        try:
            adapter = _get_embedding_adapter()
            samples = (
                gen_db.query(ProjectSample)
                .filter(ProjectSample.id.in_(sample_ids))
                .order_by(ProjectSample.id)
                .all()
            )

            completed = failed = skipped = 0
            eligible = []
            for sample in samples:
                if not force and sample.embedding is not None:
                    skipped += 1
                else:
                    eligible.append(sample)

            run = gen_db.get(ER, run_id)
            run.skipped = skipped
            gen_db.commit()

            for batch_start in range(0, len(eligible), BATCH_SIZE):
                batch = eligible[batch_start:batch_start + BATCH_SIZE]
                images = _prefetch_images(batch)

                ready_samples = []
                ready_bytes = []
                for s in batch:
                    img = images.get(s.id)
                    if not img:
                        failed += 1
                    else:
                        ready_samples.append(s)
                        ready_bytes.append(img)

                if ready_bytes:
                    try:
                        embeddings = adapter.batch_query_embedding(
                            endpoint_name, ready_bytes, ep_config,
                        )
                    except Exception as exc:
                        log.warning("Batch embedding failed: %s", exc)
                        failed += len(ready_samples)
                        embeddings = []

                    for s, emb in zip(ready_samples, embeddings):
                        if emb is None:
                            failed += 1
                        else:
                            set_sample_embedding(s, emb, gen_db)
                            completed += 1

                run = gen_db.get(ER, run_id)
                run.completed = completed
                run.failed = failed
                gen_db.commit()

            run = gen_db.get(ER, run_id)
            run.status = "succeeded"
            run.finished_at = datetime.now(timezone.utc)
            gen_db.commit()

        except Exception as exc:
            log.exception("Embedding generation failed for run %d", run_id)
            try:
                run = gen_db.get(ER, run_id)
                if run:
                    run.status = "failed"
                    run.error_message = str(exc)[:4000]
                    run.finished_at = datetime.now(timezone.utc)
                    gen_db.commit()
            except Exception:
                gen_db.rollback()
        finally:
            gen_db.close()

    threading.Thread(target=_background_worker, daemon=True).start()
    return EmbeddingRunOut.model_validate(run_row)


@router.get("/embedding-runs/latest")
def get_latest_embedding_run(project_id: int, db: Session = Depends(get_db)):
    from ..models import EmbeddingRun
    from ..schemas import EmbeddingRunOut
    _get_project(project_id, db)
    row = db.query(EmbeddingRun).filter_by(project_id=project_id).order_by(EmbeddingRun.id.desc()).first()
    if not row:
        raise HTTPException(status_code=404, detail="No embedding runs for this project.")
    return EmbeddingRunOut.model_validate(row)


@router.get("/embedding-runs/{run_id}")
def get_embedding_run(project_id: int, run_id: int, db: Session = Depends(get_db)):
    from ..models import EmbeddingRun
    from ..schemas import EmbeddingRunOut
    _get_project(project_id, db)
    row = db.query(EmbeddingRun).filter_by(id=run_id, project_id=project_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Embedding run not found.")
    return EmbeddingRunOut.model_validate(row)


@router.get("/settings")
def get_project_inference_settings(
    project_id: int,
    db: Session = Depends(get_db),
):
    """Return inference settings for a project, including defaults."""
    project = _get_project(project_id, db)
    endpoint_name = resolve_endpoint(project)
    cfg = project.endpoint_config if isinstance(project.endpoint_config, dict) else None
    return {
        "serving_endpoint": project.serving_endpoint,
        "default_serving_endpoint": get_default_endpoint(),
        "resolved_endpoint": endpoint_name,
        "endpoint_config": project.endpoint_config,
        "pre_annotation_enabled": endpoint_name is not None,
        "use_data_plane_resolved": resolve_use_data_plane(cfg),
        "pre_annotate_on_import": _env_truthy("PRE_ANNOTATE_ON_IMPORT"),
        "pre_annotate_on_import_max_samples": os.environ.get(
            "PRE_ANNOTATE_ON_IMPORT_MAX_SAMPLES", "50",
        ),
        "sam31_note": (
            "Use endpoint_config {\"adapter\": \"sam31\"} for route-optimized "
            "serving_endpoints_data_plane.query (OAuth dataplane)."
        ),
        "async_preannotate_job_configured": resolve_preannotate_job_id() is not None,
        "pre_annotate_databricks_job_id": resolve_preannotate_job_id(),
        "workspace_host": _resolve_workspace_host(),
    }


# Project-less routes (e.g. new-project form cannot call /projects/{id}/settings).
defaults_router = APIRouter(prefix="/api", tags=["inference"])


@defaults_router.get("/inference-defaults", response_model=InferenceDefaultsOut)
def get_inference_defaults():
    """Expose ``SERVING_ENDPOINT`` (and similar) for UI pre-fill. No secrets."""
    return InferenceDefaultsOut(default_serving_endpoint=get_default_endpoint())
