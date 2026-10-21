#!/usr/bin/env python3
"""Build frontend/dist, then deploy with `databricks apps deploy`.

Usage:
  python scripts/deploy_app.py --target <target> --profile <profile>
  python scripts/deploy_app.py --build-only

A failed production build exits before the Databricks CLI is invoked, so
stale UI assets are not staged or uploaded. The running App never needs
npm: it installs requirements.txt and serves the uploaded frontend/dist.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.build_frontend import FrontendBuildError, build_frontend


def deploy_app(
    deploy_args: list[str],
    *,
    build_only: bool = False,
    repo_root: Path | None = None,
    run: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    npm: str | None = None,
) -> int:
    try:
        dist = build_frontend(repo_root, npm=npm, run=run)
    except FrontendBuildError as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"Using frontend assets from {dist}")
    if build_only:
        return 0

    databricks = which("databricks")
    if not databricks:
        print(
            "databricks CLI was not found on PATH. Install it, then rerun:\n"
            "  python scripts/deploy_app.py --target <target> --profile <profile>",
            file=sys.stderr,
        )
        return 1

    completed = run([databricks, "apps", "deploy", *deploy_args], check=False)
    return getattr(completed, "returncode", 1)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    build_only = "--build-only" in args
    deploy_args = [a for a in args if a != "--build-only"]
    return deploy_app(deploy_args, build_only=build_only)


if __name__ == "__main__":
    sys.exit(main())
