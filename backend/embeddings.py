"""Embedding generation for image similarity search."""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

DEFAULT_EMBEDDING_ENDPOINT = "cv-dinov3-cv_manufacturing"


def resolve_embedding_endpoint(project: Any) -> Optional[str]:
    """Resolve the embedding endpoint name from project config or env."""
    cfg = project.endpoint_config or {}
    name = cfg.get("embedding_endpoint")
    if name:
        return name
    return os.environ.get("EMBEDDING_ENDPOINT", DEFAULT_EMBEDDING_ENDPOINT)


def _uses_pgvector(db: Session) -> bool:
    """Check if the current DB session is Postgres with a vector column."""
    try:
        dialect = db.bind.dialect.name
    except Exception:
        return False
    return dialect == "postgresql"


def set_sample_embedding(sample: Any, embedding: list[float], db: Session) -> None:
    """Set both JSON and native vector embedding on a sample.

    Always writes the JSON column. Writes the pgvector column on Postgres
    via raw SQL to avoid ORM type mismatch (model declares JSON placeholder,
    but the actual column is ``vector(N)``).
    """
    sample.embedding = embedding
    if _uses_pgvector(db) and sample.id is not None:
        from sqlalchemy import text
        vec_literal = "[" + ",".join(str(float(v)) for v in embedding) + "]"
        db.execute(
            text("UPDATE project_samples SET embedding_vec = :vec WHERE id = :id"),
            {"vec": vec_literal, "id": sample.id},
        )


def run_embedding_generation(
    db: Session,
    project: Any,
    samples: list,
    *,
    force: bool = False,
) -> dict[str, int]:
    """Generate embeddings for samples. Caller must commit.

    Returns counters: completed, failed, skipped, total.
    """
    from .inference_adapters import get_embedding_adapter
    from .volumes import read_image_bytes

    endpoint_name = resolve_embedding_endpoint(project)
    if not endpoint_name:
        return {"completed": 0, "failed": 0, "skipped": 0, "total": len(samples)}

    adapter = get_embedding_adapter()
    endpoint_config = dict(project.endpoint_config or {})
    completed = failed = skipped = 0

    for sample in samples:
        if not force and sample.embedding is not None:
            skipped += 1
            continue

        image_bytes = read_image_bytes(sample.filepath)
        if not image_bytes:
            failed += 1
            continue

        try:
            embedding = adapter.query_embedding(endpoint_name, image_bytes, endpoint_config)
        except Exception as e:
            log.warning("Embedding generation failed for sample %d: %s", sample.id, e)
            failed += 1
            continue

        if embedding is None:
            failed += 1
            continue

        set_sample_embedding(sample, embedding, db)
        completed += 1
        if completed % 50 == 0:
            db.flush()

    return {
        "completed": completed,
        "failed": failed,
        "skipped": skipped,
        "total": len(samples),
    }
