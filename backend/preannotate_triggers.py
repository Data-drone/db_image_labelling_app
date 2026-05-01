"""Submit Databricks Jobs for async pre-annotation."""

from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)


def resolve_preannotate_job_id() -> Optional[int]:
    raw = os.environ.get("PRE_ANNOTATE_DATABRICKS_JOB_ID") or os.environ.get("PRE_ANNOTATE_JOB_ID")
    if not raw or not str(raw).strip():
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        log.warning("Invalid PRE_ANNOTATE_DATABRICKS_JOB_ID / PRE_ANNOTATE_JOB_ID: %r", raw)
        return None


def trigger_preannotate_job(run_db_id: int) -> int:
    """Call ``jobs.run_now`` so the cluster worker runs ``scripts/preannotate_job.py``.

    Returns the **Databricks run id** (for linking to the Jobs UI).

    Raises:
        RuntimeError: if job id is not configured or the SDK call fails.
    """
    job_id = resolve_preannotate_job_id()
    if job_id is None:
        raise RuntimeError(
            "Async pre-annotate is not configured. Set PRE_ANNOTATE_DATABRICKS_JOB_ID "
            "to the numeric Databricks job id (from the bundle-deployed pre-annotate job)."
        )

    from .volumes import _get_workspace_client

    w = _get_workspace_client()

    # databricks-sdk: pass job_parameters as a dict (run_id matches job parameter name).
    resp = w.jobs.run_now(
        job_id=job_id,
        job_parameters={"run_id": str(run_db_id)},
    )
    drid = getattr(resp, "run_id", None) or getattr(resp, "run_id_", None)
    if drid is None and isinstance(resp, dict):
        drid = resp.get("run_id")
    if drid is None:
        raise RuntimeError(f"jobs.run_now returned no run_id: {resp!r}")
    log.info("Submitted pre-annotate job_id=%s run_id=%s for db_run_id=%s", job_id, drid, run_db_id)
    return int(drid)
