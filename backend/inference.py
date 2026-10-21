"""
Model Serving integration for pre-annotation.

Queries Databricks Model Serving endpoints with image data and parses
responses into the app's annotation format. Supports both classification
and detection (bounding box) models.
"""

import base64
import logging
import math
import os
import random
import time
from dataclasses import dataclass
from typing import Callable, Literal, Optional, TypeVar

log = logging.getLogger(__name__)

RetryContext = Literal["interactive", "job"]
_T = TypeVar("_T")

DEFAULT_ENDPOINT_CONFIG = {
    "label_key": "label",
    "confidence_key": "confidence",
    "bbox_key": "bbox",
    "min_confidence": 0.5,
}

_DEFAULT_MAX_RETRIES = 3
_DEFAULT_INITIAL_DELAY_S = 5.0
_DEFAULT_MAX_DELAY_S = 60.0
_DEFAULT_INTERACTIVE_BUDGET_S = 45.0
_DEFAULT_JOB_BUDGET_S = 900.0
# The final attempt may start at the budget edge and still run for the
# interactive HTTP timeout plus SDK retry window (30s + 10s). Keep the total
# under the 120-second Databricks Apps proxy limit.
_MAX_INTERACTIVE_BUDGET_S = 75.0
_MAX_JOB_BUDGET_S = 3600.0


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int
    initial_delay_s: float
    max_delay_s: float
    budget_s: float


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        log.warning("Ignoring invalid %s=%r; using %s", name, raw, default)
        return default
    if value < minimum:
        log.warning("Clamping %s to %s", name, minimum)
        return minimum
    if value > maximum:
        log.warning("Clamping %s to %s", name, maximum)
        return maximum
    return value


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        log.warning("Ignoring invalid %s=%r; using %s", name, raw, default)
        return default
    if not math.isfinite(value):
        log.warning("Ignoring non-finite %s=%r; using %s", name, raw, default)
        return default
    if value < minimum:
        log.warning("Clamping %s to %s", name, minimum)
        return minimum
    if value > maximum:
        log.warning("Clamping %s to %s", name, maximum)
        return maximum
    return value


def _retry_policy_from_env(context: RetryContext = "interactive") -> RetryPolicy:
    """Build a bounded retry policy from non-secret runtime configuration."""
    if context not in ("interactive", "job"):
        raise ValueError(f"Unknown serving retry context: {context!r}")

    budget_name = (
        "SERVING_COLD_START_JOB_BUDGET_S"
        if context == "job"
        else "SERVING_COLD_START_INTERACTIVE_BUDGET_S"
    )
    budget_default = (
        _DEFAULT_JOB_BUDGET_S
        if context == "job"
        else _DEFAULT_INTERACTIVE_BUDGET_S
    )
    budget_max = (
        _MAX_JOB_BUDGET_S if context == "job" else _MAX_INTERACTIVE_BUDGET_S
    )

    initial_delay = _env_float(
        "SERVING_COLD_START_INITIAL_DELAY_S",
        _DEFAULT_INITIAL_DELAY_S,
        minimum=0.0,
        maximum=300.0,
    )
    max_delay = _env_float(
        "SERVING_COLD_START_MAX_DELAY_S",
        _DEFAULT_MAX_DELAY_S,
        minimum=0.0,
        maximum=300.0,
    )
    if max_delay < initial_delay:
        log.warning(
            "SERVING_COLD_START_MAX_DELAY_S is below the initial delay; "
            "using the initial delay"
        )
        max_delay = initial_delay

    return RetryPolicy(
        max_retries=_env_int(
            "SERVING_COLD_START_MAX_RETRIES",
            _DEFAULT_MAX_RETRIES,
            minimum=0,
            maximum=10,
        ),
        initial_delay_s=initial_delay,
        max_delay_s=max_delay,
        budget_s=_env_float(
            budget_name,
            budget_default,
            minimum=0.0,
            maximum=budget_max,
        ),
    )


def _exception_chain(exc: BaseException):
    """Yield an exception and its explicit/implicit causes without looping."""
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _is_cold_start_timeout(exc: BaseException) -> bool:
    """Return whether *exc* is a narrowly recognized transient timeout."""
    for current in _exception_chain(exc):
        if isinstance(current, TimeoutError):
            return True

        cls = type(current)
        cls_name = cls.__name__
        module_name = cls.__module__
        if module_name.startswith("requests.") and cls_name in {
            "Timeout",
            "ConnectTimeout",
            "ReadTimeout",
            "ConnectionError",
        }:
            return True
        if cls_name in {"DeadlineExceeded", "TemporarilyUnavailable"}:
            return True

        status_code = getattr(current, "status_code", None)
        if status_code is None:
            response = getattr(current, "response", None)
            status_code = getattr(response, "status_code", None)
        try:
            if int(status_code) in (503, 504):
                return True
        except (TypeError, ValueError):
            pass
    return False


