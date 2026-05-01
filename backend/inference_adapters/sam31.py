"""SAM 3.1 + route-optimized serving — dataplane ``query`` + configurable record shape."""

from __future__ import annotations

import base64
import logging
from typing import Optional

from .base import InferenceAdapter

log = logging.getLogger(__name__)


class Sam31Adapter(InferenceAdapter):
    """Route-optimized endpoints: ``serving_endpoints_data_plane.query`` (OAuth dataplane).

    Request record defaults to ``{"image": "<base64>"}``. Override with
    ``endpoint_config["sam_input_image_key"]`` and optional ``sam_record_extra``
    (dict merged into the record; image field wins on key collision).
    """

    def query_and_parse(
        self,
        endpoint_name: str,
        image_bytes: bytes,
        task_type: str,
        class_list: list[str],
        endpoint_config: Optional[dict],
    ) -> list[dict]:
        from .. import inference as inf

        cfg = endpoint_config or {}
        image_key = (cfg.get("sam_input_image_key") or "image").strip() or "image"
        extra = cfg.get("sam_record_extra")
        if extra is not None and not isinstance(extra, dict):
            log.warning("sam_record_extra is not a dict; ignoring")
            extra = None

        b64 = base64.b64encode(image_bytes).decode("ascii")
        record: dict = {image_key: b64}
        if isinstance(extra, dict):
            record = {**extra, **record}

        raw = inf.query_serving_endpoint(
            endpoint_name,
            [record],
            use_data_plane=True,
        )

        if task_type == "classification":
            return inf.parse_classification_response(raw, class_list, endpoint_config)
        if task_type == "detection":
            return inf.parse_detection_response(raw, class_list, endpoint_config)
        log.warning("Unknown task_type '%s', trying classification parser", task_type)
        return inf.parse_classification_response(raw, class_list, endpoint_config)
