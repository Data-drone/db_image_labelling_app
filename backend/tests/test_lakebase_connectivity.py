"""Unit tests for Lakebase URL construction, App-resource detection, and SQLite fallback."""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.lakebase import (
    allow_sqlite_fallback,
    app_postgres_username,
    build_psycopg2_url,
    generate_connection_string,
    init_lakebase_from_app_resource,
    postgres_database_name,
    require_complete_app_postgres_env,
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

    def test_partial_pg_env_without_endpoint_is_error(self):
        prev = {k: os.environ.get(k) for k in (
            "PGHOST", "PGDATABASE", "PGUSER", "LAKEBASE_ENDPOINT",
        )}
        try:
            self._clear()
            os.environ["PGHOST"] = "h"
            os.environ["PGDATABASE"] = "db"
            os.environ["PGUSER"] = "u"
            with self.assertRaises(RuntimeError) as ctx:
                require_complete_app_postgres_env()
            self.assertIn("LAKEBASE_ENDPOINT", str(ctx.exception))
            self.assertIn("valueFrom", str(ctx.exception))
        finally:
            for key, value in prev.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_empty_env_allows_sdk_path(self):
        prev = {k: os.environ.get(k) for k in (
            "PGHOST", "PGDATABASE", "PGUSER", "LAKEBASE_ENDPOINT",
        )}
        try:
            self._clear()
            require_complete_app_postgres_env()
        finally:
            for key, value in prev.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


class TestAppPostgresUsername(unittest.TestCase):
    def test_uses_injected_pguser(self):
        prev = os.environ.get("PGUSER")
        try:
            os.environ["PGUSER"] = "app-sp-client-id"
            self.assertEqual(app_postgres_username(), "app-sp-client-id")
        finally:
            if prev is None:
                os.environ.pop("PGUSER", None)
            else:
                os.environ["PGUSER"] = prev


class TestInitAppResourceUsesPguser(unittest.TestCase):
    def test_oauth_password_with_pguser_role(self):
        prev = {k: os.environ.get(k) for k in (
            "PGHOST", "PGDATABASE", "PGUSER", "PGPORT", "LAKEBASE_ENDPOINT",
        )}
        endpoint = SimpleNamespace(
            name="projects/p/branches/b/endpoints/primary",
            status=SimpleNamespace(hosts=SimpleNamespace(host="sdk-host")),
        )
        w = MagicMock()
        w.postgres.get_endpoint.return_value = endpoint
        w.postgres.generate_database_credential.return_value = SimpleNamespace(
            token="oauth-token",
        )
        try:
            os.environ["PGHOST"] = "injected-host"
            os.environ["PGDATABASE"] = "app-db"
            os.environ["PGUSER"] = "injected-pguser"
            os.environ["PGPORT"] = "5432"
            os.environ["LAKEBASE_ENDPOINT"] = endpoint.name
            with (
                patch("backend.lakebase._get_workspace_client", return_value=w),
                patch("backend.lakebase._build_engine") as build,
                patch("backend.lakebase.sessionmaker"),
                patch("backend.lakebase.threading.Thread"),
            ):
                fake_engine = MagicMock()
                fake_engine.connect.return_value.__enter__.return_value = MagicMock()
                build.return_value = fake_engine
                init_lakebase_from_app_resource()
                url = build.call_args[0][0]
            self.assertIn("injected-pguser", url)
            self.assertIn("injected-host", url)
            self.assertIn("/app-db?", url)
            self.assertIn("oauth-token", url)
            w.postgres.generate_database_credential.assert_called_once_with(
                endpoint=endpoint.name,
            )
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
                patch("backend.lakebase.require_complete_app_postgres_env"),
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
