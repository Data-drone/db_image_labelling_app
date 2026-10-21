#!/usr/bin/env python3
"""Reject Databricks Apps source files above the platform per-file limit.

The Apps source export/import cap is 10 MiB per file (10,485,760 bytes).
This check walks the tree that would be staged after frontend build and
bundle sync exclusions, so local install trees do not false-fail.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Databricks Apps: "File is larger than 10485760 bytes"
APPS_MAX_FILE_BYTES = 10_485_760

# Directory names never part of the Apps source upload. Matching is by path
# component so `.venv` is not confused with `env`.
EXCLUDED_DIR_NAMES = frozenset(
    {".git", ".databricks", "node_modules", ".venv", "venv", "env"}
)

# Always omitted from git-based bundle sync; scanning them would false-fail
# on local caches (.databricks terraform providers, git packs, venvs).
DEFAULT_STAGING_EXCLUDES = (
    ".git/**",
    ".databricks/**",
    "**/node_modules/**",
    "**/.venv/**",
    "**/venv/**",
    "**/env/**",
)

GUIDANCE = (
    "Databricks Apps rejects any source file larger than 10,485,760 bytes "
    "(10 MiB). Wheels, model weights, and similar artifacts belong in UC "
    "Volumes or other artifact storage, not the app source bundle."
)


class AppSourceTooLargeError(RuntimeError):
    """Staged app source contains at least one file over the Apps limit."""


def _posix(path: str | Path) -> str:
    return str(path).replace("\\", "/").lstrip("./")


def load_sync_exclude_patterns(repo_root: Path) -> list[str]:
    """Read `sync.exclude` entries from databricks.yml if present."""
    manifest = Path(repo_root) / "databricks.yml"
    if not manifest.is_file():
        return []

    patterns: list[str] = []
    in_sync = False
    in_exclude = False
    for raw_line in manifest.read_text().splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.startswith("sync:"):
            in_sync = True
            in_exclude = False
            continue
        if not in_sync:
            continue
        if not raw_line.startswith(" ") and not raw_line.startswith("\t"):
            break
        stripped = line.strip()
        if stripped.startswith("exclude:"):
            in_exclude = True
            continue
        if in_exclude and stripped.startswith("- "):
            patterns.append(stripped[2:].strip().strip("'\""))
            continue
        if in_exclude and not stripped.startswith("-"):
            in_exclude = False
    return patterns


def path_is_excluded(rel_posix: str, patterns: Sequence[str]) -> bool:
    rel = _posix(rel_posix)
    if any(part in EXCLUDED_DIR_NAMES for part in Path(rel).parts):
        return True
    for raw in patterns:
        pat = _posix(raw)
        if not pat:
            continue
        if pat.endswith("/"):
            pat = pat + "**"
        if fnmatch.fnmatch(rel, pat):
            return True
        if not pat.endswith("/**") and fnmatch.fnmatch(rel, pat + "/**"):
            return True
        # `**/tmp/**` must also match a repo-root `tmp/...` path.
        if pat.startswith("**/") and fnmatch.fnmatch(rel, pat[3:]):
            return True
        if "/" not in pat.rstrip("*"):
            if fnmatch.fnmatch(Path(rel).name, pat):
                return True
            if fnmatch.fnmatch(rel, f"**/{pat}"):
                return True
    return False


def _git_staged_paths(repo_root: Path) -> list[Path] | None:
    """Return tracked and non-ignored files, or None outside a Git worktree."""
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            check=False,
            capture_output=True,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None

    paths: list[Path] = []
    for raw_path in completed.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = repo_root / os.fsdecode(raw_path)
        if path.is_file():
            paths.append(path)
    return paths


def iter_staged_files(
    repo_root: Path,
    *,
    extra_excludes: Sequence[str] = (),
) -> Iterable[tuple[Path, int]]:
    """Yield (path, size) for files that would be in the Apps source tree.

    Bundle sync honors `.gitignore`, while still uploading tracked files that
    now match an ignore rule. `git ls-files` supplies exactly that base set.
    The fallback keeps the standalone checker usable outside a Git worktree.
    """
    root = Path(repo_root)
    patterns = [
        *DEFAULT_STAGING_EXCLUDES,
        *load_sync_exclude_patterns(root),
        *extra_excludes,
    ]
    git_paths = _git_staged_paths(root)
    paths = git_paths if git_paths is not None else root.rglob("*")
    for path in paths:
        if not path.is_file():
            continue
        rel = _posix(path.relative_to(root))
        if path_is_excluded(rel, patterns):
            continue
        yield path, path.stat().st_size


def find_oversized_staged_files(
    repo_root: Path,
    *,
    max_bytes: int = APPS_MAX_FILE_BYTES,
    extra_excludes: Sequence[str] = (),
) -> list[tuple[str, int]]:
    oversized: list[tuple[str, int]] = []
    root = Path(repo_root)
    for path, size in iter_staged_files(root, extra_excludes=extra_excludes):
        if size > max_bytes:
            oversized.append((_posix(path.relative_to(root)), size))
    oversized.sort(key=lambda item: (-item[1], item[0]))
    return oversized


def format_oversized_error(offenders: Sequence[tuple[str, int]]) -> str:
    lines = [
        "App source exceeds the Databricks Apps 10 MiB per-file limit.",
        "",
        GUIDANCE,
        "",
        "Oversized staged files:",
    ]
    for rel, size in offenders:
        lines.append(f"  {rel}  {size:,} bytes ({size / (1024 * 1024):.2f} MiB)")
    return "\n".join(lines)


def check_app_source_size(
    repo_root: Path | None = None,
    *,
    max_bytes: int = APPS_MAX_FILE_BYTES,
) -> None:
    """Raise AppSourceTooLargeError if any staged file is over the limit."""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    offenders = find_oversized_staged_files(root, max_bytes=max_bytes)
    if offenders:
        raise AppSourceTooLargeError(format_oversized_error(offenders))


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        check_app_source_size()
    except AppSourceTooLargeError as exc:
        print(exc, file=sys.stderr)
        return 1
    print("All staged app source files are within the 10 MiB per-file limit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
