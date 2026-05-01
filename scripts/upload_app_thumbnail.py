#!/usr/bin/env python3
"""
Upload an image to the Databricks App overview thumbnail (Apps list / app details).

Default asset (first match wins):
  assets/databricks-app-thumbnail.jpg
  assets/databricks-app-thumbnail.jpeg
  assets/databricks-app-thumbnail.png

  python scripts/upload_app_thumbnail.py <app-name> [--image path/to/file]

Requires Databricks CLI auth (same profile/host as your workspace).
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS = REPO_ROOT / "assets"
DEFAULT_CANDIDATES = [
    ASSETS / "databricks-app-thumbnail.jpg",
    ASSETS / "databricks-app-thumbnail.jpeg",
    ASSETS / "databricks-app-thumbnail.png",
]


def resolve_default_image() -> Path | None:
    for p in DEFAULT_CANDIDATES:
        if p.is_file():
            return p
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("app_name", help="Databricks App name (workspace)")
    ap.add_argument(
        "--image",
        type=Path,
        default=None,
        help="Image file (JPG/PNG/etc.). Omit to use the first existing default under assets/.",
    )
    args = ap.parse_args()
    img: Path | None = args.image
    if img is None:
        img = resolve_default_image()
    if img is None or not img.is_file():
        print("No thumbnail image found.", file=sys.stderr)
        print("Add one of:", file=sys.stderr)
        for p in DEFAULT_CANDIDATES:
            print(f"  {p}", file=sys.stderr)
        print("Or pass --image /path/to/your/file.jpg", file=sys.stderr)
        sys.exit(1)
    b64 = base64.b64encode(img.read_bytes()).decode("ascii")
    body = {"app_thumbnail": {"thumbnail": b64}}
    subprocess.run(
        [
            "databricks",
            "apps",
            "update-app-thumbnail",
            args.app_name,
            "--json",
            json.dumps(body),
        ],
        check=True,
        cwd=str(REPO_ROOT),
    )
    print(f"App thumbnail updated (from {img.relative_to(REPO_ROOT)}).", flush=True)


if __name__ == "__main__":
    main()
