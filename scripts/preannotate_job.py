#!/usr/bin/env python3
"""
Databricks Job entrypoint — argv[1] = ``preannotate_runs.id`` (database row).

The bundle job should pass ``{{job.parameters.run_id}}`` as the first parameter
(see ``resources/preannotate_job.job.yml``).

Environment matches the CV Explorer app (Lakebase or ``DATABASE_URL``, Databricks
auth for volumes + serving, ``USE_LAKEBASE``, etc.). Bundle parameters inject
the exact Lakebase endpoint/database and inference adapter.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Resolve the repo root so ``import backend…`` works.
#
# On classic compute __file__ is set and the script is at <root>/scripts/…
# On serverless __file__ is NOT set, but the python_file workspace path is in
# sys.argv[0]. The deployed layout is:
#   /Workspace/…/files/scripts/preannotate_job.py
#   /Workspace/…/files/backend/…
# So parent-of-parent of the script path is the repo root.
_candidates = []
try:
    _candidates.append(Path(__file__).resolve().parents[1])
except NameError:
    pass

if sys.argv and sys.argv[0]:
    _candidates.append(Path(sys.argv[0]).parent.parent)

for _root in _candidates:
    if (_root / "backend").is_dir():
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))
        break
else:
    # Last resort: cwd might be the repo root already
    if (Path.cwd() / "backend").is_dir():
        sys.path.insert(0, str(Path.cwd()))


def _run_id() -> int:
    if len(sys.argv) > 1:
        try:
            return int(sys.argv[1])
        except ValueError:
            pass
    v = os.environ.get("PREANNOTATE_RUN_ID") or os.environ.get("run_id")
    if not v:
        raise SystemExit("Missing run id: pass argv[1] or set PREANNOTATE_RUN_ID / run_id")
    return int(v)


def _inject_sp_credentials():
    """Read SP OAuth credentials from argv (passed via job parameters) and set as env vars."""
    sp_id = sys.argv[2] if len(sys.argv) > 2 else ""
    sp_secret = sys.argv[3] if len(sys.argv) > 3 else ""

    if sp_id and sp_secret:
        os.environ["SP_SERVING_CLIENT_ID"] = sp_id
        os.environ["SP_SERVING_CLIENT_SECRET"] = sp_secret
        print(f"[PREANNOTATE] SP OAuth credentials injected (client_id={sp_id[:8]}...)", flush=True)
    else:
        print(f"[PREANNOTATE] No SP credentials in argv (argc={len(sys.argv)})", flush=True)


def _inject_bundle_config():
    """Copy target-specific job parameters into the worker environment."""
    values = {
        "LAKEBASE_PROJECT_ID": sys.argv[4] if len(sys.argv) > 4 else "",
        "LAKEBASE_ENDPOINT": sys.argv[5] if len(sys.argv) > 5 else "",
        "LAKEBASE_DATABASE_NAME": sys.argv[6] if len(sys.argv) > 6 else "",
        "LAKEBASE_SCHEMA": sys.argv[7] if len(sys.argv) > 7 else "",
        "SERVING_ENDPOINT_ADAPTER": sys.argv[8] if len(sys.argv) > 8 else "",
        "SERVING_COLD_START_MAX_RETRIES": sys.argv[9] if len(sys.argv) > 9 else "",
        "SERVING_COLD_START_INITIAL_DELAY_S": sys.argv[10] if len(sys.argv) > 10 else "",
        "SERVING_COLD_START_MAX_DELAY_S": sys.argv[11] if len(sys.argv) > 11 else "",
        "SERVING_COLD_START_JOB_BUDGET_S": sys.argv[12] if len(sys.argv) > 12 else "",
    }
    for name, value in values.items():
        if value:
            os.environ[name] = value
    if values["LAKEBASE_ENDPOINT"] and values["LAKEBASE_DATABASE_NAME"]:
        os.environ["LAKEBASE_AUTO_PROVISION"] = "false"


def _main():
    _inject_sp_credentials()
    _inject_bundle_config()
    from backend.jobs.preannotate_worker import main
    main(_run_id())


if __name__ == "__main__":
    _main()
