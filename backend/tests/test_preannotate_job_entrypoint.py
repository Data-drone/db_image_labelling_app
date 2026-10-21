"""Tests for bundle parameters consumed by the serverless job entrypoint."""

import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import preannotate_job

_JOB_YAML = Path(__file__).resolve().parents[2] / "resources" / "preannotate_job.job.yml"


class TestPreannotateBundleConfig(unittest.TestCase):
    def test_injects_named_arguments(self):
        argv = [
            "preannotate_job.py",
            "17",
            "client-id",
            "client-secret",
            "LAKEBASE_ENDPOINT=projects/cv-explorer/branches/production/endpoints/primary",
            "LAKEBASE_DATABASE_NAME=databricks_postgres",
            "LAKEBASE_SCHEMA=",
            "PRE_ANNOTATE_JOB_BATCH_SIZE=25",
            "PRE_ANNOTATE_MAX_CONSECUTIVE_UNAVAILABLE=5",
        ]
        with patch.object(sys, "argv", argv), patch.dict(
            os.environ, {}, clear=True
        ):
            preannotate_job._inject_bundle_config()
            self.assertEqual(
                dict(os.environ),
                {
                    "LAKEBASE_ENDPOINT": "projects/cv-explorer/branches/production/endpoints/primary",
                    "LAKEBASE_DATABASE_NAME": "databricks_postgres",
                    "PRE_ANNOTATE_JOB_BATCH_SIZE": "25",
                    "PRE_ANNOTATE_MAX_CONSECUTIVE_UNAVAILABLE": "5",
                    "LAKEBASE_AUTO_PROVISION": "false",
                },
            )

    def test_rejects_unknown_named_argument(self):
        argv = [
            "preannotate_job.py",
            "17",
            "",
            "",
            "LAKEBASE_SCHEMA=cv_explorer_app",
            "PATH=/tmp/evil",
        ]
        with patch.object(sys, "argv", argv), patch.dict(
            os.environ, {}, clear=True
        ):
            with self.assertRaisesRegex(SystemExit, "'PATH'"):
                preannotate_job._inject_bundle_config()
            self.assertNotIn("PATH", os.environ)

    def test_bundle_task_arguments_are_allowlisted(self):
        text = _JOB_YAML.read_text()
        declared = set(re.findall(r"^\s+- name: (\w+)$", text, re.MULTILINE))
        pairs = re.findall(
            r'^\s+- "([A-Z_]+)=\{\{job\.parameters\.(\w+)\}\}"$',
            text,
            re.MULTILINE,
        )

        self.assertIn(("PRE_ANNOTATE_JOB_BATCH_SIZE", "preannotate_job_batch_size"), pairs)
        for env_name, param in pairs:
            self.assertIn(env_name, preannotate_job._JOB_ENV_NAMES)
            self.assertIn(param, declared)

    def test_injects_legacy_positional_arguments(self):
        argv = [
            "preannotate_job.py",
            "17",
            "client-id",
            "client-secret",
            "cv-explorer",
            "projects/cv-explorer/branches/production/endpoints/primary",
            "databricks_postgres",
            "cv_explorer_app",
            "sam31",
            "3",
            "5",
            "60",
            "900",
        ]
        keys = (
            "LAKEBASE_PROJECT_ID",
            "LAKEBASE_ENDPOINT",
            "LAKEBASE_DATABASE_NAME",
            "LAKEBASE_SCHEMA",
            "SERVING_ENDPOINT_ADAPTER",
            "SERVING_COLD_START_MAX_RETRIES",
            "SERVING_COLD_START_INITIAL_DELAY_S",
            "SERVING_COLD_START_MAX_DELAY_S",
            "SERVING_COLD_START_JOB_BUDGET_S",
            "LAKEBASE_AUTO_PROVISION",
        )
        with patch.object(sys, "argv", argv), patch.dict(
            os.environ, {}, clear=True
        ):
            preannotate_job._inject_bundle_config()
            self.assertEqual(os.environ["LAKEBASE_PROJECT_ID"], "cv-explorer")
            self.assertEqual(
                os.environ["LAKEBASE_ENDPOINT"],
                "projects/cv-explorer/branches/production/endpoints/primary",
            )
            self.assertEqual(
                os.environ["LAKEBASE_DATABASE_NAME"], "databricks_postgres"
            )
            self.assertEqual(os.environ["LAKEBASE_SCHEMA"], "cv_explorer_app")
            self.assertEqual(os.environ["SERVING_ENDPOINT_ADAPTER"], "sam31")
            self.assertEqual(os.environ["SERVING_COLD_START_MAX_RETRIES"], "3")
            self.assertEqual(
                os.environ["SERVING_COLD_START_INITIAL_DELAY_S"], "5"
            )
            self.assertEqual(os.environ["SERVING_COLD_START_MAX_DELAY_S"], "60")
            self.assertEqual(
                os.environ["SERVING_COLD_START_JOB_BUDGET_S"], "900"
            )
            self.assertEqual(os.environ["LAKEBASE_AUTO_PROVISION"], "false")
            self.assertEqual(set(keys), set(os.environ))

    def test_partial_legacy_arguments_do_not_disable_auto_provision(self):
        with patch.object(
            sys,
            "argv",
            ["preannotate_job.py", "17", "", "", "cv-explorer"],
        ), patch.dict(os.environ, {}, clear=True):
            preannotate_job._inject_bundle_config()
            self.assertEqual(os.environ["LAKEBASE_PROJECT_ID"], "cv-explorer")
            self.assertNotIn("LAKEBASE_AUTO_PROVISION", os.environ)


if __name__ == "__main__":
    unittest.main()
