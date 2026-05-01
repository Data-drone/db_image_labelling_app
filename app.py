"""
Databricks Apps entry: locate the Git checkout under /app then run start.py.

The runtime cwd is not always the repo root; start.py also normalizes cwd for uvicorn.
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def _find_project_root() -> Path | None:
    """Search a few likely layout roots (Databricks + local)."""
    candidates: list[Path] = []
    ap = Path("/app")
    if ap.is_dir():
        candidates.append(ap)
        try:
            for sub in ap.iterdir():
                if sub.is_dir():
                    candidates.append(sub)
                    try:
                        for sub2 in sub.iterdir():
                            if sub2.is_dir():
                                candidates.append(sub2)
                    except OSError:
                        pass
        except OSError:
            pass
    here = Path(__file__).resolve().parent
    candidates.append(here)
    candidates.append(Path.cwd())

    seen: set[Path] = set()
    for base in candidates:
        base = base.resolve()
        if base in seen or not base.is_dir():
            continue
        seen.add(base)
        start = base / "start.py"
        if start.is_file() and (base / "backend").is_dir() and (base / "requirements.txt").is_file():
            return base
    return None


def main() -> None:
    root = _find_project_root()
    if root is None:
        print(
            "Could not find project root (start.py + backend/ + requirements.txt).",
            file=sys.stderr,
        )
        sys.exit(1)
    os.chdir(root)
    sys.path.insert(0, str(root))
    runpy.run_path(str(root / "start.py"), run_name="__main__")


if __name__ == "__main__":
    main()
