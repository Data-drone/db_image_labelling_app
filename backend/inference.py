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


def check_endpoint_health(endpoint_name: str) -> dict:
    """Check if a serving endpoint exists and is ready.

    Returns {"status": "ready"|"not_ready"|"not_found"|"error", ...}.
    """
    try:
        w = _get_workspace_client()
        ep = w.serving_endpoints.get(name=endpoint_name)
        state = "unknown"
        if ep.state and ep.state.ready:
            state = str(ep.state.ready)
        if state == "READY":
            return {"status": "ready", "endpoint": endpoint_name, "state": state}
        return {"status": "not_ready", "endpoint": endpoint_name, "state": state}
    except Exception as e:
        err_str = str(e)
        if "RESOURCE_DOES_NOT_EXIST" in err_str or "404" in err_str:
            return {"status": "not_found", "endpoint": endpoint_name, "error": "Endpoint not found"}
        if "PERMISSION_DENIED" in err_str or "403" in err_str:
            return {"status": "error", "endpoint": endpoint_name, "error": "Permission denied"}
        return {"status": "error", "endpoint": endpoint_name, "error": err_str}


def query_endpoint(endpoint_name: str, image_bytes: bytes) -> dict:
    """Send an image to a serving endpoint and return the raw prediction.

    Sends image as base64-encoded string in dataframe_records format.
    Raises on network/SDK errors.
    """
    w = _get_workspace_client()
    b64 = base64.b64encode(image_bytes).decode("ascii")
    resp = w.serving_endpoints.query(
        name=endpoint_name,
        dataframe_records=[{"image": b64}],
    )
    return resp.as_dict() if hasattr(resp, "as_dict") else resp


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
    """
    raw = query_endpoint(endpoint_name, image_bytes)

    if task_type == "classification":
        return parse_classification_response(raw, class_list, endpoint_config)
    elif task_type == "detection":
        return parse_detection_response(raw, class_list, endpoint_config)
    else:
        log.warning("Unknown task_type '%s', trying classification parser", task_type)
        return parse_classification_response(raw, class_list, endpoint_config)
