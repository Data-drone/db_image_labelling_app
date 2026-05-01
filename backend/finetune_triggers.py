"""Submit Databricks Jobs for finetuning after dataset export."""

from __future__ import annotations

import logging
from typing import Optional

from .job_utils import resolve_job_id, trigger_databricks_job

log = logging.getLogger(__name__)

_ENV_VARS = ["FINETUNE_DATABRICKS_JOB_ID", "FINETUNE_JOB_ID"]


def resolve_finetune_job_id() -> Optional[int]:
    return resolve_job_id(_ENV_VARS)


def trigger_finetune_job(run_db_id: int, export_path: str) -> int:
    """Kick off the configured finetuning job. Returns the Databricks run id."""
    job_id = resolve_finetune_job_id()
    if job_id is None:
        raise RuntimeError(
            "Finetuning job is not configured. Set FINETUNE_DATABRICKS_JOB_ID "
            "to the numeric Databricks job id."
        )

    job_params = {
        "run_id": str(run_db_id),
        "export_path": export_path,
    }
    drid = trigger_databricks_job(job_id, job_params)
    log.info("Submitted finetune job_id=%s run_id=%s for db_run_id=%s", job_id, drid, run_db_id)
    return drid
