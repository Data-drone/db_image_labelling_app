"""Authorization tests for destructive Admin API operations."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.tests.conftest import test_client


MUTATIONS = (
    ("post", "/api/admin/provision-lakebase", {"project_id": "blocked"}),
    ("post", "/api/admin/reset-database", None),
    ("post", "/api/admin/connect-lakebase", {"project_id": "blocked"}),
    ("get", "/api/admin/test-serving", None),
)


def _request(client, method, path, body=None, email="user@databricks.com"):
    headers = {"X-Forwarded-Email": email}
    if method == "post":
        return client.post(path, json=body, headers=headers)
    return client.get(path, headers=headers)


class TestAdminAuthorization(unittest.TestCase):
    def _client(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        return test_client(Path(temp_dir.name))

    def test_empty_allowlist_disables_all_mutations(self):
        with patch.dict("os.environ", {"ADMIN_EMAILS": ""}):
            with self._client() as (client, _main, _tmp):
                for method, path, body in MUTATIONS:
                    with self.subTest(path=path):
                        response = _request(client, method, path, body)
                        self.assertEqual(response.status_code, 403)
                        self.assertIn("disabled", response.json()["detail"].lower())

    def test_non_admin_is_rejected(self):
        with patch.dict("os.environ", {"ADMIN_EMAILS": "admin@databricks.com"}):
            with self._client() as (client, _main, _tmp):
                for method, path, body in MUTATIONS:
                    with self.subTest(path=path):
                        response = _request(client, method, path, body)
                        self.assertEqual(response.status_code, 403)
                        self.assertIn("admin access", response.json()["detail"].lower())

    def test_user_identifier_is_not_accepted_as_email(self):
        with patch.dict("os.environ", {"ADMIN_EMAILS": "admin@databricks.com"}):
            with self._client() as (client, _main, _tmp):
                response = client.post(
                    "/api/admin/reset-database",
                    headers={"X-Forwarded-User": "admin@databricks.com"},
                )
                self.assertEqual(response.status_code, 403)
                self.assertIn("email is required", response.json()["detail"].lower())

    def test_status_reports_case_insensitive_admin_match(self):
        with patch.dict(
            "os.environ",
            {"ADMIN_EMAILS": "other@databricks.com, ADMIN@DATABRICKS.COM"},
        ):
            with self._client() as (client, _main, _tmp):
                admin = client.get(
                    "/api/admin/db-status",
                    headers={"X-Forwarded-Email": "admin@databricks.com"},
                )
                user = client.get(
                    "/api/admin/db-status",
                    headers={"X-Forwarded-Email": "user@databricks.com"},
                )
                self.assertTrue(admin.json()["admin_mutations_enabled"])
                self.assertFalse(user.json()["admin_mutations_enabled"])

    def test_authorized_admin_can_reset_database(self):
        with patch.dict("os.environ", {"ADMIN_EMAILS": "admin@databricks.com"}):
            with self._client() as (client, _main, _tmp):
                response = _request(
                    client,
                    "post",
                    "/api/admin/reset-database",
                    email="ADMIN@DATABRICKS.COM",
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