def _query_with_cold_start_retry(
    endpoint_name: str,
    call: Callable[[], _T],
    *,
    policy: RetryPolicy,
) -> _T:
    """Run one serving query with bounded retries for cold-start timeouts."""
    started = time.monotonic()
    retries_used = 0

    while True:
        try:
            return call()
        except Exception as exc:
            if not _is_cold_start_timeout(exc) or retries_used >= policy.max_retries:
                raise

            base_delay = min(
                policy.initial_delay_s * (2 ** retries_used),
                policy.max_delay_s,
            )
            delay = min(
                policy.max_delay_s,
                base_delay + random.uniform(0.0, base_delay * 0.2),
            )
            elapsed = time.monotonic() - started
            if elapsed + delay > policy.budget_s:
                raise

            retries_used += 1
            log.warning(
                "Serving endpoint %s cold-start retry %d/%d in %.2fs (%s)",
                endpoint_name,
                retries_used,
                policy.max_retries,
                delay,
                type(exc).__name__,
            )
            time.sleep(delay)


_serving_workspace_clients: dict[RetryContext, object] = {}


def _serving_sdk_timeouts(context: RetryContext) -> tuple[int, int]:
    """Return HTTP and SDK-internal retry timeouts for serving calls."""
    if context == "job":
        return 120, 120
    return 30, 10


def _get_workspace_client(retry_context: RetryContext = "interactive"):
    """Get a serving-only client whose SDK retries fit the outer retry budget.

    The SDK defaults to a five-minute internal retry window. A scale-to-zero
    endpoint can consume that entire window before our cold-start helper sees
    the timeout, so serving uses dedicated clients with shorter bounds.
    """
    client = _serving_workspace_clients.get(retry_context)
    if client is not None:
        return client

    from databricks.sdk import WorkspaceClient
    from databricks.sdk.core import Config

    http_timeout_s, sdk_retry_timeout_s = _serving_sdk_timeouts(retry_context)
    client = WorkspaceClient(config=Config(
        http_timeout_seconds=http_timeout_s,
        retry_timeout_seconds=sdk_retry_timeout_s,
    ))
    _serving_workspace_clients[retry_context] = client
    return client


def get_default_endpoint() -> Optional[str]:
    """Return the workspace-level default serving endpoint from env, if set."""
    return os.environ.get("SERVING_ENDPOINT") or None


def resolve_endpoint(project) -> Optional[str]:
    """Resolve the serving endpoint for a project.

    Priority: project-level setting > env var default > None.
    """
    return project.serving_endpoint or get_default_endpoint()


def _merge_config(endpoint_config: Optional[dict]) -> dict:
    merged = dict(DEFAULT_ENDPOINT_CONFIG)
    if endpoint_config:
        merged.update(endpoint_config)
    return merged


def _truthy_config(val) -> Optional[bool]:
    if val is True:
        return True
    if val is False:
        return False
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off"):
            return False
    return None


def resolve_use_data_plane(endpoint_config: Optional[dict]) -> bool:
    """Use ``serving_endpoints_data_plane.query`` (route-optimized / OAuth dataplane).

    Project ``endpoint_config["use_data_plane"]`` overrides when set to a boolean-like
    value; otherwise defaults to **True** (all new Databricks endpoints are
    route-optimized and require the data-plane client). Set the env var
    ``USE_SERVING_DATA_PLANE=false`` or project config ``use_data_plane: false``
    to force the legacy control-plane path.
    """
    cfg = endpoint_config or {}
    explicit = _truthy_config(cfg.get("use_data_plane"))
    if explicit is not None:
        return explicit
    for key in ("USE_SERVING_DATA_PLANE", "SERVING_ROUTE_OPTIMIZED"):
        v = os.environ.get(key, "")
        t = _truthy_config(v)
        if t is not None:
            return t
    return True


_sp_workspace_clients: dict[RetryContext, object] = {}


