"""Lakebase connection switching, refresh-generation guards, and endpoint selection."""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.tests.conftest import test_client


def _endpoint(name, host, read_write=True):
    endpoint_type = (
        "ENDPOINT_TYPE_READ_WRITE" if read_write else "ENDPOINT_TYPE_READ_ONLY"
    )
    return SimpleNamespace(
        name=name,
        status=SimpleNamespace(
            endpoint_type=endpoint_type,
            hosts=SimpleNamespace(host=host),
        ),
    )


def _url(host, database="keep-db"):
    return f"postgresql+psycopg2://role:tok@{host}:5432/{database}?sslmode=require"


def _engine(label):
    engine = MagicMock(name=label)
    engine.url = label
    engine.connect.return_value.__enter__.return_value = MagicMock()
    return engine


class LakebaseStateTest(unittest.TestCase):
    def setUp(self):
        import backend.lakebase as lakebase

        self.lb = lakebase
        self._snapshot = {
            "_engine": lakebase._engine,
            "_session_factory": lakebase._session_factory,
            "_token_refresh_thread": lakebase._token_refresh_thread,
            "_current_connection_url": lakebase._current_connection_url,
            "_current_endpoint": lakebase._current_endpoint,
            "_current_pg_username": lakebase._current_pg_username,
            "_current_host": lakebase._current_host,
            "_current_port": lakebase._current_port,
            "_current_database": lakebase._current_database,
            "_connection_generation": lakebase._connection_generation,
        }
        self._reset_state()

    def tearDown(self):
        for name, value in self._snapshot.items():
            setattr(self.lb, name, value)

    def _reset_state(self):
        with self.lb._lock:
            self.lb._engine = None
            self.lb._session_factory = None
            self.lb._token_refresh_thread = None
            self.lb._current_connection_url = None
            self.lb._current_endpoint = None
            self.lb._current_pg_username = None
            self.lb._current_host = None
            self.lb._current_port = None
            self.lb._current_database = None
            self.lb._connection_generation = 0

    def _connect(self, endpoint, database=None, **kwargs):
        host = endpoint.status.hosts.host
        engine = _engine(host)

        def fake_generate(
            _endpoint, pg_username=None, database_name=None, host=None, port=None,
        ):
            return _url(
                host or _endpoint.status.hosts.host,
                database_name or "databricks_postgres",
            )

        def build(url):
            self.assertIn(host, url)
            if database:
                self.assertIn(f"/{database}?", url)
            return engine

        with (
            patch("backend.lakebase.generate_connection_string", side_effect=fake_generate),
            patch("backend.lakebase._build_engine", side_effect=build),
            patch("backend.lakebase._get_pg_username", return_value="role"),
        ):
            return self.lb.connect_endpoint(
                endpoint,
                pg_username="role",
                database_name=database,
                start_refresh=kwargs.get("start_refresh", False),
                before_activate=kwargs.get("before_activate"),
            ), engine


