"""Unit tests for bounded Model Serving cold-start retries."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend import inference

try:
    import databricks.sdk  # noqa: F401
except ImportError:
    _HAS_DATABRICKS_SDK = False
else:
    _HAS_DATABRICKS_SDK = True


class _Response:
    def __init__(self, value: dict):
        self.value = value

    def as_dict(self) -> dict:
        return self.value


def _named_exception(name: str, message: str = "failure", **attrs):
    exc_type = type(name, (Exception,), {})
    exc = exc_type(message)
    for key, value in attrs.items():
        setattr(exc, key, value)
    return exc


class TestServingColdStartRetry(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "SERVING_COLD_START_MAX_RETRIES": "3",
                "SERVING_COLD_START_INITIAL_DELAY_S": "1",
                "SERVING_COLD_START_MAX_DELAY_S": "10",
                "SERVING_COLD_START_INTERACTIVE_BUDGET_S": "45",
                "SERVING_COLD_START_JOB_BUDGET_S": "900",
            },
            clear=True,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    @patch("backend.inference.random.uniform", return_value=0.0)
    @patch("backend.inference.time.sleep")
    def test_control_plane_timeout_then_success(self, sleep, _uniform):
        query = MagicMock(
            side_effect=[TimeoutError("endpoint warming"), _Response({"ok": True})]
        )
        workspace = SimpleNamespace(
            serving_endpoints=SimpleNamespace(query=query),
        )

        with patch.object(inference, "_get_workspace_client", return_value=workspace):
            result = inference.query_serving_endpoint(
                "classifier",
                [{"image": "payload"}],
                use_data_plane=False,
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(query.call_count, 2)
        sleep.assert_called_once_with(1.0)

    @patch("backend.inference.random.uniform", return_value=0.0)
    @patch("backend.inference.time.sleep")
    def test_data_plane_timeout_then_success(self, sleep, _uniform):
        query = MagicMock(
            side_effect=[TimeoutError("endpoint warming"), _Response({"ok": True})]
        )
        workspace = SimpleNamespace(
            serving_endpoints_data_plane=SimpleNamespace(query=query),
        )

        with patch.object(inference, "_get_workspace_client", return_value=workspace):
            result = inference.query_serving_endpoint(
                "route-optimized",
                [{"image": "payload"}],
                use_data_plane=True,
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(query.call_count, 2)
        sleep.assert_called_once_with(1.0)

    @patch("backend.inference.random.uniform", return_value=0.0)
    @patch("backend.inference.time.sleep")
    def test_retry_exhaustion_reraises_last_timeout(self, sleep, _uniform):
        policy = inference.RetryPolicy(
            max_retries=2,
            initial_delay_s=1,
            max_delay_s=10,
            budget_s=60,
        )
        errors = [
            TimeoutError("first"),
            TimeoutError("second"),
            TimeoutError("last"),
        ]
        call = MagicMock(side_effect=errors)

        with self.assertRaisesRegex(TimeoutError, "last"):
            inference._query_with_cold_start_retry(
                "slow-endpoint",
                call,
                policy=policy,
            )

        self.assertEqual(call.call_count, 3)
        self.assertEqual(sleep.call_args_list[0].args, (1.0,))
        self.assertEqual(sleep.call_args_list[1].args, (2.0,))

    @patch("backend.inference.random.uniform", return_value=0.0)
    @patch("backend.inference.time.sleep")
    @patch("backend.inference.time.monotonic", side_effect=[100.0, 100.0])
    def test_budget_stops_retry_before_sleep(self, _monotonic, sleep, _uniform):
        policy = inference.RetryPolicy(
            max_retries=3,
            initial_delay_s=5,
            max_delay_s=10,
            budget_s=4,
        )
        call = MagicMock(side_effect=TimeoutError("too slow"))

        with self.assertRaises(TimeoutError):
            inference._query_with_cold_start_retry(
                "slow-endpoint",
                call,
                policy=policy,
            )

        call.assert_called_once_with()
        sleep.assert_not_called()

    def test_job_context_allows_a_larger_budget(self):
        interactive = inference._retry_policy_from_env("interactive")
        job = inference._retry_policy_from_env("job")

        self.assertEqual(interactive.budget_s, 45)
        self.assertEqual(job.budget_s, 900)
        self.assertEqual(inference._serving_sdk_timeouts("interactive"), (30, 10))
        self.assertEqual(inference._serving_sdk_timeouts("job"), (120, 120))

    def test_non_retryable_errors_propagate_immediately(self):
        policy = inference.RetryPolicy(3, 1, 10, 60)
        errors = [
            _named_exception("PermissionDenied"),
            _named_exception("BadRequest"),
            _named_exception("InternalError", status_code=500),
        ]

        for error in errors:
            with self.subTest(error=type(error).__name__):
                call = MagicMock(side_effect=error)
                with patch("backend.inference.time.sleep") as sleep:
                    with self.assertRaises(type(error)):
                        inference._query_with_cold_start_retry(
                            "endpoint",
                            call,
                            policy=policy,
                        )
                call.assert_called_once_with()
                sleep.assert_not_called()

    @patch("backend.inference.time.sleep")
    def test_oauth_unavailable_falls_back_without_retry(self, sleep):
        default_query = MagicMock(
            side_effect=RuntimeError("OAuth tokens are not available")
        )
        sp_query = MagicMock(return_value=_Response({"ok": True}))
        workspace = SimpleNamespace(
            serving_endpoints_data_plane=SimpleNamespace(query=default_query),
        )
        sp_workspace = SimpleNamespace(
            serving_endpoints_data_plane=SimpleNamespace(query=sp_query),
        )

        with (
            patch.object(inference, "_get_workspace_client", return_value=workspace),
            patch.object(
                inference,
                "_get_sp_workspace_client",
                return_value=sp_workspace,
            ),
        ):
            result = inference.query_serving_endpoint(
                "route-optimized",
                [{"image": "payload"}],
                use_data_plane=True,
            )

        self.assertEqual(result, {"ok": True})
        default_query.assert_called_once()
        sp_query.assert_called_once()
        sleep.assert_not_called()

    @patch("backend.inference.random.uniform", return_value=0.0)
    @patch("backend.inference.time.sleep")
    def test_retry_log_excludes_exception_payload(self, _sleep, _uniform):
        sensitive_payload = "A" * 500
        call = MagicMock(
            side_effect=[
                TimeoutError(sensitive_payload),
                {"predictions": []},
            ]
        )
        policy = inference.RetryPolicy(1, 1, 10, 60)

        with self.assertLogs("backend.inference", level="WARNING") as captured:
            inference._query_with_cold_start_retry(
                "safe-endpoint",
                call,
                policy=policy,
            )

        output = "\n".join(captured.output)
        self.assertIn("safe-endpoint", output)
        self.assertIn("1/1", output)
        self.assertNotIn(sensitive_payload, output)

    def test_invalid_environment_values_are_bounded(self):
        with patch.dict(
            os.environ,
            {
                "SERVING_COLD_START_MAX_RETRIES": "-4",
                "SERVING_COLD_START_INITIAL_DELAY_S": "nan",
                "SERVING_COLD_START_MAX_DELAY_S": "9999",
                "SERVING_COLD_START_INTERACTIVE_BUDGET_S": "9999",
            },
            clear=True,
        ):
            policy = inference._retry_policy_from_env("interactive")

        self.assertEqual(policy.max_retries, 0)
        self.assertEqual(policy.initial_delay_s, 5)
        self.assertEqual(policy.max_delay_s, 300)
        self.assertEqual(policy.budget_s, 75)

    @unittest.skipUnless(_HAS_DATABRICKS_SDK, "databricks-sdk not installed")
    def test_dedicated_clients_apply_sdk_timeout_config(self):
        inference._serving_workspace_clients.clear()
        inference._sp_workspace_clients.clear()
        try:
            default_config = object()
            sp_config = object()
            default_client = SimpleNamespace(
                config=SimpleNamespace(auth_type="pat")
            )
            sp_client = SimpleNamespace(
                config=SimpleNamespace(auth_type="oauth-m2m")
            )
            with (
                patch.dict(
                    os.environ,
                    {
                        "DATABRICKS_HOST": "https://example.cloud.databricks.com",
                        "SP_SERVING_CLIENT_ID": "test-client-id",
                        "SP_SERVING_CLIENT_SECRET": "test-client-secret",
                    },
                    clear=True,
                ),
                patch(
                    "databricks.sdk.core.Config",
                    side_effect=[default_config, sp_config],
                ) as config_cls,
                patch(
                    "databricks.sdk.WorkspaceClient",
                    side_effect=[default_client, sp_client],
                ) as client_cls,
            ):
                self.assertIs(
                    inference._get_workspace_client("interactive"),
                    default_client,
                )
                self.assertIs(
                    inference._get_sp_workspace_client("job"),
                    sp_client,
                )

            self.assertEqual(
                config_cls.call_args_list[0].kwargs,
                {"http_timeout_seconds": 30, "retry_timeout_seconds": 10},
            )
            self.assertEqual(
                config_cls.call_args_list[1].kwargs,
                {
                    "host": "https://example.cloud.databricks.com",
                    "client_id": "test-client-id",
                    "client_secret": "test-client-secret",
                    "http_timeout_seconds": 120,
                    "retry_timeout_seconds": 120,
                },
            )
            self.assertEqual(client_cls.call_args_list[0].kwargs, {"config": default_config})
            self.assertEqual(client_cls.call_args_list[1].kwargs, {"config": sp_config})
        finally:
            inference._serving_workspace_clients.clear()
            inference._sp_workspace_clients.clear()


if __name__ == "__main__":
    unittest.main()
