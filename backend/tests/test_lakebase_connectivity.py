"""Unit tests for Lakebase URL construction, App-resource detection, and SQLite fallback."""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.lakebase import (
    allow_sqlite_fallback,
    build_psycopg2_url,
    generate_connection_string,
    postgres_database_name,
    uses_app_resource_postgres,
)


class TestBuildPsycopg2Url(unittest.TestCase):
    def test_pins_psycopg2_driver(self):
        url = build_psycopg2_url("ep.example.com", "role", "tok", "databricks_postgres")
        self.assertTrue(url.startswith("postgresql+psycopg2://"))
        self.assertIn("ep.example.com:5432/databricks_postgres", url)
        self.assertIn("sslmode=require", url)
        self.assertNotIn("postgresql://role", url)

    def test_quotes_special_characters(self):
        url = build_psycopg2_url("h", "user@corp", "p/w:d", "db")
        self.assertIn("user%40corp", url)
        self.assertIn("p%2Fw%3Ad", url)


class TestPostgresDatabaseName(unittest.TestCase):
    def test_prefers_explicit_then_env(self):
        prev_a = os.environ.get("LAKEBASE_DATABASE_NAME")
        prev_b = os.environ.get("PGDATABASE")
        try:
            os.environ["LAKEBASE_DATABASE_NAME"] = "from_lb"
            os.environ["PGDATABASE"] = "from_pg"
            self.assertEqual(postgres_database_name("explicit"), "explicit")
            os.environ.pop("LAKEBASE_DATABASE_NAME", None)
            self.assertEqual(postgres_database_name(), "from_pg")
            os.environ.pop("PGDATABASE", None)
            self.assertEqual(postgres_database_name(), "databricks_postgres")
        finally:
            for key, prev in (
                ("LAKEBASE_DATABASE_NAME", prev_a),
                ("PGDATABASE", prev_b),
            ):
                if prev is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = prev


class TestUsesAppResourcePostgres(unittest.TestCase):
    def _clear(self):
        for key in ("PGHOST", "PGDATABASE", "PGUSER", "PGPASSWORD", "LAKEBASE_ENDPOINT"):
            os.environ.pop(key, None)

    def test_requires_endpoint_not_password(self):
        prev = {k: os.environ.get(k) for k in (
            "PGHOST", "PGDATABASE", "PGUSER", "PGPASSWORD", "LAKEBASE_ENDPOINT",
        )}
        try:
            self._clear()
            os.environ["PGHOST"] = "h"
            os.environ["PGDATABASE"] = "db"
            os.environ["PGUSER"] = "u"
            os.environ["PGPASSWORD"] = "static"
            self.assertFalse(uses_app_resource_postgres())
            os.environ["LAKEBASE_ENDPOINT"] = "projects/p/branches/b/endpoints/primary"
            os.environ.pop("PGPASSWORD", None)
            self.assertTrue(uses_app_resource_postgres())
        finally:
            for key, value in prev.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


class TestGenerateConnectionString(unittest.TestCase):
    def test_oauth_url_uses_psycopg2_and_pgdatabase(self):
        endpoint = SimpleNamespace(
            name="projects/p/branches/b/endpoints/primary",
            status=SimpleNamespace(hosts=SimpleNamespace(host="from-sdk.example")),
        )
        cred = SimpleNamespace(token="secret-token")
        w = MagicMock()
        w.postgres.generate_database_credential.return_value = cred
        prev_db = os.environ.get("PGDATABASE")
        try:
            os.environ["PGDATABASE"] = "cv_explorer_db"
            with patch("backend.lakebase._get_workspace_client", return_value=w):
                url = generate_connection_string(endpoint, "sp-uuid")
            self.assertTrue(url.startswith("postgresql+psycopg2://"))
            self.assertIn("from-sdk.example", url)
            self.assertIn("/cv_explorer_db?", url)
            self.assertIn("sp-uuid", url)
            w.postgres.generate_database_credential.assert_called_once_with(
                endpoint=endpoint.name,
            )
        finally:
            if prev_db is None:
                os.environ.pop("PGDATABASE", None)
            else:
                os.environ["PGDATABASE"] = prev_db

    def test_prefers_injected_host_over_endpoint_status(self):
        endpoint = SimpleNamespace(
            name="projects/p/branches/b/endpoints/primary",
            status=SimpleNamespace(hosts=SimpleNamespace(host="sdk-host")),
        )
        w = MagicMock()
        w.postgres.generate_database_credential.return_value = SimpleNamespace(token="t")
        with patch("backend.lakebase._get_workspace_client", return_value=w):
            url = generate_connection_string(
                endpoint, "u", database_name="db", host="pghost.injected", port="5433",
            )
        self.assertIn("pghost.injected:5433/db", url)


class TestAllowSqliteFallback(unittest.TestCase):
    def test_default_false(self):
        prev = os.environ.get("ALLOW_SQLITE_FALLBACK")
        try:
            os.environ.pop("ALLOW_SQLITE_FALLBACK", None)
            self.assertFalse(allow_sqlite_fallback())
            os.environ["ALLOW_SQLITE_FALLBACK"] = "true"
            self.assertTrue(allow_sqlite_fallback())
        finally:
            if prev is None:
                os.environ.pop("ALLOW_SQLITE_FALLBACK", None)
            else:
                os.environ["ALLOW_SQLITE_FALLBACK"] = prev


class TestStartupRefusesSqliteFallback(unittest.TestCase):
    def test_failed_lakebase_raises_without_flag(self):
        import importlib

        from fastapi.testclient import TestClient

        prev = {
            k: os.environ.get(k)
            for k in ("USE_LAKEBASE", "ALLOW_SQLITE_FALLBACK", "DATABASE_URL")
        }
        try:
            os.environ["USE_LAKEBASE"] = "true"
            os.environ.pop("ALLOW_SQLITE_FALLBACK", None)
            os.environ["DATABASE_URL"] = "sqlite:///:memory:"
            import backend.main as main_mod
            importlib.reload(main_mod)
            with (
                patch("backend.lakebase.uses_app_resource_postgres", return_value=False),
                patch("backend.lakebase.init_lakebase", side_effect=RuntimeError("no psycopg")),
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    with TestClient(main_mod.app):
                        pass
            self.assertIn("ALLOW_SQLITE_FALLBACK", str(ctx.exception))
        finally:
            for key, value in prev.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            os.environ["USE_LAKEBASE"] = "false"
            import backend.main as main_mod
            importlib.reload(main_mod)


class TestDepsDelegatesToLakebase(unittest.TestCase):
    def test_get_engine_uses_live_lakebase_engine(self):
        from backend import deps

        live = object()
        deps.configure_db(engine=object(), session_factory=object(), use_lakebase=True)
        try:
            with patch("backend.lakebase.get_engine", return_value=live):
                self.assertIs(deps.get_engine(), live)
        finally:
            deps.configure_db(None, None, False)
