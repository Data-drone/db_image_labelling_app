"""Submit Databricks Jobs for finetuning after dataset export."""

from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)


def resolve_finetune_job_id() -> Optional[int]:
    raw = os.environ.get("FINETUNE_DATABRICKS_JOB_ID") or os.environ.get("FINETUNE_JOB_ID")
    if not raw or not str(raw).strip():
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        log.warning("Invalid FINETUNE_DATABRICKS_JOB_ID / FINETUNE_JOB_ID: %r", raw)
        return None


def trigger_finetune_job(run_db_id: int, export_path: str) -> int:
    """Call ``jobs.run_now`` to kick off the configured finetuning job.

    The job receives the export path so it knows where the dataset lives.
    Returns the **Databricks run id** (for linking to the Jobs UI).
    """
    job_id = resolve_finetune_job_id()
    if job_id is None:
        raise RuntimeError(
            "Finetuning job is not configured. Set FINETUNE_DATABRICKS_JOB_ID "
            "to the numeric Databricks job id."
        )

    from .volumes import _get_workspace_client

    w = _get_workspace_client()

    job_params = {
        "run_id": str(run_db_id),
        "export_path": export_path,
    }

    resp = w.jobs.run_now(
        job_id=job_id,
        job_parameters=job_params,
    )
    drid = getattr(resp, "run_id", None) or getattr(resp, "run_id_", None)
    if drid is None and isinstance(resp, dict):
        drid = resp.get("run_id")
    if drid is None:
        raise RuntimeError(f"jobs.run_now returned no run_id: {resp!r}")
    log.info("Submitted finetune job_id=%s run_id=%s for db_run_id=%s", job_id, drid, run_db_id)
    return int(drid)
