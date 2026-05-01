"""SAM 3.1 + route-optimized serving — dataplane ``query`` + JSON-wrapped input."""

from __future__ import annotations

import base64
import json
import logging
from typing import Optional

from .base import InferenceAdapter

log = logging.getLogger(__name__)


class Sam31Adapter(InferenceAdapter):
    """SAM 3.1 serving wrapper.

    The endpoint schema is ``{"input": "<json_string>"}`` where the JSON string
    contains ``{"image": "<base64>", "prompt_type": "text", "prompt": "..."}``
    (or point/box prompts).

    For detection tasks the text prompt is the class list joined by ``. ``.
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
        b64 = base64.b64encode(image_bytes).decode("ascii")

        prompt = cfg.get("sam_text_prompt") or ". ".join(class_list)
        inner_payload: dict = {
            "image": b64,
            "prompt_type": "text",
            "prompt": prompt,
        }
        if "sam_record_extra" in cfg and isinstance(cfg["sam_record_extra"], dict):
            inner_payload = {**cfg["sam_record_extra"], **inner_payload}

        record = [{"input": json.dumps(inner_payload)}]

        raw = inf.query_serving_endpoint(
            endpoint_name,
            record,
            use_data_plane=True,
        )

        return self._parse_sam_response(raw, class_list, cfg)

    def _parse_sam_response(
        self,
        raw: dict,
        class_list: list[str],
        cfg: dict,
    ) -> list[dict]:
        """Parse SAM 3.1 detection output into annotation dicts."""
        min_conf = float(cfg.get("min_confidence", 0.3))
        predictions = raw.get("predictions", [])
        annotations: list[dict] = []

        for pred_row in predictions:
            output_str = pred_row if isinstance(pred_row, str) else pred_row.get("output", "")
            if isinstance(output_str, str):
                try:
                    output = json.loads(output_str)
                except (json.JSONDecodeError, TypeError):
                    log.warning("Cannot parse SAM output: %s", output_str[:200])
                    continue
            else:
                output = output_str

            if "error" in output:
                log.warning("SAM endpoint returned error: %s", output["error"])
                continue

            img_size = output.get("image_size", {})
            img_w = img_size.get("width", 1)
            img_h = img_size.get("height", 1)

            for det in output.get("detections", []):
                score = det.get("score", 0.0)
                if score < min_conf:
                    continue

                box = det.get("box")
                if not box:
                    continue

                x1 = box.get("x1", 0) / img_w
                y1 = box.get("y1", 0) / img_h
                x2 = box.get("x2", 0) / img_w
                y2 = box.get("y2", 0) / img_h
                w = x2 - x1
                h = y2 - y1

                label = class_list[0] if class_list else "object"

                annotations.append({
                    "label": label,
                    "ann_type": "bbox",
                    "bbox_json": {"x": round(x1, 6), "y": round(y1, 6),
                                  "w": round(w, 6), "h": round(h, 6)},
                    "confidence": round(score, 4),
                })

        return annotations
