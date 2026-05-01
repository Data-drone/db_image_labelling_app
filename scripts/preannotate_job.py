#!/usr/bin/env python3
"""
Databricks Job entrypoint — argv[1] = ``preannotate_runs.id`` (database row).

The bundle job should pass ``{{job.parameters.run_id}}`` as the first parameter
(see ``resources/preannotate_job.job.yml``).

Environment matches the CV Explorer app (Lakebase or ``DATABASE_URL``, Databricks
auth for volumes + serving, ``USE_LAKEBASE``, etc.).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Repo root (parent of scripts/)
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run_id() -> int:
    if len(sys.argv) > 1:
        return int(sys.argv[1])
    v = os.environ.get("PREANNOTATE_RUN_ID") or os.environ.get("run_id")
    if not v:
        raise SystemExit("Missing run id: pass argv[1] or set PREANNOTATE_RUN_ID / run_id")
    return int(v)


if __name__ == "__main__":
    from backend.jobs.preannotate_worker import main

    main(_run_id())
