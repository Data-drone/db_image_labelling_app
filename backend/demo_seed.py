"""Optional startup seeding of a ready-to-use demo project.

A fresh deployment lands on an empty project list, which is a poor first
impression for a demo and useless on the SQLite fallback, where the database
lives in the container's ``/tmp`` and is therefore recreated on every restart.
Seeding closes that gap: point ``SEED_DEMO_PROJECTS`` at a UC Volume folder of
images and the app comes up with a project already populated.

Off unless configured, and idempotent by project name -- a project that
already exists is left exactly as the user left it, annotations included, so
this is safe to leave enabled on a persistent Lakebase deployment too.

Env:
  SEED_DEMO_PROJECTS   ``<name>|<task_type>|<classes,comma,separated>|<volume path>``
                       Repeat with ``;`` to seed several projects.
                       Empty/unset disables seeding.
"""

from __future__ import annotations

import logging
import os

from .models import LabelingProject
from .volumes import scan_volume_for_samples

log = logging.getLogger(__name__)

SEEDED_BY = "demo-seed"


def _parse(spec: str) -> list[dict]:
    out = []
    for chunk in spec.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split("|")]
        if len(parts) != 4 or not all(parts):
            log.warning("Ignoring malformed SEED_DEMO_PROJECTS entry: %r", chunk)
            continue
        name, task_type, classes, path = parts
        class_list = [c.strip() for c in classes.split(",") if c.strip()]
        if not class_list:
            log.warning("Ignoring seed entry with no classes: %r", chunk)
            continue
        out.append({
            "name": name,
            "task_type": task_type,
            "class_list": class_list,
            "source_volume": path,
        })
    return out


def seed_demo_projects(session_factory) -> None:
    """Create the configured demo projects if they are missing.

    Never raises: a failed seed must not take the app down with it, since the
    app is perfectly usable without the demo data.
    """
    spec = os.environ.get("SEED_DEMO_PROJECTS", "").strip()
    if not spec:
        return

    for cfg in _parse(spec):
        try:
            with session_factory() as db:
                existing = db.query(LabelingProject).filter_by(name=cfg["name"]).first()
                if existing:
                    log.info("Demo project '%s' already exists, leaving it alone", cfg["name"])
                    continue
                project = LabelingProject(
                    name=cfg["name"],
                    description=(
                        "Seeded demo project. Delete it if you don't want it -- "
                        "it is only recreated if it is missing at startup."
                    ),
                    task_type=cfg["task_type"],
                    class_list=cfg["class_list"],
                    source_volume=cfg["source_volume"],
                    created_by=SEEDED_BY,
                )
                db.add(project)
                db.flush()  # need project.id before adding samples
                n = scan_volume_for_samples(db, project.id, cfg["source_volume"])
                if n == 0:
                    # An empty project is worse than none: it looks broken and
                    # hides the real cause, which is the volume path or grants.
                    db.rollback()
                    log.warning(
                        "No images found under %s, skipping demo project '%s'",
                        cfg["source_volume"], cfg["name"],
                    )
                    continue
                db.commit()
                log.info("Seeded demo project '%s' (%s) with %d samples from %s",
                         cfg["name"], cfg["task_type"], n, cfg["source_volume"])
        except Exception as e:
            log.warning("Demo seed failed for '%s': %s", cfg.get("name"), e)
