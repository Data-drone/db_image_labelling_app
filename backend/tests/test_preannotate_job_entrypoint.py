"""Tests for bundle parameters consumed by the serverless job entrypoint."""

import os
import sys
import unittest
from unittest.mock import patch

from scripts import preannotate_job


class TestPreannotateBundleConfig(unittest.TestCase):
    def test_injects_exact_lakebase_target_and_adapter(self):
        argv = [
            "preannotate_job.py",
            "17",
            "client-id",
            "client-secret",
            "cv-explorer",
            "projects/cv-explorer/branches/production/endpoints/primary",
            "databricks_postgres",
            "sam31",
        ]
        keys = (
            "LAKEBASE_PROJECT_ID",
            "LAKEBASE_ENDPOINT",
            "LAKEBASE_DATABASE_NAME",
            "SERVING_ENDPOINT_ADAPTER",
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
            self.assertEqual(os.environ["SERVING_ENDPOINT_ADAPTER"], "sam31")
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
