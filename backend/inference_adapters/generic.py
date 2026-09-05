"""Default adapter: Databricks serving ``dataframe_records`` + existing parsers."""

from __future__ import annotations

import logging
from typing import Optional

from .base import InferenceAdapter

log = logging.getLogger(__name__)


class GenericAdapter(InferenceAdapter):
    """Current behavior — ``[{"image": b64}]`` query and classification/detection parsers."""

    def query_and_parse(
        self,
        endpoint_name: str,
        image_bytes: bytes,
        task_type: str,
        class_list: list[str],
        endpoint_config: Optional[dict],
    ) -> list[dict]:
        # Import here so ``backend.inference`` can load before this package is initialized.
        from .. import inference as inf

        use_dp = inf.resolve_use_data_plane(endpoint_config)
        raw = inf.query_endpoint(endpoint_name, image_bytes, use_data_plane=use_dp)

        if task_type == "classification":
            return inf.parse_classification_response(raw, class_list, endpoint_config)
        if task_type == "detection":
            return inf.parse_detection_response(raw, class_list, endpoint_config)
        if task_type == "segmentation":
            # There is no generic wire format for masks -- a segmentation endpoint
            # needs an adapter that knows its own response shape (see ``sam31``).
            # Parse boxes so the run still yields locatable drafts rather than
            # whole-image labels; the mask is simply absent.
            log.warning(
                "Segmentation project on the 'generic' adapter: masks cannot be parsed "
                'generically. Set endpoint_config {"adapter": "sam31"} for mask drafts. '
                "Falling back to the detection parser (boxes only)."
            )
            return inf.parse_detection_response(raw, class_list, endpoint_config)
        log.warning("Unknown task_type '%s', trying classification parser", task_type)
        return inf.parse_classification_response(raw, class_list, endpoint_config)
