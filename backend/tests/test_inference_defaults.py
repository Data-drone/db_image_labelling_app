"""Tests for GET /api/inference-defaults (env-backed UI defaults)."""

import os
import tempfile
import unittest
from pathlib import Path

from backend.tests.conftest import test_client


class TestInferenceDefaults(unittest.TestCase):
    def test_inference_defaults_reflects_serving_endpoint_env(self):
        prev = os.environ.get("SERVING_ENDPOINT")
        try:
            with tempfile.TemporaryDirectory() as td:
                with test_client(Path(td)) as (c, _, _):
                    if prev is not None:
                        os.environ.pop("SERVING_ENDPOINT", None)
                    r = c.get("/api/inference-defaults")
                    self.assertEqual(r.status_code, 200, r.text)
                    self.assertIsNone(r.json().get("default_serving_endpoint"))

                    os.environ["SERVING_ENDPOINT"] = "env-default-endpoint"
                    r2 = c.get("/api/inference-defaults")
                    self.assertEqual(r2.status_code, 200, r2.text)
                    self.assertEqual(
                        r2.json().get("default_serving_endpoint"),
                        "env-default-endpoint",
                    )
        finally:
            if prev is None:
                os.environ.pop("SERVING_ENDPOINT", None)
            else:
                os.environ["SERVING_ENDPOINT"] = prev
