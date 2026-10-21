"""Apps source must fail locally when a staged file exceeds 10 MiB."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.check_app_source_size import (
    APPS_MAX_FILE_BYTES,
    AppSourceTooLargeError,
    check_app_source_size,
    find_oversized_staged_files,
    load_sync_exclude_patterns,
)
from scripts.deploy_app import deploy_app


class _Proc:
    def __init__(self, returncode: int):
        self.returncode = returncode


def _write(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)


def _init_git(root: Path) -> None:
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=root,
        check=True,
        capture_output=True,
    )


class TestCheckAppSourceSize(unittest.TestCase):
    def test_file_over_limit_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _write(root / "backend" / "too_big.bin", APPS_MAX_FILE_BYTES + 1)
            with self.assertRaises(AppSourceTooLargeError) as ctx:
                check_app_source_size(root)
        self.assertIn("backend/too_big.bin", str(ctx.exception))
        self.assertIn(f"{APPS_MAX_FILE_BYTES + 1:,}", str(ctx.exception))
        self.assertIn("UC Volumes", str(ctx.exception))

    def test_file_at_limit_is_accepted(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _write(root / "backend" / "exact.bin", APPS_MAX_FILE_BYTES)
            check_app_source_size(root)

    def test_excluded_install_trees_do_not_false_fail(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "databricks.yml").write_text(
                "sync:\n  exclude:\n    - frontend/node_modules/**\n    - tmp/**\n"
            )
            _write(
                root / "frontend" / "node_modules" / "pkg" / "huge.bin",
                APPS_MAX_FILE_BYTES + 50,
            )
            _write(root / ".venv" / "lib" / "huge.so", APPS_MAX_FILE_BYTES + 50)
            _write(root / "venv" / "lib" / "huge.so", APPS_MAX_FILE_BYTES + 50)
            _write(root / "tmp" / "scratch.bin", APPS_MAX_FILE_BYTES + 50)
            _write(root / "ok.txt", 16)
            check_app_source_size(root)

    def test_gitignored_local_file_does_not_false_fail(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _init_git(root)
            (root / ".gitignore").write_text("*.db\n")
            _write(root / "local.db", APPS_MAX_FILE_BYTES + 1)
            check_app_source_size(root)

    def test_tracked_ignored_file_is_still_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _init_git(root)
            (root / ".gitignore").write_text("*.db\n")
            _write(root / "tracked.db", APPS_MAX_FILE_BYTES + 1)
            subprocess.run(
                ["git", "add", "--force", "tracked.db"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            with self.assertRaises(AppSourceTooLargeError) as ctx:
                check_app_source_size(root)
        self.assertIn("tracked.db", str(ctx.exception))

    def test_error_lists_every_oversized_staged_file(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _write(root / "a.bin", APPS_MAX_FILE_BYTES + 2)
            _write(root / "nested" / "b.bin", APPS_MAX_FILE_BYTES + 3)
            offenders = find_oversized_staged_files(root)
            with self.assertRaises(AppSourceTooLargeError) as ctx:
                check_app_source_size(root)
        self.assertEqual(
            [path for path, _ in offenders],
            ["nested/b.bin", "a.bin"],
        )
        self.assertIn("a.bin", str(ctx.exception))
        self.assertIn("nested/b.bin", str(ctx.exception))

    def test_reads_sync_exclude_from_databricks_yml(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "databricks.yml").write_text(
                "bundle:\n  name: demo\n"
                "sync:\n  exclude:\n    - .isaac/**\n    - tmp/**\n"
                "variables:\n  app_name:\n    default: demo\n"
            )
            self.assertEqual(
                load_sync_exclude_patterns(root),
                [".isaac/**", "tmp/**"],
            )


class TestDeployAppSourceSize(unittest.TestCase):
    def test_oversized_staged_file_skips_databricks_upload(self):
        calls = []
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            frontend = root / "frontend"
            frontend.mkdir()
            (frontend / "package.json").write_text(
                '{"scripts":{"build":"vite build"}}\n'
            )
            (frontend / "node_modules").mkdir()

            def _run(cmd, **kwargs):
                if cmd[:2] == ["npm", "run"]:
                    dist = Path(kwargs["cwd"]) / "dist"
                    dist.mkdir()
                    (dist / "index.html").write_text("<html></html>")
                    return _Proc(0)
                calls.append(cmd)
                return _Proc(0)

            _write(root / "weights.pt", APPS_MAX_FILE_BYTES + 1)
            code = deploy_app(
                ["--target", "dev", "--profile", "demo"],
                repo_root=root,
                run=_run,
                which=lambda name: name,
                npm="npm",
            )
        self.assertEqual(code, 1)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
