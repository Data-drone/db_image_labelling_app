"""Abstract inference adapter — one contract per serving stack."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

log = logging.getLogger(__name__)


class UnknownInferenceAdapterError(ValueError):
    """Raised when ``adapter`` (project config or env) names an unknown implementation.

    API layer should map this to HTTP 400 (see ``routes/inference.py``).
    We do not fall back to generic for unknown names — that would hide typos.
    """


class InferenceAdapter(ABC):
    """Pluggable contract between model serving and annotation dicts.

    Implementations call the appropriate serving API and parse responses into
    the same shape ``predict_sample`` has always returned: list of dicts with
    ``label``, ``ann_type``, optional ``bbox_json``, optional ``confidence``.
    """

    @abstractmethod
    def query_and_parse(
        self,
        endpoint_name: str,
        image_bytes: bytes,
        task_type: str,
        class_list: list[str],
        endpoint_config: Optional[dict],
    ) -> list[dict]:
        """Query the serving endpoint and return AnnotationCreate-compatible dicts."""
        ...

    def query_embedding(
        self,
        endpoint_name: str,
        image_bytes: bytes,
        endpoint_config: Optional[dict],
    ) -> Optional[list[float]]:
        """Query the serving endpoint for an embedding vector. Returns None if unsupported."""
        return None
