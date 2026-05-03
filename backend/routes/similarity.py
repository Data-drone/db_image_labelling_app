"""
Label Propagation and Near-Duplicate Detection — powered by DINO embeddings + pgvector.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..deps import get_db, get_user_email
from ..models import Annotation, LabelingProject, ProjectSample
from ..preannotate import refresh_sample_status_after_annotation_change
from ..schemas import (
    LabelPropagateRequest,
    LabelPropagateResult,
    NearDuplicateGroup,
    NearDuplicateResult,
    SimilarSampleOut,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["similarity"])


def _is_pgvector(db: Session) -> bool:
    try:
        return db.bind.dialect.name == "postgresql"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Label Propagation
# ---------------------------------------------------------------------------

def _propagate_pgvector(
    db: Session,
    project: LabelingProject,
    threshold: float,
    max_targets: int,
    source_statuses: list[str],
    user_email: str,
) -> LabelPropagateResult:
    """Use pgvector to find nearest labeled neighbor for each unlabeled sample."""
    target_samples = (
        db.query(ProjectSample)
        .filter(
            ProjectSample.project_id == project.id,
            ProjectSample.status == "unlabeled",
            ProjectSample.embedding.isnot(None),
        )
        .order_by(ProjectSample.id)
        .all()
    )
    if not target_samples:
        return LabelPropagateResult(total_candidates=0)

    if max_targets > 0:
        target_samples = target_samples[:max_targets]

    status_placeholders = ", ".join(f":s{i}" for i in range(len(source_statuses)))
    status_params = {f"s{i}": s for i, s in enumerate(source_statuses)}

    propagated = 0
    skipped = 0

    for target in target_samples:
        if target.embedding is None:
            skipped += 1
            continue

        query_vec = "[" + ",".join(str(float(v)) for v in target.embedding) + "]"

        row = db.execute(
            text(
                f"SELECT id, 1 - (embedding_vec <=> :q) AS similarity "
                f"FROM project_samples "
                f"WHERE project_id = :pid "
                f"  AND embedding_vec IS NOT NULL "
                f"  AND id != :sid "
                f"  AND status IN ({status_placeholders}) "
                f"ORDER BY embedding_vec <=> :q "
                f"LIMIT 1"
            ),
            {"q": query_vec, "pid": project.id, "sid": target.id, **status_params},
        ).fetchone()

        if row is None or row.similarity < threshold:
            skipped += 1
            continue

        source_annotations = (
            db.query(Annotation)
            .filter_by(sample_id=row.id, project_id=project.id)
            .filter(Annotation.is_draft.is_(False))
            .all()
        )
        if not source_annotations:
            skipped += 1
            continue

        # Clear existing model drafts on this target
        (
            db.query(Annotation)
            .filter(
                Annotation.project_id == project.id,
                Annotation.sample_id == target.id,
                Annotation.is_draft.is_(True),
                Annotation.created_by.like("propagate:%"),
            )
            .delete(synchronize_session=False)
        )

        for ann in source_annotations:
            db.add(
                Annotation(
                    sample_id=target.id,
                    project_id=project.id,
                    label=ann.label,
                    ann_type=ann.ann_type,
                    bbox_json=ann.bbox_json,
                    is_draft=True,
                    created_by=f"propagate:{user_email}",
                )
            )

        target.status = "pre_labeled"
        propagated += 1

        if propagated % 100 == 0:
            db.flush()

    return LabelPropagateResult(
        propagated=propagated,
        skipped=skipped,
        total_candidates=len(target_samples),
    )


def _propagate_python(
    db: Session,
    project: LabelingProject,
    threshold: float,
    max_targets: int,
    source_statuses: list[str],
    user_email: str,
) -> LabelPropagateResult:
    """Fallback: load all embeddings in Python and compute cosine similarity."""
    all_samples = (
        db.query(
            ProjectSample.id,
            ProjectSample.status,
            ProjectSample.embedding,
        )
        .filter(
            ProjectSample.project_id == project.id,
            ProjectSample.embedding.isnot(None),
        )
        .all()
    )

    sources = [(s.id, s.embedding) for s in all_samples if s.status in source_statuses]
    targets = [(s.id, s.embedding) for s in all_samples if s.status == "unlabeled"]

    if not sources or not targets:
        return LabelPropagateResult(total_candidates=len(targets))

    if max_targets > 0:
        targets = targets[:max_targets]

    source_norms = []
    for sid, emb in sources:
        norm = sum(x * x for x in emb) ** 0.5
        source_norms.append((sid, emb, norm))

    propagated = 0
    skipped = 0

    for tid, temb in targets:
        tnorm = sum(x * x for x in temb) ** 0.5
        if tnorm == 0:
            skipped += 1
            continue

        best_sim = -1.0
        best_sid = None
        for sid, semb, snorm in source_norms:
            if snorm == 0:
                continue
            dot = sum(a * b for a, b in zip(temb, semb))
            sim = dot / (tnorm * snorm)
            if sim > best_sim:
                best_sim = sim
                best_sid = sid

        if best_sim < threshold or best_sid is None:
            skipped += 1
            continue

        source_annotations = (
            db.query(Annotation)
            .filter_by(sample_id=best_sid, project_id=project.id)
            .filter(Annotation.is_draft.is_(False))
            .all()
        )
        if not source_annotations:
            skipped += 1
            continue

        target_sample = db.query(ProjectSample).filter_by(id=tid).first()

        (
            db.query(Annotation)
            .filter(
                Annotation.project_id == project.id,
                Annotation.sample_id == tid,
                Annotation.is_draft.is_(True),
                Annotation.created_by.like("propagate:%"),
            )
            .delete(synchronize_session=False)
        )

        for ann in source_annotations:
            db.add(
                Annotation(
                    sample_id=tid,
                    project_id=project.id,
                    label=ann.label,
                    ann_type=ann.ann_type,
                    bbox_json=ann.bbox_json,
                    is_draft=True,
                    created_by=f"propagate:{user_email}",
                )
            )

        if target_sample:
            target_sample.status = "pre_labeled"
        propagated += 1

    return LabelPropagateResult(
        propagated=propagated,
        skipped=skipped,
        total_candidates=len(targets),
    )


@router.post("/propagate-labels", response_model=LabelPropagateResult)
def propagate_labels(
    project_id: int,
    payload: LabelPropagateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Propagate labels from labeled samples to similar unlabeled ones as drafts.

    For each unlabeled sample with an embedding, finds the closest labeled
    neighbor. If similarity >= threshold, copies the neighbor's annotations
    as draft annotations (``is_draft=True``, ``created_by=propagate:<user>``).
    """
    project = db.query(LabelingProject).filter_by(id=project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")

    embedded_count = (
        db.query(ProjectSample)
        .filter(
            ProjectSample.project_id == project_id,
            ProjectSample.embedding.isnot(None),
        )
        .count()
    )
    if embedded_count == 0:
        raise HTTPException(status_code=400, detail="No embeddings found. Generate embeddings first.")

    user_email = get_user_email(request)

    if _is_pgvector(db):
        try:
            result = _propagate_pgvector(
                db, project, payload.similarity_threshold,
                payload.max_targets, payload.source_statuses, user_email,
            )
            db.commit()
            return result
        except Exception as exc:
            db.rollback()
            log.warning("pgvector propagation failed, falling back to Python: %s", exc)

    result = _propagate_python(
        db, project, payload.similarity_threshold,
        payload.max_targets, payload.source_statuses, user_email,
    )
    db.commit()
    return result


# ---------------------------------------------------------------------------
# Near-Duplicate Detection
# ---------------------------------------------------------------------------

def _detect_duplicates_pgvector(
    db: Session,
    project_id: int,
    threshold: float,
    limit: int,
) -> NearDuplicateResult:
    """Find near-duplicate groups using pgvector pairwise search."""
    samples = (
        db.query(ProjectSample.id, ProjectSample.filename, ProjectSample.status, ProjectSample.embedding)
        .filter(
            ProjectSample.project_id == project_id,
            ProjectSample.embedding.isnot(None),
        )
        .order_by(ProjectSample.id)
        .all()
    )

    if not samples:
        return NearDuplicateResult(groups=[], threshold=threshold)

    seen: set[int] = set()
    groups: list[NearDuplicateGroup] = []

    for sample in samples:
        if sample.id in seen:
            continue

        query_vec = "[" + ",".join(str(float(v)) for v in sample.embedding) + "]"

        rows = db.execute(
            text(
                "SELECT id, filename, status, 1 - (embedding_vec <=> :q) AS similarity "
                "FROM project_samples "
                "WHERE project_id = :pid AND embedding_vec IS NOT NULL AND id != :sid "
                "  AND 1 - (embedding_vec <=> :q) >= :thresh "
                "ORDER BY embedding_vec <=> :q "
                "LIMIT 50"
            ),
            {"q": query_vec, "pid": project_id, "sid": sample.id, "thresh": threshold},
        ).fetchall()

        # Only include rows not already claimed by another group
        members = []
        for r in rows:
            if r.id not in seen:
                members.append(SimilarSampleOut(
                    sample_id=r.id,
                    filename=r.filename,
                    similarity=round(r.similarity, 4),
                    status=r.status,
                ))
                seen.add(r.id)

        if members:
            seen.add(sample.id)
            groups.append(NearDuplicateGroup(
                representative_id=sample.id,
                representative_filename=sample.filename,
                members=members,
            ))

            if limit > 0 and len(groups) >= limit:
                break

    total_dups = sum(len(g.members) for g in groups)
    return NearDuplicateResult(groups=groups, total_duplicates=total_dups, threshold=threshold)


def _detect_duplicates_python(
    db: Session,
    project_id: int,
    threshold: float,
    limit: int,
) -> NearDuplicateResult:
    """Fallback: all-pairs cosine similarity in Python."""
    samples = (
        db.query(ProjectSample.id, ProjectSample.filename, ProjectSample.status, ProjectSample.embedding)
        .filter(
            ProjectSample.project_id == project_id,
            ProjectSample.embedding.isnot(None),
        )
        .order_by(ProjectSample.id)
        .all()
    )

    if not samples:
        return NearDuplicateResult(groups=[], threshold=threshold)

    embs = []
    for s in samples:
        norm = sum(x * x for x in s.embedding) ** 0.5
        embs.append((s.id, s.filename, s.status, s.embedding, norm))

    seen: set[int] = set()
    groups: list[NearDuplicateGroup] = []

    for i, (sid, fname, status, emb, norm) in enumerate(embs):
        if sid in seen or norm == 0:
            continue

        members = []
        for j in range(i + 1, len(embs)):
            oid, ofn, ost, oemb, onorm = embs[j]
            if oid in seen or onorm == 0:
                continue
            dot = sum(a * b for a, b in zip(emb, oemb))
            sim = dot / (norm * onorm)
            if sim >= threshold:
                members.append(SimilarSampleOut(
                    sample_id=oid, filename=ofn, similarity=round(sim, 4), status=ost,
                ))
                seen.add(oid)

        if members:
            members.sort(key=lambda m: m.similarity, reverse=True)
            seen.add(sid)
            groups.append(NearDuplicateGroup(
                representative_id=sid,
                representative_filename=fname,
                members=members,
            ))

            if limit > 0 and len(groups) >= limit:
                break

    total_dups = sum(len(g.members) for g in groups)
    return NearDuplicateResult(groups=groups, total_duplicates=total_dups, threshold=threshold)


@router.get("/near-duplicates", response_model=NearDuplicateResult)
def detect_near_duplicates(
    project_id: int,
    threshold: float = Query(0.95, ge=0.5, le=1.0),
    limit: int = Query(50, ge=0, le=500),
    db: Session = Depends(get_db),
):
    """Detect near-duplicate image groups using embedding cosine similarity.

    Groups samples where pairwise similarity >= ``threshold``.
    Each group has a representative and a list of near-duplicate members.
    Uses pgvector HNSW index when available, falls back to Python.
    """
    project = db.query(LabelingProject).filter_by(id=project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")

    embedded_count = (
        db.query(ProjectSample)
        .filter(
            ProjectSample.project_id == project_id,
            ProjectSample.embedding.isnot(None),
        )
        .count()
    )
    if embedded_count == 0:
        raise HTTPException(status_code=400, detail="No embeddings found. Generate embeddings first.")

    if _is_pgvector(db):
        try:
            return _detect_duplicates_pgvector(db, project_id, threshold, limit)
        except Exception as exc:
            log.warning("pgvector duplicate detection failed, falling back to Python: %s", exc)

    return _detect_duplicates_python(db, project_id, threshold, limit)