def _get_sp_workspace_client(retry_context: RetryContext = "interactive"):
    """Create a WorkspaceClient using injected SP OAuth credentials (M2M).

    Used when the default client (unified auth on serverless) can't access
    route-optimized serving endpoints that require OAuth data-plane tokens.
    """
    client = _sp_workspace_clients.get(retry_context)
    if client is not None:
        return client

    from databricks.sdk import WorkspaceClient
    from databricks.sdk.core import Config

    sp_id = os.environ.get("SP_SERVING_CLIENT_ID", "")
    sp_secret = os.environ.get("SP_SERVING_CLIENT_SECRET", "")
    if not sp_id or not sp_secret:
        return None

    host = (
        os.environ.get("DATABRICKS_HOST")
        or _get_workspace_client(retry_context).config.host
    )
    http_timeout_s, sdk_retry_timeout_s = _serving_sdk_timeouts(retry_context)
    client = WorkspaceClient(config=Config(
        host=host,
        client_id=sp_id,
        client_secret=sp_secret,
        http_timeout_seconds=http_timeout_s,
        retry_timeout_seconds=sdk_retry_timeout_s,
    ))
    log.info("Created SP OAuth WorkspaceClient for data-plane serving (auth_type=%s)",
             client.config.auth_type)
    _sp_workspace_clients[retry_context] = client
    return client


def query_serving_endpoint(
    endpoint_name: str,
    dataframe_records: list,
    *,
    use_data_plane: bool,
    retry_context: RetryContext = "interactive",
) -> dict:
    """Query a serving endpoint with automatic fallback for route-optimized endpoints.

    Tries: SDK data-plane OAuth → SP OAuth data-plane → SDK control plane.
    """
    policy = _retry_policy_from_env(retry_context)
    w = _get_workspace_client(retry_context)

    if use_data_plane:
        dp = getattr(w, "serving_endpoints_data_plane", None)
        if dp is not None:
            try:
                resp = _query_with_cold_start_retry(
                    endpoint_name,
                    lambda: dp.query(
                        name=endpoint_name,
                        dataframe_records=dataframe_records,
                    ),
                    policy=policy,
                )
                return resp.as_dict() if hasattr(resp, "as_dict") else resp
            except Exception as e:
                if "OAuth tokens are not available" not in str(e):
                    raise
                log.info("Data-plane OAuth unavailable on default client")

        sp_w = _get_sp_workspace_client(retry_context)
        if sp_w is not None:
            sp_dp = getattr(sp_w, "serving_endpoints_data_plane", None)
            if sp_dp is not None:
                log.info("Trying SP OAuth data-plane for %s", endpoint_name)
                resp = _query_with_cold_start_retry(
                    endpoint_name,
                    lambda: sp_dp.query(
                        name=endpoint_name,
                        dataframe_records=dataframe_records,
                    ),
                    policy=policy,
                )
                return resp.as_dict() if hasattr(resp, "as_dict") else resp

        raise RuntimeError(
            f"Endpoint '{endpoint_name}' requires data-plane OAuth but no valid OAuth "
            f"client is available. Ensure SP_SERVING_CLIENT_ID / SP_SERVING_CLIENT_SECRET "
            f"are set, or that the runtime supports data-plane tokens."
        )

    resp = _query_with_cold_start_retry(
        endpoint_name,
        lambda: w.serving_endpoints.query(
            name=endpoint_name,
            dataframe_records=dataframe_records,
        ),
        policy=policy,
    )
    return resp.as_dict() if hasattr(resp, "as_dict") else resp


def check_endpoint_health(endpoint_name: str) -> dict:
    """Check if a serving endpoint exists and is ready.

    Returns {"status": "ready"|"not_ready"|"not_found"|"error", ...}.
    """
    try:
        w = _get_workspace_client()
        ep = w.serving_endpoints.get(name=endpoint_name)
        state_str = "unknown"
        if ep.state and ep.state.ready:
            state_str = str(ep.state.ready)
        if "READY" in state_str.upper():
            return {"status": "ready", "endpoint": endpoint_name, "state": state_str}
        return {"status": "not_ready", "endpoint": endpoint_name, "state": state_str}
    except Exception as e:
        err_str = str(e)
        if "RESOURCE_DOES_NOT_EXIST" in err_str or "404" in err_str:
            return {"status": "not_found", "endpoint": endpoint_name, "error": "Endpoint not found"}
        if "PERMISSION_DENIED" in err_str or "403" in err_str:
            return {"status": "error", "endpoint": endpoint_name, "error": "Permission denied"}
        return {"status": "error", "endpoint": endpoint_name, "error": err_str}


def query_endpoint(
    endpoint_name: str,
    image_bytes: bytes,
    *,
    use_data_plane: bool = False,
    image_field: str = "image",
    extra_record_fields: Optional[dict] = None,
    retry_context: RetryContext = "interactive",
) -> dict:
    """Send an image to a serving endpoint and return the raw prediction.

    Sends base64 image in ``dataframe_records`` (default field name ``image``).
    Set ``use_data_plane=True`` for route-optimized endpoints (OAuth dataplane).
    ``extra_record_fields`` are merged into the single request record (e.g. SAM text prompts).
    """
    b64 = base64.b64encode(image_bytes).decode("ascii")
    record: dict = {image_field: b64}
    if extra_record_fields:
        record = {**extra_record_fields, **record}
    return query_serving_endpoint(
        endpoint_name,
        [record],
        use_data_plane=use_data_plane,
        retry_context=retry_context,
    )


