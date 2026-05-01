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


def _configure_db():
    """Return a SQLAlchemy session (caller must close)."""
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
        return get_session()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    url = os.environ.get("DATABASE_URL", "sqlite:////tmp/cv_explorer.db")
    engine = create_engine(url, echo=False)
    Base.metadata.create_all(engine)
    ensure_annotations_is_draft_column(engine)
    ensure_preannotate_runs_table(engine)
    return sessionmaker(bind=engine)()


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

    db = _configure_db()
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

        project = db.query(LabelingProject).filter_by(id=run_row.project_id).first()
        if not project:
            run_row.status = "failed"
            run_row.error_message = "Project not found."
            run_row.finished_at = datetime.now(timezone.utc)
            db.commit()
            sys.exit(1)

        if run_row.include_pre_labeled:
            base = db.query(ProjectSample).filter(
                ProjectSample.project_id == run_row.project_id,
                or_(
                    ProjectSample.status == "unlabeled",
                    ProjectSample.status == "pre_labeled",
                ),
            )
        else:
            base = db.query(ProjectSample).filter_by(
                project_id=run_row.project_id,
                status="unlabeled",
            )

        base = base.order_by(ProjectSample.id)
        if run_row.max_samples and run_row.max_samples > 0:
            base = base.limit(run_row.max_samples)

        total_planned = base.count()
        run_row.total_planned = total_planned
        db.commit()

        completed = failed = skipped = 0
        offset = 0
        while offset < total_planned:
            chunk = base.offset(offset).limit(BATCH_SIZE).all()
            if not chunk:
                break
            stats = run_preannotate_for_samples(
                db,
                project,
                chunk,
                min_confidence=run_row.min_confidence,
                text_prompt=run_row.text_prompt,
            )
            completed += stats["completed"]
            failed += stats["failed"]
            skipped += stats["skipped"]
            run_row.completed = completed
            run_row.failed = failed
            run_row.skipped = skipped
            db.commit()
            offset += len(chunk)

        run_row.status = "succeeded"
        run_row.finished_at = datetime.now(timezone.utc)
        db.commit()
        log.info(
            "Preannotate run %s done: completed=%s failed=%s skipped=%s total=%s",
            run_id, completed, failed, skipped, run_row.total_planned,
        )
    except Exception as e:
        log.exception("Preannotate job failed")
        try:
            failed_row = db.query(PreannotateRun).filter_by(id=run_id).first()
            if failed_row:
                failed_row.status = "failed"
                failed_row.error_message = str(e)[:4000]
                failed_row.finished_at = datetime.now(timezone.utc)
                db.commit()
        except Exception:
            db.rollback()
        sys.exit(1)
    finally:
        db.close()
