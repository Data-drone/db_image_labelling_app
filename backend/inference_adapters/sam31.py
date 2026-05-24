"""SAM 3.1 + route-optimized serving — dataplane ``query`` + JSON-wrapped input."""

from __future__ import annotations

import base64
import binascii
import io
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

        return self._parse_sam_response(raw, class_list, cfg, task_type)

    def _parse_sam_response(
        self,
        raw: dict,
        class_list: list[str],
        cfg: dict,
        task_type: str = "detection",
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

                label = self._resolve_label(det, class_list)

                entry = {
                    "label": label,
                    "ann_type": "bbox",
                    "bbox_json": {"x": round(x1, 6), "y": round(y1, 6),
                                  "w": round(w, 6), "h": round(h, 6)},
                    "confidence": round(score, 4),
                }

                mask = _extract_mask(det)
                if mask is not None:
                    entry["mask_json"] = mask
                    if task_type == "segmentation":
                        entry["ann_type"] = "mask"

                annotations.append(entry)

        return annotations

    @staticmethod
    def _resolve_label(det: dict, class_list: list[str]) -> str:
        """Map a detection back to a project class.

        The text prompt is the class list joined by ``. ``, so a multi-class
        prompt gets multi-class results; taking ``class_list[0]``
        unconditionally would label every object with the first class. The
        endpoint's own label is only trusted when it actually names a project
        class, otherwise we fall back rather than invent a new class.
        """
        for key in ("label", "phrase", "class_name", "category", "text"):
            value = det.get(key)
            if not isinstance(value, str):
                continue
            candidate = value.strip()
            if candidate in class_list:
                return candidate
            lowered = {c.lower(): c for c in class_list}
            if candidate.lower() in lowered:
                return lowered[candidate.lower()]
        return class_list[0] if class_list else "object"



def _extract_mask(det: dict):
    """Best-effort extraction of a segmentation mask from one detection.

    NOTE: this is written blind. No segmentation endpoint is deployed in the
    workspaces this app runs against, so the exact key and encoding SAM 3.1
    returns could not be observed (see issue #28, step 0). Rather than guess
    one shape and silently produce nothing for the others, every encoding a
    SAM-family endpoint plausibly emits is accepted and transcoded to
    uncompressed COCO RLE at this boundary:

      * ``{"size": [h, w], "counts": "<compressed string>"}``
      * ``{"size": [h, w], "counts": [int, ...]}``
      * a base64-encoded PNG/single-channel image
      * a nested list of 0/1 rows

    Returns uncompressed RLE for the caller to validate against the image's
    real dimensions, or None. Never raises.
    """
    from .. import masks as mask_utils

    raw = None
    for key in ("mask", "segmentation", "mask_rle", "rle", "mask_png", "mask_base64"):
        if det.get(key) is not None:
            raw = det[key]
            break
    if raw is None:
        return None

    # RLE dict, either compressed or uncompressed.
    if isinstance(raw, dict) and "counts" in raw and "size" in raw:
        wire = mask_utils.to_wire(raw)
        if wire is None:
            log.warning("SAM returned an unreadable RLE mask, dropping it")
        return wire

    # Base64 image bytes.
    if isinstance(raw, str):
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            log.warning("SAM mask is a string but not valid base64, dropping it")
            return None
        try:
            import numpy as np
            from PIL import Image as PILImage
            with PILImage.open(io.BytesIO(data)) as img:
                arr = np.array(img.convert("L"))
            height, width, counts = mask_utils.counts_from_array(arr)
            return {"size": [height, width], "counts": counts}
        except Exception as e:
            log.warning("Could not decode SAM mask image: %s", e)
            return None

    # Nested list bitmap.
    if isinstance(raw, (list, tuple)):
        try:
            height, width, counts = mask_utils.counts_from_array(raw)
            return {"size": [height, width], "counts": counts}
        except Exception as e:
            log.warning("Could not read SAM mask array: %s", e)
            return None

    log.warning("Unrecognised SAM mask encoding: %s", type(raw).__name__)
    return None