def parse_classification_response(
    raw: dict,
    class_list: list[str],
    config: Optional[dict] = None,
) -> list[dict]:
    """Parse a classification endpoint response into AnnotationCreate-compatible dicts.

    Handles common response shapes:
      {"predictions": ["cat"]}
      {"predictions": [{"label": "cat", "confidence": 0.9}]}
    """
    cfg = _merge_config(config)
    label_key = cfg["label_key"]
    confidence_key = cfg["confidence_key"]
    min_conf = float(cfg["min_confidence"])

    predictions = raw.get("predictions", [])
    if not predictions:
        return []

    pred = predictions[0]

    if isinstance(pred, str):
        if pred in class_list:
            return [{"label": pred, "ann_type": "classification", "confidence": None}]
        return []

    if isinstance(pred, dict):
        label = pred.get(label_key, "")
        confidence = pred.get(confidence_key)
        if confidence is not None and float(confidence) < min_conf:
            return []
        if label and label in class_list:
            return [{"label": label, "ann_type": "classification", "confidence": confidence}]
        if label:
            return [{"label": label, "ann_type": "classification", "confidence": confidence}]
        return []

    if isinstance(pred, (int, float)):
        idx = int(pred)
        if 0 <= idx < len(class_list):
            return [{"label": class_list[idx], "ann_type": "classification", "confidence": None}]
        return []

    return []


def parse_detection_response(
    raw: dict,
    class_list: list[str],
    config: Optional[dict] = None,
) -> list[dict]:
    """Parse a detection endpoint response into AnnotationCreate-compatible dicts.

    Handles common response shapes:
      {"predictions": [[{"label": "cat", "confidence": 0.87, "bbox": [x, y, w, h]}]]}
      {"predictions": [{"labels": [...], "boxes": [...], "scores": [...]}]}
    """
    cfg = _merge_config(config)
    label_key = cfg["label_key"]
    confidence_key = cfg["confidence_key"]
    bbox_key = cfg["bbox_key"]
    min_conf = float(cfg["min_confidence"])

    predictions = raw.get("predictions", [])
    if not predictions:
        return []

    pred = predictions[0]
    annotations = []

    if isinstance(pred, list):
        for det in pred:
            if not isinstance(det, dict):
                continue
            label = det.get(label_key, "")
            confidence = det.get(confidence_key)
            bbox = det.get(bbox_key)
            if confidence is not None and float(confidence) < min_conf:
                continue
            if not bbox or not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
                continue
            annotations.append({
                "label": label,
                "ann_type": "bbox",
                "bbox_json": {"x": bbox[0], "y": bbox[1], "w": bbox[2], "h": bbox[3]},
                "confidence": confidence,
            })

    elif isinstance(pred, dict):
        labels = pred.get("labels", pred.get(label_key + "s", []))
        boxes = pred.get("boxes", pred.get(bbox_key + "es", pred.get(bbox_key + "s", [])))
        scores = pred.get("scores", pred.get(confidence_key + "s", []))

        for i, (lbl, box) in enumerate(zip(labels, boxes)):
            conf = scores[i] if i < len(scores) else None
            if conf is not None and float(conf) < min_conf:
                continue
            if not isinstance(box, (list, tuple)) or len(box) < 4:
                continue
            annotations.append({
                "label": lbl,
                "ann_type": "bbox",
                "bbox_json": {"x": box[0], "y": box[1], "w": box[2], "h": box[3]},
                "confidence": conf,
            })

    return annotations


def predict_sample(
    endpoint_name: str,
    image_bytes: bytes,
    task_type: str,
    class_list: list[str],
    endpoint_config: Optional[dict] = None,
    *,
    retry_context: RetryContext = "interactive",
) -> list[dict]:
    """End-to-end: query endpoint and parse response for a single image.

    Returns a list of annotation dicts (may be empty on error or low confidence).
    Dispatches to a pluggable :class:`~backend.inference_adapters.InferenceAdapter`
    (default ``generic`` — same behavior as before this refactor).
    """
    # Lazy import avoids circular imports (adapters may call back into this module).
    from .inference_adapters import get_adapter_for_config

    adapter = get_adapter_for_config(endpoint_config)
    return adapter.query_and_parse(
        endpoint_name,
        image_bytes,
        task_type,
        class_list,
        endpoint_config,
        retry_context=retry_context,
    )
