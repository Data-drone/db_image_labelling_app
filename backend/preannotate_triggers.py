"""Submit Databricks Jobs for async pre-annotation."""

from __future__ import annotations

import logging
from typing import Optional

from .job_utils import resolve_job_id, trigger_databricks_job

log = logging.getLogger(__name__)

_ENV_VARS = ["PRE_ANNOTATE_DATABRICKS_JOB_ID", "PRE_ANNOTATE_JOB_ID"]


def resolve_preannotate_job_id() -> Optional[int]:
    return resolve_job_id(_ENV_VARS)


def trigger_preannotate_job(run_db_id: int) -> int:
    """Kick off the bundle-deployed pre-annotate job. Returns the Databricks run id."""
    job_id = resolve_preannotate_job_id()
    if job_id is None:
        raise RuntimeError(
            "Async pre-annotate is not configured. Set PRE_ANNOTATE_DATABRICKS_JOB_ID "
            "to the numeric Databricks job id (from the bundle-deployed pre-annotate job)."
        )

    job_params = {"run_id": str(run_db_id)}
    drid = trigger_databricks_job(job_id, job_params)
    log.info("Submitted pre-annotate job_id=%s run_id=%s for db_run_id=%s", job_id, drid, run_db_id)
    return drid
