"""
Label Propagation, Near-Duplicate Detection, Diversity Sampling, and Outlier Detection
— powered by DINO embeddings + pgvector.
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
    DiversitySampleOut,
    DiversitySamplingResult,
    LabelPropagateRequest,
    LabelPropagateResult,
    NearDuplicateGroup,
    NearDuplicateResult,
    OutlierDetectionResult,
    OutlierSampleOut,
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

    Only supported for classification projects — bounding box coordinates
    are image-specific and not transferable between images.
    """
    project = db.query(LabelingProject).filter_by(id=project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")

    if project.task_type != "classification":
        raise HTTPException(
            status_code=400,
            detail="Label propagation is only supported for classification projects. "
            "Bounding box coordinates from one image are not transferable to another.",
        )

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


# ---------------------------------------------------------------------------
# Diversity Sampling (Smart Queue)
# ---------------------------------------------------------------------------

def _diversity_pgvector(
    db: Session,
    project_id: int,
    limit: int,
) -> DiversitySamplingResult:
    """Rank unlabeled samples by max cosine distance from any labeled sample (pgvector)."""
    labeled_count = (
        db.query(ProjectSample)
        .filter(
            ProjectSample.project_id == project_id,
            ProjectSample.status == "labeled",
            ProjectSample.embedding.isnot(None),
        )
        .count()
    )
    if labeled_count == 0:
        # No labeled samples — fall back to random unlabeled with embeddings
        unlabeled = (
            db.query(ProjectSample.id, ProjectSample.filename, ProjectSample.status)
            .filter(
                ProjectSample.project_id == project_id,
                ProjectSample.status.in_(["unlabeled", "pre_labeled"]),
                ProjectSample.embedding.isnot(None),
            )
            .order_by(ProjectSample.id)
            .limit(limit)
            .all()
        )
        items = [
            DiversitySampleOut(sample_id=s.id, filename=s.filename, diversity_score=1.0, status=s.status)
            for s in unlabeled
        ]
        return DiversitySamplingResult(items=items, total=len(items))

    # For each unlabeled sample, compute min cosine similarity to any labeled sample.
    # Diversity score = 1 - min_similarity (i.e. max distance).
    # We use a lateral join / subquery approach via raw SQL for efficiency.
    rows = db.execute(
        text(
            "WITH labeled AS ( "
            "  SELECT id, embedding_vec FROM project_samples "
            "  WHERE project_id = :pid AND status = 'labeled' AND embedding_vec IS NOT NULL "
            "), "
            "unlabeled AS ( "
            "  SELECT id, filename, status, embedding_vec FROM project_samples "
            "  WHERE project_id = :pid AND status IN ('unlabeled', 'pre_labeled') "
            "    AND embedding_vec IS NOT NULL "
            ") "
            "SELECT u.id, u.filename, u.status, "
            "  (SELECT MIN(u.embedding_vec <=> l.embedding_vec) FROM labeled l) AS min_distance "
            "FROM unlabeled u "
            "ORDER BY min_distance DESC "
            "LIMIT :lim"
        ),
        {"pid": project_id, "lim": limit},
    ).fetchall()

    items = [
        DiversitySampleOut(
            sample_id=r.id,
            filename=r.filename,
            diversity_score=round(float(r.min_distance), 4),
            status=r.status,
        )
        for r in rows
    ]
    total = (
        db.query(ProjectSample)
        .filter(
            ProjectSample.project_id == project_id,
            ProjectSample.status.in_(["unlabeled", "pre_labeled"]),
            ProjectSample.embedding.isnot(None),
        )
        .count()
    )
    return DiversitySamplingResult(items=items, total=total)


def _diversity_python(
    db: Session,
    project_id: int,
    limit: int,
) -> DiversitySamplingResult:
    """Fallback: compute diversity scores in Python."""
    all_samples = (
        db.query(ProjectSample.id, ProjectSample.filename, ProjectSample.status, ProjectSample.embedding)
        .filter(
            ProjectSample.project_id == project_id,
            ProjectSample.embedding.isnot(None),
        )
        .all()
    )

    labeled = [(s.id, s.embedding) for s in all_samples if s.status == "labeled"]
    unlabeled = [(s.id, s.filename, s.status, s.embedding) for s in all_samples if s.status in ("unlabeled", "pre_labeled")]

    if not unlabeled:
        return DiversitySamplingResult(items=[], total=0)

    if not labeled:
        items = [
            DiversitySampleOut(sample_id=s[0], filename=s[1], diversity_score=1.0, status=s[2])
            for s in unlabeled[:limit]
        ]
        return DiversitySamplingResult(items=items, total=len(unlabeled))

    # Precompute labeled norms
    labeled_data = []
    for sid, emb in labeled:
        norm = sum(x * x for x in emb) ** 0.5
        labeled_data.append((emb, norm))

    scored = []
    for uid, fname, status, uemb in unlabeled:
        unorm = sum(x * x for x in uemb) ** 0.5
        if unorm == 0:
            scored.append((0.0, uid, fname, status))
            continue
        # max distance = 1 - max_similarity = 1 - max(cos_sim)
        max_sim = -1.0
        for lemb, lnorm in labeled_data:
            if lnorm == 0:
                continue
            dot = sum(a * b for a, b in zip(uemb, lemb))
            sim = dot / (unorm * lnorm)
            if sim > max_sim:
                max_sim = sim
        # diversity = distance from closest labeled sample
        diversity = 1.0 - max_sim if max_sim > -1.0 else 1.0
        scored.append((diversity, uid, fname, status))

    scored.sort(reverse=True)
    items = [
        DiversitySampleOut(sample_id=sid, filename=fn, diversity_score=round(score, 4), status=st)
        for score, sid, fn, st in scored[:limit]
    ]
    return DiversitySamplingResult(items=items, total=len(unlabeled))


@router.get("/diversity-queue", response_model=DiversitySamplingResult)
def diversity_queue(
    project_id: int,
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Rank unlabeled samples by diversity — most visually different from labeled set first.

    Each sample is scored by its maximum cosine distance to any labeled sample.
    Higher score = more different from what's already been labeled = higher priority.
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
            return _diversity_pgvector(db, project_id, limit)
        except Exception as exc:
            log.warning("pgvector diversity sampling failed, falling back to Python: %s", exc)

    return _diversity_python(db, project_id, limit)


# ---------------------------------------------------------------------------
# Outlier Detection
# ---------------------------------------------------------------------------

def _outlier_pgvector(
    db: Session,
    project_id: int,
    k: int,
    percentile: float,
) -> OutlierDetectionResult:
    """Score each sample by avg distance to K nearest neighbors (pgvector)."""
    # Use LATERAL join to compute avg distance to K nearest neighbors
    rows = db.execute(
        text(
            "SELECT s.id, s.filename, s.status, knn.avg_dist AS avg_knn_distance "
            "FROM project_samples s "
            "CROSS JOIN LATERAL ( "
            "  SELECT AVG(s.embedding_vec <=> n.embedding_vec) AS avg_dist "
            "  FROM project_samples n "
            "  WHERE n.project_id = :pid AND n.embedding_vec IS NOT NULL AND n.id != s.id "
            "  ORDER BY s.embedding_vec <=> n.embedding_vec "
            "  LIMIT :k "
            ") knn "
            "WHERE s.project_id = :pid AND s.embedding_vec IS NOT NULL "
            "  AND knn.avg_dist IS NOT NULL "
            "ORDER BY knn.avg_dist DESC"
        ),
        {"pid": project_id, "k": k},
    ).fetchall()

    if not rows:
        return OutlierDetectionResult(items=[], total=0, threshold=0.0, outlier_count=0)

    scores = [float(r.avg_knn_distance) for r in rows]
    # Threshold at given percentile
    sorted_scores = sorted(scores)
    threshold_idx = int(len(sorted_scores) * percentile)
    threshold_idx = min(threshold_idx, len(sorted_scores) - 1)
    threshold_val = sorted_scores[threshold_idx]

    items = []
    outlier_count = 0
    for r in rows:
        is_outlier = float(r.avg_knn_distance) >= threshold_val
        if is_outlier:
            outlier_count += 1
        items.append(OutlierSampleOut(
            sample_id=r.id,
            filename=r.filename,
            outlier_score=round(float(r.avg_knn_distance), 4),
            is_outlier=is_outlier,
            status=r.status,
        ))

    return OutlierDetectionResult(
        items=items,
        total=len(items),
        threshold=round(threshold_val, 4),
        outlier_count=outlier_count,
    )


def _outlier_python(
    db: Session,
    project_id: int,
    k: int,
    percentile: float,
) -> OutlierDetectionResult:
    """Fallback: compute KNN outlier scores in Python."""
    samples = (
        db.query(ProjectSample.id, ProjectSample.filename, ProjectSample.status, ProjectSample.embedding)
        .filter(
            ProjectSample.project_id == project_id,
            ProjectSample.embedding.isnot(None),
        )
        .all()
    )

    if not samples:
        return OutlierDetectionResult(items=[], total=0, threshold=0.0, outlier_count=0)

    # Precompute norms
    data = []
    for s in samples:
        norm = sum(x * x for x in s.embedding) ** 0.5
        data.append((s.id, s.filename, s.status, s.embedding, norm))

    scored = []
    for i, (sid, fname, status, emb, norm) in enumerate(data):
        if norm == 0:
            scored.append((sid, fname, status, 2.0))
            continue
        # Compute cosine distance to all others
        distances = []
        for j, (_, _, _, oemb, onorm) in enumerate(data):
            if i == j or onorm == 0:
                continue
            dot = sum(a * b for a, b in zip(emb, oemb))
            cos_dist = 1.0 - (dot / (norm * onorm))
            distances.append(cos_dist)
        distances.sort()
        knn_distances = distances[:k]
        avg_dist = sum(knn_distances) / len(knn_distances) if knn_distances else 0.0
        scored.append((sid, fname, status, avg_dist))

    # Sort by score descending
    scored.sort(key=lambda x: x[3], reverse=True)

    # Compute threshold
    all_scores = sorted(x[3] for x in scored)
    threshold_idx = int(len(all_scores) * percentile)
    threshold_idx = min(threshold_idx, len(all_scores) - 1)
    threshold_val = all_scores[threshold_idx]

    items = []
    outlier_count = 0
    for sid, fname, status, score in scored:
        is_outlier = score >= threshold_val
        if is_outlier:
            outlier_count += 1
        items.append(OutlierSampleOut(
            sample_id=sid,
            filename=fname,
            outlier_score=round(score, 4),
            is_outlier=is_outlier,
            status=status,
        ))

    return OutlierDetectionResult(
        items=items,
        total=len(items),
        threshold=round(threshold_val, 4),
        outlier_count=outlier_count,
    )


@router.get("/outliers", response_model=OutlierDetectionResult)
def detect_outliers(
    project_id: int,
    k: int = Query(5, ge=1, le=50),
    percentile: float = Query(0.95, ge=0.5, le=0.99),
    db: Session = Depends(get_db),
):
    """Detect outlier samples by average distance to K nearest neighbors.

    Samples with unusually high average KNN distance are flagged as outliers.
    The threshold is set at the given percentile of all scores.
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
            return _outlier_pgvector(db, project_id, k, percentile)
        except Exception as exc:
            log.warning("pgvector outlier detection failed, falling back to Python: %s", exc)

    return _outlier_python(db, project_id, k, percentile)
