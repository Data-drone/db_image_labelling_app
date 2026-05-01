"""Shared helpers for triggering Databricks Jobs and syncing run status."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


def resolve_job_id(env_var_names: list[str]) -> Optional[int]:
    for name in env_var_names:
        raw = os.environ.get(name)
        if raw and str(raw).strip():
            try:
                return int(str(raw).strip())
            except ValueError:
                log.warning("Invalid %s: %r", name, raw)
    return None


def trigger_databricks_job(job_id: int, job_params: dict) -> int:
    """Call jobs.run_now and return the Databricks run id."""
    from .volumes import _get_workspace_client

    w = _get_workspace_client()
    resp = w.jobs.run_now(
        job_id=job_id,
        job_parameters=job_params,
    )
    drid = getattr(resp, "run_id", None) or getattr(resp, "run_id_", None)
    if drid is None and isinstance(resp, dict):
        drid = resp.get("run_id")
    if drid is None:
        raise RuntimeError(f"jobs.run_now returned no run_id: {resp!r}")
    return int(drid)


def sync_run_status(row, db: Session) -> None:
    """If a run row is non-terminal but the Databricks run has finished, update it."""
    if row.status in _TERMINAL_STATUSES or not row.databricks_run_id:
        return
    try:
        from .volumes import _get_workspace_client
        w = _get_workspace_client()
        run = w.jobs.get_run(run_id=row.databricks_run_id)
        state = run.state
        if not state:
            return
        lcs = str(getattr(state, "life_cycle_state", "") or "").upper()
        result = str(getattr(state, "result_state", "") or "").upper()
        msg = str(getattr(state, "state_message", "") or "")

        log.info(
            "Cross-check run %s (db_run=%s): life_cycle=%s result=%s msg=%.120s",
            row.id, row.databricks_run_id, lcs, result, msg,
        )

        if "RUNNING" in lcs and row.status != "running":
            row.status = "running"
            row.started_at = row.started_at or datetime.now(timezone.utc)
            db.commit()
        elif "FAILED" in result or "INTERNAL_ERROR" in lcs or "SKIPPED" in lcs or "BLOCKED" in lcs:
            row.status = "failed"
            row.error_message = (msg or f"Databricks run {lcs}/{result}")[:4000]
            row.finished_at = datetime.now(timezone.utc)
            db.commit()
        elif "CANCEL" in result:
            row.status = "cancelled"
            row.finished_at = datetime.now(timezone.utc)
            db.commit()
        elif "SUCCESS" in result:
            row.status = "succeeded"
            row.finished_at = datetime.now(timezone.utc)
            db.commit()
    except Exception:
        log.warning("Could not cross-check Databricks run %s", row.databricks_run_id, exc_info=True)


def get_project_or_404(project_id: int, db: Session, model_class):
    p = db.query(model_class).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")
    return p
