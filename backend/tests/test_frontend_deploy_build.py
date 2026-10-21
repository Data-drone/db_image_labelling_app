"""Frontend production build must succeed before Apps source is uploaded."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.build_frontend import FrontendBuildError, build_frontend
from scripts.deploy_app import deploy_app


def _fake_frontend(tmp: Path, *, node_modules: bool = True) -> Path:
    frontend = tmp / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text('{"scripts":{"build":"vite build"}}\n')
    if node_modules:
        (frontend / "node_modules").mkdir()
    return tmp


class _Proc:
    def __init__(self, returncode: int):
        self.returncode = returncode


class TestBuildFrontend(unittest.TestCase):
    def test_missing_npm_fails_with_setup_guidance(self):
        with tempfile.TemporaryDirectory() as raw:
            root = _fake_frontend(Path(raw))
            with patch("scripts.build_frontend.shutil.which", return_value=None):
                with self.assertRaises(FrontendBuildError) as ctx:
                    build_frontend(root)
        message = str(ctx.exception)
        self.assertIn("npm was not found", message)
        self.assertIn("cd frontend && npm ci", message)
        self.assertIn("requirements.txt", message)

    def test_missing_node_modules_fails_before_npm(self):
        with tempfile.TemporaryDirectory() as raw:
            root = _fake_frontend(Path(raw), node_modules=False)
            ran = []
            with self.assertRaises(FrontendBuildError) as ctx:
                build_frontend(root, npm="/usr/bin/npm", run=lambda *a, **k: ran.append((a, k)))
        self.assertEqual(ran, [])
        self.assertIn("node_modules is missing", str(ctx.exception))
        self.assertIn("npm ci", str(ctx.exception))

    def test_failed_npm_build_does_not_treat_dist_as_ready(self):
        with tempfile.TemporaryDirectory() as raw:
            root = _fake_frontend(Path(raw))
            with self.assertRaises(FrontendBuildError) as ctx:
                build_frontend(root, npm="npm", run=lambda *a, **k: _Proc(1))
            self.assertFalse((root / "frontend" / "dist" / "index.html").exists())
        self.assertIn("npm run build failed", str(ctx.exception))

    def test_successful_build_requires_generated_index(self):
        with tempfile.TemporaryDirectory() as raw:
            root = _fake_frontend(Path(raw))

            def _succeed(cmd, cwd, check):
                dist = Path(cwd) / "dist"
                dist.mkdir()
                (dist / "index.html").write_text("<html></html>")
                return _Proc(0)

            dist = build_frontend(root, npm="npm", run=_succeed)
            self.assertEqual(dist, root / "frontend" / "dist")
            self.assertTrue((dist / "index.html").is_file())


class TestDeployApp(unittest.TestCase):
    def test_failed_build_skips_databricks_upload(self):
        calls = []
        with tempfile.TemporaryDirectory() as raw:
            root = _fake_frontend(Path(raw), node_modules=False)
            code = deploy_app(
                ["--target", "dev", "--profile", "demo"],
                repo_root=root,
                run=lambda *a, **k: calls.append(a[0]) or _Proc(0),
                which=lambda name: f"/bin/{name}",
            )
        self.assertEqual(code, 1)
        self.assertEqual(calls, [])

    def test_successful_build_invokes_apps_deploy_with_passthrough_args(self):
        calls = []
        with tempfile.TemporaryDirectory() as raw:
            root = _fake_frontend(Path(raw))

            def _run(cmd, **kwargs):
                if cmd[:2] == ["npm", "run"]:
                    dist = Path(kwargs["cwd"]) / "dist"
                    dist.mkdir()
                    (dist / "index.html").write_text("<html></html>")
                    return _Proc(0)
                calls.append(cmd)
                return _Proc(0)

            code = deploy_app(
                ["--target", "dev", "--profile", "demo"],
                repo_root=root,
                run=_run,
                which=lambda name: name,
                npm="npm",
            )
        self.assertEqual(code, 0)
        self.assertEqual(calls, [["databricks", "apps", "deploy", "--target", "dev", "--profile", "demo"]])

    def test_build_only_does_not_call_databricks(self):
        calls = []
        with tempfile.TemporaryDirectory() as raw:
            root = _fake_frontend(Path(raw))

            def _run(cmd, **kwargs):
                if cmd[:2] == ["npm", "run"]:
                    dist = Path(kwargs["cwd"]) / "dist"
                    dist.mkdir()
                    (dist / "index.html").write_text("<html></html>")
                    return _Proc(0)
                calls.append(cmd)
                return _Proc(0)

            code = deploy_app(
                ["--target", "dev"],
                build_only=True,
                repo_root=root,
                run=_run,
                which=lambda name: name,
                npm="npm",
            )
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])


class TestRuntimeDoesNotRequireNpm(unittest.TestCase):
    def test_app_manifest_starts_python_not_npm(self):
        app_yml = Path(__file__).resolve().parents[2] / "app.yml"
        text = app_yml.read_text()
        self.assertIn('command: ["python", "start.py"]', text)

    def test_python_requirements_do_not_pull_node(self):
        req = (Path(__file__).resolve().parents[2] / "requirements.txt").read_text()
        self.assertNotIn("npm", req.lower())
        self.assertNotIn("nodejs", req.lower())


if __name__ == "__main__":
    unittest.main()
