#!/usr/bin/env python3
"""Build the React SPA into frontend/dist for Databricks Apps.

The Apps runtime installs Python from requirements.txt and serves the
prebuilt dist. It does not run npm: package.json lives under frontend/,
not the repo root. This script is the local/CI production build.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SETUP_GUIDE = """
Install Node.js 18+ and npm, then from the repo root:

  cd frontend && npm ci

The Databricks Apps runtime does not install npm packages. It uses
requirements.txt for Python and serves the generated frontend/dist.
Do not add a root package.json — that would make Apps run npm install
at startup.
""".strip()


class FrontendBuildError(RuntimeError):
    """Production frontend build could not run or did not produce dist."""


def _fail(reason: str) -> None:
    raise FrontendBuildError(f"{reason}\n\n{SETUP_GUIDE}")


def build_frontend(
    repo_root: Path | None = None,
    *,
    npm: str | None = None,
    run: Callable[..., object] = subprocess.run,
) -> Path:
    """Run `npm run build` and return the dist directory.

    Fails before invoking npm if Node/npm or frontend/node_modules is
    missing. Fails after npm if the process errors or dist/index.html
    is not produced.
    """
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    frontend = root / "frontend"
    if not (frontend / "package.json").is_file():
        _fail(f"frontend/package.json not found at {frontend}.")

    npm_bin = npm or shutil.which("npm")
    if not npm_bin:
        _fail("npm was not found on PATH.")

    if not (frontend / "node_modules").is_dir():
        _fail("frontend/node_modules is missing.")

    completed = run(
        [npm_bin, "run", "build"],
        cwd=frontend,
        check=False,
    )
    if getattr(completed, "returncode", 1) != 0:
        _fail("npm run build failed. Fix the frontend build before deploying.")

    dist_index = frontend / "dist" / "index.html"
    if not dist_index.is_file():
        _fail("npm run build finished but frontend/dist/index.html was not created.")

    return frontend / "dist"


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        dist = build_frontend()
    except FrontendBuildError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Frontend production build written to {dist}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
