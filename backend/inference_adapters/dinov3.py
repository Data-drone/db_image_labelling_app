"""DINOv3 embedding adapter — route-optimized dataplane query."""

from __future__ import annotations

import base64
import logging
from typing import Optional

from .base import InferenceAdapter

log = logging.getLogger(__name__)

BATCH_SIZE = 8


def _parse_single_prediction(pred) -> Optional[list[float]]:
    """Extract a float list embedding from one prediction entry."""
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


class DinOv3Adapter(InferenceAdapter):
    """DINOv3 serving wrapper for image embeddings.

    The endpoint accepts ``{"image": "<base64>"}`` and returns
    ``{"predictions": [{"embedding": [<1024 floats>]}]}``.

    Supports batched requests (up to 8 images per call).
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
        results = self.batch_query_embedding(endpoint_name, [image_bytes], endpoint_config)
        return results[0]

    def batch_query_embedding(
        self,
        endpoint_name: str,
        image_bytes_list: list[bytes],
        endpoint_config: Optional[dict],
    ) -> list[Optional[list[float]]]:
        """Query embeddings for a batch of images (up to BATCH_SIZE).

        Returns a list with one entry per input image — either a float list
        or None on failure.
        """
        from .. import inference as inf

        records = [
            {"image": base64.b64encode(img).decode("ascii")}
            for img in image_bytes_list
        ]

        try:
            raw = inf.query_serving_endpoint(
                endpoint_name,
                records,
                use_data_plane=True,
            )
        except Exception as exc:
            log.warning("DINOv3 batch query failed: %s", exc)
            return [None] * len(image_bytes_list)

        predictions = raw.get("predictions", [])
        results: list[Optional[list[float]]] = []
        for i in range(len(image_bytes_list)):
            if i < len(predictions):
                results.append(_parse_single_prediction(predictions[i]))
            else:
                log.warning("DINOv3 batch: missing prediction for index %d", i)
                results.append(None)

        return results
