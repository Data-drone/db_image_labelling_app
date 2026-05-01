"""
Model Serving integration for pre-annotation.

Queries Databricks Model Serving endpoints with image data and parses
responses into the app's annotation format. Supports both classification
and detection (bounding box) models.
"""

import base64
import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_ENDPOINT_CONFIG = {
    "label_key": "label",
    "confidence_key": "confidence",
    "bbox_key": "bbox",
    "min_confidence": 0.5,
}


def _get_workspace_client():
    from .volumes import _get_workspace_client as _gwc
    return _gwc()


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


_sp_workspace_client = None


def _get_sp_workspace_client():
    """Create a WorkspaceClient using injected SP OAuth credentials (M2M).

    Used when the default client (unified auth on serverless) can't access
    route-optimized serving endpoints that require OAuth data-plane tokens.
    """
    global _sp_workspace_client
    if _sp_workspace_client is not None:
        return _sp_workspace_client

    from databricks.sdk import WorkspaceClient

    sp_id = os.environ.get("SP_SERVING_CLIENT_ID", "")
    sp_secret = os.environ.get("SP_SERVING_CLIENT_SECRET", "")
    if not sp_id or not sp_secret:
        return None

    host = os.environ.get("DATABRICKS_HOST") or _get_workspace_client().config.host
    _sp_workspace_client = WorkspaceClient(
        host=host,
        client_id=sp_id,
        client_secret=sp_secret,
    )
    log.info("Created SP OAuth WorkspaceClient for data-plane serving (auth_type=%s)",
             _sp_workspace_client.config.auth_type)
    return _sp_workspace_client


def query_serving_endpoint(
    endpoint_name: str,
    dataframe_records: list,
    *,
    use_data_plane: bool,
) -> dict:
    """Query a serving endpoint with automatic fallback for route-optimized endpoints.

    Tries: SDK data-plane OAuth → SP OAuth data-plane → SDK control plane.
    """
    w = _get_workspace_client()

    if use_data_plane:
        dp = getattr(w, "serving_endpoints_data_plane", None)
        if dp is not None:
            try:
                resp = dp.query(name=endpoint_name, dataframe_records=dataframe_records)
                return resp.as_dict() if hasattr(resp, "as_dict") else resp
            except Exception as e:
                if "OAuth tokens are not available" not in str(e):
                    raise
                log.info("Data-plane OAuth unavailable on default client")

        sp_w = _get_sp_workspace_client()
        if sp_w is not None:
            sp_dp = getattr(sp_w, "serving_endpoints_data_plane", None)
            if sp_dp is not None:
                log.info("Trying SP OAuth data-plane for %s", endpoint_name)
                resp = sp_dp.query(name=endpoint_name, dataframe_records=dataframe_records)
                return resp.as_dict() if hasattr(resp, "as_dict") else resp

    resp = w.serving_endpoints.query(
        name=endpoint_name,
        dataframe_records=dataframe_records,
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
    )
