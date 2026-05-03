"""Embedding generation for image similarity search.

Supports batched endpoint calls (up to 8 images per request) with
concurrent image pre-fetching from UC Volumes for throughput.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

DEFAULT_EMBEDDING_ENDPOINT = "cv-dinov3-cv_manufacturing"
PREFETCH_WORKERS = 8


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


def _prefetch_images(samples: list) -> dict[int, Optional[bytes]]:
    """Download images concurrently from UC Volumes. Returns {sample.id: bytes|None}."""
    from .volumes import read_image_bytes

    result: dict[int, Optional[bytes]] = {}

    def _read(s):
        return s.id, read_image_bytes(s.filepath)

    with ThreadPoolExecutor(max_workers=PREFETCH_WORKERS) as pool:
        futures = {pool.submit(_read, s): s.id for s in samples}
        for fut in as_completed(futures):
            try:
                sid, data = fut.result()
                result[sid] = data
            except Exception:
                result[futures[fut]] = None

    return result


def run_embedding_generation(
    db: Session,
    project: Any,
    samples: list,
    *,
    force: bool = False,
) -> dict[str, int]:
    """Generate embeddings for samples using batched inference. Caller must commit.

    Returns counters: completed, failed, skipped, total.
    """
    from .inference_adapters import get_embedding_adapter
    from .inference_adapters.dinov3 import BATCH_SIZE

    endpoint_name = resolve_embedding_endpoint(project)
    if not endpoint_name:
        return {"completed": 0, "failed": 0, "skipped": 0, "total": len(samples)}

    adapter = get_embedding_adapter()
    endpoint_config = dict(project.endpoint_config or {})
    completed = failed = skipped = 0

    eligible = []
    for sample in samples:
        if not force and sample.embedding is not None:
            skipped += 1
        else:
            eligible.append(sample)

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

        if not ready_bytes:
            continue

        try:
            embeddings = adapter.batch_query_embedding(
                endpoint_name, ready_bytes, endpoint_config,
            )
        except Exception as e:
            log.warning("Batch embedding failed: %s", e)
            failed += len(ready_samples)
            continue

        for s, emb in zip(ready_samples, embeddings):
            if emb is None:
                failed += 1
            else:
                set_sample_embedding(s, emb, db)
                completed += 1

        db.flush()

    return {
        "completed": completed,
        "failed": failed,
        "skipped": skipped,
        "total": len(samples),
    }
