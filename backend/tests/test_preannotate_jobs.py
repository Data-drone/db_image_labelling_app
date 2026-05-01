"""
Tests for async pre-annotate (Databricks Job enqueue) and run status APIs.

Uses SQLite + TestClient (see conftest). Mocks ``jobs.run_now`` path via
``trigger_preannotate_job`` and endpoint health.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.tests.conftest import make_sample_volume, test_client


def _create_project(c, source_volume: Path, **over):
    body = {
        "name": "preann-proj",
        "description": "",
        "task_type": "classification",
        "class_list": ["cat", "dog"],
        "source_volume": str(source_volume),
    }
    body.update(over)
    r = c.post("/api/projects", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


class TestPreannotateAsyncEnqueue(unittest.TestCase):
    def test_async_returns_503_when_job_not_configured(self):
        prev = os.environ.pop("PRE_ANNOTATE_DATABRICKS_JOB_ID", None)
        prev2 = os.environ.pop("PRE_ANNOTATE_JOB_ID", None)
        try:
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td)
                with test_client(tmp) as (c, _main, _tmp):
                    vol = make_sample_volume(tmp)
                    pid = _create_project(
                        c,
                        vol,
                        name="p503",
                        serving_endpoint="dummy-endpoint",
                    )
                    r = c.post(
                        f"/api/projects/{pid}/pre-annotate-async",
                        json={"max_samples": 0, "include_pre_labeled": False},
                    )
                    self.assertEqual(r.status_code, 503, r.text)
                    self.assertIn("not configured", r.json()["detail"].lower())
        finally:
            if prev is not None:
                os.environ["PRE_ANNOTATE_DATABRICKS_JOB_ID"] = prev
            if prev2 is not None:
                os.environ["PRE_ANNOTATE_JOB_ID"] = prev2

    def test_async_enqueue_and_fetch_status(self):
        prev = os.environ.get("PRE_ANNOTATE_DATABRICKS_JOB_ID")
        os.environ["PRE_ANNOTATE_DATABRICKS_JOB_ID"] = "12345"
        try:
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td)
                with test_client(tmp) as (c, _main, _tmp):
                    vol = make_sample_volume(tmp)
                    pid = _create_project(
                        c,
                        vol,
                        name="pasync",
                        serving_endpoint="dummy-endpoint",
                    )
                    with (
                        patch(
                            "backend.routes.preannotate_runs.check_endpoint_health",
                            return_value={"status": "ready"},
                        ),
                        patch(
                            "backend.routes.preannotate_runs.trigger_preannotate_job",
                            return_value=987654,
                        ),
                    ):
                        r = c.post(
                            f"/api/projects/{pid}/pre-annotate-async",
                            json={
                                "max_samples": 2,
                                "include_pre_labeled": False,
                                "min_confidence": 0.25,
                            },
                        )
                    self.assertEqual(r.status_code, 200, r.text)
                    body = r.json()
                    self.assertEqual(body["status"], "queued")
                    self.assertEqual(body["databricks_run_id"], 987654)
                    self.assertEqual(body["max_samples"], 2)
                    self.assertFalse(body["include_pre_labeled"])
                    self.assertAlmostEqual(body["min_confidence"], 0.25)
                    rid = body["id"]

                    r2 = c.get(f"/api/projects/{pid}/pre-annotate-runs/{rid}")
                    self.assertEqual(r2.status_code, 200, r2.text)
                    self.assertEqual(r2.json()["id"], rid)

                    r3 = c.get(f"/api/projects/{pid}/pre-annotate-runs/latest")
                    self.assertEqual(r3.status_code, 200)
                    self.assertEqual(r3.json()["id"], rid)
        finally:
            if prev is None:
                os.environ.pop("PRE_ANNOTATE_DATABRICKS_JOB_ID", None)
            else:
                os.environ["PRE_ANNOTATE_DATABRICKS_JOB_ID"] = prev

    def test_get_run_wrong_project_404(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            with test_client(tmp) as (c, _main, _tmp):
                vol = make_sample_volume(tmp)
                p1 = _create_project(
                    c, vol, name="pa", serving_endpoint="dummy-endpoint"
                )
                p2 = _create_project(
                    c, vol, name="pb", serving_endpoint="dummy-endpoint"
                )
                os.environ["PRE_ANNOTATE_DATABRICKS_JOB_ID"] = "1"
                try:
                    with (
                        patch(
                            "backend.routes.preannotate_runs.check_endpoint_health",
                            return_value={"status": "ready"},
                        ),
                        patch(
                            "backend.routes.preannotate_runs.trigger_preannotate_job",
                            return_value=1,
                        ),
                    ):
                        r = c.post(
                            f"/api/projects/{p1}/pre-annotate-async",
                            json={},
                        )
                    rid = r.json()["id"]
                    r404 = c.get(f"/api/projects/{p2}/pre-annotate-runs/{rid}")
                    self.assertEqual(r404.status_code, 404)
                finally:
                    os.environ.pop("PRE_ANNOTATE_DATABRICKS_JOB_ID", None)


class TestPreannotateTriggers(unittest.TestCase):
    def test_resolve_job_id_from_env(self):
        from backend import preannotate_triggers as pt

        prev = os.environ.get("PRE_ANNOTATE_DATABRICKS_JOB_ID")
        prev2 = os.environ.get("PRE_ANNOTATE_JOB_ID")
        try:
            os.environ.pop("PRE_ANNOTATE_DATABRICKS_JOB_ID", None)
            os.environ.pop("PRE_ANNOTATE_JOB_ID", None)
            self.assertIsNone(pt.resolve_preannotate_job_id())
            os.environ["PRE_ANNOTATE_DATABRICKS_JOB_ID"] = " 42 "
            self.assertEqual(pt.resolve_preannotate_job_id(), 42)
            os.environ.pop("PRE_ANNOTATE_DATABRICKS_JOB_ID", None)
            os.environ["PRE_ANNOTATE_JOB_ID"] = "99"
            self.assertEqual(pt.resolve_preannotate_job_id(), 99)
        finally:
            if prev:
                os.environ["PRE_ANNOTATE_DATABRICKS_JOB_ID"] = prev
            else:
                os.environ.pop("PRE_ANNOTATE_DATABRICKS_JOB_ID", None)
            if prev2:
                os.environ["PRE_ANNOTATE_JOB_ID"] = prev2
            else:
                os.environ.pop("PRE_ANNOTATE_JOB_ID", None)


class TestInferenceSettingsJobFlag(unittest.TestCase):
    def test_settings_includes_async_flag(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            with test_client(tmp) as (c, _main, _tmp):
                vol = make_sample_volume(tmp)
                pid = _create_project(c, vol, name="pset")
                r = c.get(f"/api/projects/{pid}/settings")
                self.assertEqual(r.status_code, 200)
                data = r.json()
                self.assertIn("async_preannotate_job_configured", data)
                self.assertFalse(data["async_preannotate_job_configured"])
