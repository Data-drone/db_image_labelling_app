"""Submit Databricks Jobs for async pre-annotation."""

from __future__ import annotations

import logging
import os
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

    # Pin the job to the *currently active* Lakebase connection. Without this,
    # the job falls back to its bundle-deploy-time defaults, so an Admin
    # switching the app to a different Lakebase project/branch leaves queued
    # jobs reading/writing the original connection instead.
    from . import deps

    if deps.is_lakebase():
        from .lakebase import current_connection_params

        conn = current_connection_params()
        if conn.get("project_id"):
            job_params["lakebase_project_id"] = conn["project_id"]
        if conn.get("endpoint"):
            job_params["lakebase_endpoint"] = conn["endpoint"]
        if conn.get("database"):
            job_params["lakebase_database_name"] = conn["database"]
        if conn.get("schema"):
            job_params["lakebase_schema_name"] = conn["schema"]

    sp_id = os.environ.get("SP_SERVING_CLIENT_ID") or os.environ.get("DATABRICKS_CLIENT_ID", "")
    sp_secret = os.environ.get("SP_SERVING_CLIENT_SECRET") or os.environ.get("DATABRICKS_CLIENT_SECRET", "")
    if sp_id and sp_secret:
        job_params["sp_client_id"] = sp_id
        job_params["sp_client_secret"] = sp_secret
    else:
        log.warning(
            "No SP OAuth credentials available (SP_SERVING_CLIENT_ID or DATABRICKS_CLIENT_ID); "
            "the pre-annotate job may fail on route-optimized endpoints."
        )

    drid = trigger_databricks_job(job_id, job_params)
    log.info("Submitted pre-annotate job_id=%s run_id=%s for db_run_id=%s", job_id, drid, run_db_id)
    return drid
