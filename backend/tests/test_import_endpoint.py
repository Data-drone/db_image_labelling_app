"""
HTTP-level tests for POST /api/projects/{id}/import.

Uses SQLite backend and local-filesystem stand-ins for UC Volumes.
Uses stdlib ``unittest`` (pytest not guaranteed in the container).
"""
import tempfile
import unittest
from pathlib import Path

from backend.tests.conftest import test_client


class TestImportEndpointSkeleton(unittest.TestCase):
    def test_projects_list_reachable(self):
        with tempfile.TemporaryDirectory() as td:
            with test_client(Path(td)) as (c, _main, _tmp):
                r = c.get("/api/projects")
                self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
