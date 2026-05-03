"""DINOv3 embedding adapter — route-optimized dataplane query."""

from __future__ import annotations

import base64
import logging
from typing import Optional

from .base import InferenceAdapter

log = logging.getLogger(__name__)


class DinOv3Adapter(InferenceAdapter):
    """DINOv3 serving wrapper for image embeddings.

    The endpoint accepts ``{"image": "<base64>"}`` and returns
    ``{"predictions": [{"embedding": [<1024 floats>]}]}``.
    """

    def query_and_parse(
        self,
        endpoint_name: str,
        image_bytes: bytes,
        task_type: str,
        class_list: list[str],
        endpoint_config: Optional[dict],
    ) -> list[dict]:
        return []

    def query_embedding(
        self,
        endpoint_name: str,
        image_bytes: bytes,
        endpoint_config: Optional[dict],
    ) -> Optional[list[float]]:
        from .. import inference as inf

        b64 = base64.b64encode(image_bytes).decode("ascii")
        record = [{"image": b64}]

        raw = inf.query_serving_endpoint(
            endpoint_name,
            record,
            use_data_plane=True,
        )

        predictions = raw.get("predictions", [])
        if not predictions:
            log.warning("DINOv3 endpoint returned no predictions")
            return None

        pred = predictions[0]
        if isinstance(pred, list):
            return pred

        embedding = pred.get("embedding") if isinstance(pred, dict) else None
        if embedding is None:
            log.warning("DINOv3 prediction missing embedding field")
            return None

        if isinstance(embedding, str):
            import json as _json
            try:
                embedding = _json.loads(embedding)
            except (ValueError, TypeError):
                log.warning("DINOv3 embedding field is not valid JSON: %s", embedding[:100])
                return None

        if not isinstance(embedding, list):
            log.warning("DINOv3 embedding field is not a list: %s", type(embedding).__name__)
            return None

        return embedding