class TestEndpointSelection(unittest.TestCase):
    def test_selects_read_write_by_name_not_list_order(self):
        from backend.lakebase import select_read_write_endpoint

        ro = _endpoint("projects/p/branches/production/endpoints/aaa-ro", "ro", False)
        rw_z = _endpoint("projects/p/branches/production/endpoints/z-primary", "z")
        rw_a = _endpoint("projects/p/branches/production/endpoints/a-primary", "a")
        chosen = select_read_write_endpoint([ro, rw_z, rw_a])
        self.assertIs(chosen, rw_a)

    def test_startup_uses_configured_endpoint_env(self):
        from backend.lakebase import get_endpoint

        project = SimpleNamespace(name="projects/cv-explorer")
        endpoint_name = (
            "projects/cv-explorer/branches/production/endpoints/primary"
        )
        expected = SimpleNamespace(name=endpoint_name)
        w = MagicMock()
        w.postgres.get_endpoint.return_value = expected
        with (
            patch("backend.lakebase._get_workspace_client", return_value=w),
            patch.dict(os.environ, {"LAKEBASE_ENDPOINT": endpoint_name}),
        ):
            self.assertIs(get_endpoint(project), expected)
        w.postgres.list_endpoints.assert_not_called()
        w.postgres.get_endpoint.assert_called_once_with(name=endpoint_name)

    def test_auto_provision_false_still_requires_configured_endpoint(self):
        from backend.lakebase import get_endpoint

        project = SimpleNamespace(name="projects/cv-explorer")
        with (
            patch("backend.lakebase._get_workspace_client"),
            patch("backend.lakebase.LAKEBASE_AUTO_PROVISION", False),
            patch.dict(os.environ, {"LAKEBASE_ENDPOINT": ""}, clear=False),
        ):
            os.environ.pop("LAKEBASE_ENDPOINT", None)
            with self.assertRaises(RuntimeError) as ctx:
                get_endpoint(project)
        self.assertIn("LAKEBASE_ENDPOINT is required", str(ctx.exception))

    def test_get_endpoint_uses_branch_marked_default(self):
        from backend.lakebase import get_endpoint

        project = SimpleNamespace(name="projects/cv-explorer", status=None, spec=None)
        feature = SimpleNamespace(
            name="projects/cv-explorer/branches/feature",
            status=SimpleNamespace(default=False),
        )
        mainline = SimpleNamespace(
            name="projects/cv-explorer/branches/mainline",
            status=SimpleNamespace(default=True),
        )
        rw = _endpoint(mainline.name + "/endpoints/primary", "mainline-host")
        w = MagicMock()
        w.postgres.list_branches.return_value = [feature, mainline]
        w.postgres.list_endpoints.return_value = [rw]
        with (
            patch("backend.lakebase._get_workspace_client", return_value=w),
            patch("backend.lakebase.LAKEBASE_AUTO_PROVISION", True),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("LAKEBASE_ENDPOINT", None)
            chosen = get_endpoint(project)
        self.assertIs(chosen, rw)
        w.postgres.list_endpoints.assert_called_once_with(parent=mainline.name)

    def test_get_endpoint_uses_project_default_branch(self):
        from backend.lakebase import get_endpoint

        project = SimpleNamespace(
            name="projects/cv-explorer",
            status=SimpleNamespace(default_branch="production"),
        )
        production_rw = _endpoint(
            "projects/cv-explorer/branches/production/endpoints/primary",
            "prod-host",
        )
        feature_rw = _endpoint(
            "projects/cv-explorer/branches/feature/endpoints/primary",
            "feature-host",
        )
        w = MagicMock()

        def list_endpoints(parent):
            if parent.endswith("/feature"):
                return [feature_rw]
            if parent.endswith("/production"):
                return [
                    _endpoint(parent + "/endpoints/ro", "ro", False),
                    production_rw,
                ]
            return []

        w.postgres.list_branches.return_value = []
        w.postgres.list_endpoints.side_effect = list_endpoints
        with (
            patch("backend.lakebase._get_workspace_client", return_value=w),
            patch("backend.lakebase.LAKEBASE_AUTO_PROVISION", True),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("LAKEBASE_ENDPOINT", None)
            self.assertIs(get_endpoint(project), production_rw)
        w.postgres.list_endpoints.assert_called_with(
            parent="projects/cv-explorer/branches/production"
        )

    def test_get_endpoint_uses_explicit_branch(self):
        from backend.lakebase import get_endpoint

        project = SimpleNamespace(name="projects/cv-explorer", status=None, spec=None)
        feature_rw = _endpoint(
            "projects/cv-explorer/branches/dev/endpoints/primary",
            "dev-host",
        )
        w = MagicMock()
        w.postgres.list_endpoints.return_value = [
            _endpoint(
                "projects/cv-explorer/branches/dev/endpoints/zzz",
                "zzz",
            ),
            feature_rw,
        ]
        with (
            patch("backend.lakebase._get_workspace_client", return_value=w),
            patch("backend.lakebase.LAKEBASE_AUTO_PROVISION", True),
        ):
            chosen = get_endpoint(project, branch_name="dev")
        self.assertEqual(chosen.name, feature_rw.name)
        w.postgres.list_endpoints.assert_called_once_with(
            parent="projects/cv-explorer/branches/dev"
        )

    def test_explicit_branch_ignores_configured_endpoint_env(self):
        from backend.lakebase import get_endpoint

        project = SimpleNamespace(name="projects/cv-explorer", status=None, spec=None)
        branch_rw = _endpoint(
            "projects/cv-explorer/branches/dev/endpoints/primary",
            "dev-host",
        )
        w = MagicMock()
        w.postgres.list_endpoints.return_value = [branch_rw]
        with (
            patch("backend.lakebase._get_workspace_client", return_value=w),
            patch("backend.lakebase.LAKEBASE_AUTO_PROVISION", True),
            patch.dict(
                os.environ,
                {
                    "LAKEBASE_ENDPOINT": (
                        "projects/cv-explorer/branches/production/endpoints/primary"
                    )
                },
            ),
        ):
            chosen = get_endpoint(project, branch_name="dev")
        self.assertIs(chosen, branch_rw)
        w.postgres.get_endpoint.assert_not_called()

    def test_rejects_branch_from_another_project(self):
        from backend.lakebase import resolve_branch_name

        project = SimpleNamespace(name="projects/cv-explorer")
        with self.assertRaises(RuntimeError) as ctx:
            resolve_branch_name(
                project, "projects/other/branches/production"
            )
        self.assertIn("does not belong", str(ctx.exception))


class TestConnectionSwitch(LakebaseStateTest):
    def test_requests_use_new_connection_after_switch(self):
        from backend import deps

        first_ep = _endpoint("projects/p/branches/production/endpoints/a", "host-a")
        second_ep = _endpoint("projects/p/branches/production/endpoints/b", "host-b")
        self._connect(first_ep, database="keep-db")
        deps.configure_db(engine=object(), session_factory=object(), use_lakebase=True)
        try:
            self.assertEqual(self.lb.get_engine().url, "host-a")
            self._connect(second_ep)
            self.assertEqual(self.lb.get_engine().url, "host-b")
            self.assertIs(deps.get_engine(), self.lb.get_engine())
            self.assertIn("/keep-db?", self.lb._current_connection_url)
            self.assertEqual(self.lb.connection_generation(), 2)
        finally:
            deps.configure_db(None, None, False)

    def test_old_refresh_cannot_restore_previous_connection(self):
        first_ep = _endpoint("projects/p/branches/production/endpoints/a", "host-a")
        second_ep = _endpoint("projects/p/branches/production/endpoints/b", "host-b")
        self._connect(first_ep, database="keep-db")
        stale_generation = self.lb.connection_generation()
        first_engine = self.lb.get_engine()
        self._connect(second_ep)
        second_engine = self.lb.get_engine()

        stale_engine = _engine("stale-refresh")
        with (
            patch(
                "backend.lakebase.generate_connection_string",
                return_value=_url("host-a"),
            ),
            patch("backend.lakebase._build_engine", return_value=stale_engine),
        ):
            swapped = self.lb._refresh_once(
                first_ep, "role", stale_generation, host="host-a",
                port="5432", database_name="keep-db",
            )
        self.assertFalse(swapped)
        self.assertIs(self.lb.get_engine(), second_engine)
        stale_engine.dispose.assert_called()
        self.assertIsNot(self.lb.get_engine(), first_engine)

    def test_switch_during_in_flight_refresh(self):
        first_ep = _endpoint("projects/p/branches/production/endpoints/a", "host-a")
        second_ep = _endpoint("projects/p/branches/production/endpoints/b", "host-b")
        first_engine = _engine("host-a")
        live_engine = _engine("host-b")
        stale_engine = _engine("stale-refresh")
        self.lb._activate_connection(
            first_engine,
            _url("host-a"),
            first_ep,
            "role",
            "host-a",
            "5432",
            "keep-db",
            start_refresh=False,
        )
        stale_generation = self.lb.connection_generation()

        entered = threading.Event()
        release = threading.Event()
        results = []

        def generate(endpoint, pg_username=None, database_name=None, host=None, port=None):
            resolved = host or endpoint.status.hosts.host
            if resolved == "host-a":
                entered.set()
                self.assertTrue(release.wait(timeout=2))
                return _url("host-a")
            return _url(resolved, database_name or "keep-db")

        def build(url):
            if "host-b" in url:
                return live_engine
            return stale_engine

        def run_refresh():
            try:
                results.append(
                    self.lb._refresh_once(
                        first_ep,
                        "role",
                        stale_generation,
                        host="host-a",
                        port="5432",
                        database_name="keep-db",
                    )
                )
            except Exception as exc:  # pragma: no cover - test helper
                results.append(exc)

        with (
            patch("backend.lakebase.generate_connection_string", side_effect=generate),
            patch("backend.lakebase._build_engine", side_effect=build),
        ):
            worker = threading.Thread(target=run_refresh)
            worker.start()
            self.assertTrue(entered.wait(timeout=2))
            self.lb.connect_endpoint(
                second_ep,
                pg_username="role",
                start_refresh=False,
            )
            live = self.lb.get_engine()
            release.set()
            worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [False])
        self.assertIs(live, live_engine)
        self.assertIs(self.lb.get_engine(), live_engine)
        stale_engine.dispose.assert_called()

    def test_repeated_switches_keep_latest_connection(self):
        engines = []
        for host in ("host-a", "host-b", "host-c"):
            ep = _endpoint(f"projects/p/branches/production/endpoints/{host}", host)
            _, engine = self._connect(ep, database="keep-db")
            engines.append(engine)
        self.assertIs(self.lb.get_engine(), engines[-1])
        self.assertEqual(self.lb.connection_generation(), 3)
        self.assertEqual(self.lb._current_host, "host-c")
        engines[0].dispose.assert_called()
        engines[1].dispose.assert_called()

    def test_failed_validation_leaves_existing_connection(self):
        first_ep = _endpoint("projects/p/branches/production/endpoints/a", "host-a")
        second_ep = _endpoint("projects/p/branches/production/endpoints/b", "host-b")
        _, first_engine = self._connect(first_ep, database="keep-db")
        generation = self.lb.connection_generation()

        bad = _engine("bad")
        bad.connect.side_effect = RuntimeError("unreachable")
        prepared = []
        with (
            patch("backend.lakebase.generate_connection_string", return_value=_url("host-b")),
            patch("backend.lakebase._build_engine", return_value=bad),
        ):
            with self.assertRaises(RuntimeError):
                self.lb.connect_endpoint(
                    second_ep,
                    pg_username="role",
                    start_refresh=False,
                    before_activate=lambda engine: prepared.append(engine),
                )
        self.assertIs(self.lb.get_engine(), first_engine)
        self.assertEqual(self.lb.connection_generation(), generation)
        self.assertEqual(prepared, [])
        bad.dispose.assert_called()
        first_engine.dispose.assert_not_called()

    def test_stale_refresh_loop_exits_before_generating(self):
        first_ep = _endpoint("projects/p/branches/production/endpoints/a", "host-a")
        self._connect(first_ep, database="keep-db")
        stale_generation = self.lb.connection_generation()
        self.lb._connection_generation = stale_generation + 1
        with (
            patch("backend.lakebase.generate_connection_string") as generate,
            patch("backend.lakebase.time.sleep"),
        ):
            self.lb._refresh_token_loop(
                first_ep, "role", stale_generation,
                host="host-a", port="5432", database_name="keep-db",
            )
        generate.assert_not_called()

    def test_refresh_uses_selected_host_not_pghost_env(self):
        ep = _endpoint("projects/p/branches/production/endpoints/a", "selected-host")
        self._connect(ep, database="keep-db")
        generation = self.lb.connection_generation()
        refreshed = _engine("refreshed")
        captured = {}

        def generate(endpoint, pg_username, database_name=None, host=None, port=None):
            captured["host"] = host
            captured["database"] = database_name
            captured["user"] = pg_username
            return _url(host, database_name)

        with (
            patch("backend.lakebase.generate_connection_string", side_effect=generate),
            patch("backend.lakebase._build_engine", return_value=refreshed),
            patch.dict(os.environ, {"PGHOST": "injected-app-host", "PGDATABASE": "other-db"}),
        ):
            self.assertTrue(
                self.lb._refresh_once(
                    ep,
                    self.lb._current_pg_username,
                    generation,
                    host=self.lb._current_host,
                    port=self.lb._current_port,
                    database_name=self.lb._current_database,
                )
            )
        self.assertEqual(captured["host"], "selected-host")
        self.assertEqual(captured["database"], "keep-db")
        self.assertEqual(captured["user"], "role")
        self.assertIs(self.lb.get_engine(), refreshed)


class TestAdminConnectLakebase(unittest.TestCase):
    def test_connect_route_centralizes_switch_and_keeps_existing_on_failure(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        endpoint = _endpoint(
            "projects/cv-explorer/branches/production/endpoints/primary",
            "prod-host",
        )
        project = SimpleNamespace(name="projects/cv-explorer")
        w = MagicMock()
        w.postgres.get_project.return_value = project

        with patch.dict("os.environ", {"ADMIN_EMAILS": "admin@databricks.com"}):
            with test_client(Path(temp_dir.name)) as (client, _main, _tmp):
                with (
                    patch(
                        "backend.routes.admin._get_workspace_client",
                        return_value=w,
                    ),
                    patch(
                        "backend.lakebase.get_endpoint",
                        return_value=endpoint,
                    ) as get_ep,
                    patch("backend.lakebase.connect_endpoint") as connect,
                    patch("backend.lakebase.prepare_lakebase_schema"),
                    patch("backend.lakebase.setup_replica_identity"),
                    patch(
                        "backend.lakebase.get_engine",
                        return_value=_engine("live"),
                    ),
                    patch(
                        "backend.lakebase.get_session_factory",
                        return_value=MagicMock(),
                    ),
                ):
                    live = _engine("live")
                    connect.return_value = live
                    response = client.post(
                        "/api/admin/connect-lakebase",
                        json={"project_id": "cv-explorer", "branch_id": "production"},
                        headers={"X-Forwarded-Email": "admin@databricks.com"},
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    body = response.json()
                    self.assertEqual(body["status"], "connected")
                    self.assertEqual(body["host"], "prod-host")
                    get_ep.assert_called_once()
                    self.assertEqual(
                        get_ep.call_args.kwargs["branch_name"],
                        "projects/cv-explorer/branches/production",
                    )
                    connect.assert_called_once()
                    self.assertIsNotNone(connect.call_args.kwargs["before_activate"])

                    connect.side_effect = RuntimeError("validation failed")
                    failed = client.post(
                        "/api/admin/connect-lakebase",
                        json={"project_id": "cv-explorer"},
                        headers={"X-Forwarded-Email": "admin@databricks.com"},
                    )
                    self.assertEqual(failed.status_code, 500)


if __name__ == "__main__":
    unittest.main()
