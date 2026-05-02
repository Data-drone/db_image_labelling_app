"""
Databricks Job worker: batch pre-annotation for a ``PreannotateRun`` row.

Run from ``scripts/preannotate_job.py`` with the numeric DB primary key of
``preannotate_runs`` as argv[1]. Uses the same Lakebase / SQLite env as the app.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone

log = logging.getLogger(__name__)

BATCH_SIZE = int(os.environ.get("PRE_ANNOTATE_JOB_BATCH_SIZE", "50"))


def _init_db_backend():
    """Initialize the DB engine/tables and return a session factory callable.

    Returning a factory (instead of a single session) lets the worker obtain
    fresh sessions per batch chunk, surviving Lakebase SSL/token drops.
    """
    from ..models import Base, ensure_annotations_is_draft_column, ensure_preannotate_runs_table

    use_lakebase = os.environ.get("USE_LAKEBASE", "true").lower() != "false"
    if use_lakebase:
        from ..lakebase import (
            get_engine,
            get_session,
            init_lakebase,
            init_lakebase_from_app_resource,
            uses_app_resource_postgres,
        )

        if uses_app_resource_postgres():
            init_lakebase_from_app_resource()
        else:
            init_lakebase()
        engine = get_engine()
        Base.metadata.create_all(engine)
        ensure_annotations_is_draft_column(engine)
        ensure_preannotate_runs_table(engine)
        return get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    url = os.environ.get("DATABASE_URL", "sqlite:////tmp/cv_explorer.db")
    engine = create_engine(url, echo=False)
    Base.metadata.create_all(engine)
    ensure_annotations_is_draft_column(engine)
    ensure_preannotate_runs_table(engine)
    factory = sessionmaker(bind=engine)
    return factory


def main(run_id: int) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )

    from sqlalchemy import or_
    from ..models import LabelingProject, PreannotateRun, ProjectSample
    from ..preannotate import run_preannotate_for_samples

    new_session = _init_db_backend()

    db = new_session()
    try:
        run_row = db.query(PreannotateRun).filter_by(id=run_id).first()
        if not run_row:
            log.error("PreannotateRun id=%s not found", run_id)
            sys.exit(1)

        if run_row.status in ("succeeded", "failed", "cancelled"):
            log.info("Run %s already terminal (%s); exiting.", run_id, run_row.status)
            return

        run_row.status = "running"
        run_row.started_at = datetime.now(timezone.utc)
        db.commit()

        project_id = run_row.project_id
        include_pre_labeled = run_row.include_pre_labeled
        max_samples = run_row.max_samples
        min_confidence = run_row.min_confidence
        text_prompt = run_row.text_prompt
    finally:
        db.close()

    db = new_session()
    try:
        project = db.query(LabelingProject).filter_by(id=project_id).first()
        if not project:
            run_row = db.query(PreannotateRun).filter_by(id=run_id).first()
            run_row.status = "failed"
            run_row.error_message = "Project not found."
            run_row.finished_at = datetime.now(timezone.utc)
            db.commit()
            sys.exit(1)

        if include_pre_labeled:
            base = db.query(ProjectSample).filter(
                ProjectSample.project_id == project_id,
                or_(
                    ProjectSample.status == "unlabeled",
                    ProjectSample.status == "pre_labeled",
                ),
            )
        else:
            base = db.query(ProjectSample).filter_by(
                project_id=project_id,
                status="unlabeled",
            )

        base = base.order_by(ProjectSample.id)
        if max_samples and max_samples > 0:
            base = base.limit(max_samples)

        sample_ids = [s.id for s in base.all()]
    finally:
        db.close()

    total_planned = len(sample_ids)
    db = new_session()
    try:
        run_row = db.query(PreannotateRun).filter_by(id=run_id).first()
        run_row.total_planned = total_planned
        db.commit()
    finally:
        db.close()

    completed = failed = skipped = 0
    last_error = ""
    for batch_start in range(0, total_planned, BATCH_SIZE):
        batch_ids = sample_ids[batch_start : batch_start + BATCH_SIZE]
        db = new_session()
        try:
            project = db.query(LabelingProject).filter_by(id=project_id).first()
            chunk = (
                db.query(ProjectSample)
                .filter(ProjectSample.id.in_(batch_ids))
                .order_by(ProjectSample.id)
                .all()
            )
            if not chunk:
                break
            stats = run_preannotate_for_samples(
                db,
                project,
                chunk,
                min_confidence=min_confidence,
                text_prompt=text_prompt,
            )
            db.commit()
            completed += stats["completed"]
            failed += stats["failed"]
            skipped += stats["skipped"]
        except Exception as e:
            log.exception("Batch starting at offset %d failed", batch_start)
            failed += len(batch_ids)
            last_error = str(e)
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            db.close()

        db = new_session()
        try:
            run_row = db.query(PreannotateRun).filter_by(id=run_id).first()
            run_row.completed = completed
            run_row.failed = failed
            run_row.skipped = skipped
            db.commit()
        except Exception:
            log.warning("Could not update progress counters", exc_info=True)
        finally:
            db.close()

    db = new_session()
    try:
        run_row = db.query(PreannotateRun).filter_by(id=run_id).first()
        if completed == 0 and failed > 0:
            run_row.status = "failed"
            run_row.error_message = (
                f"All {failed} sample(s) failed. Last error: {last_error[:500]}"
            ) if last_error else (
                f"All {failed} sample(s) failed inference. "
                "Check endpoint configuration and data-plane OAuth credentials."
            )
        elif failed > 0 and completed > 0:
            run_row.status = "succeeded"
            run_row.error_message = f"{failed}/{failed + completed + skipped} samples failed."
        else:
            run_row.status = "succeeded"
        run_row.completed = completed
        run_row.failed = failed
        run_row.skipped = skipped
        run_row.finished_at = datetime.now(timezone.utc)
        db.commit()
        log.info(
            "Preannotate run %s done: status=%s completed=%s failed=%s skipped=%s total=%s",
            run_id, run_row.status, completed, failed, skipped, total_planned,
        )
    except Exception as e:
        log.exception("Failed to write final status")
        try:
            db.rollback()
            run_row = db.query(PreannotateRun).filter_by(id=run_id).first()
            if run_row:
                run_row.status = "failed"
                run_row.error_message = str(e)[:4000]
                run_row.finished_at = datetime.now(timezone.utc)
                db.commit()
        except Exception:
            pass
        sys.exit(1)
    finally:
        db.close()
