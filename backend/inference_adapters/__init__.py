"""
Pluggable inference adapters for pre-annotation.

Selection order (first wins for the resolved name):
1. ``endpoint_config["adapter"]`` if non-empty string (normalized lowercase)
2. ``SERVING_ENDPOINT_ADAPTER`` env var, defaulting to ``"generic"``

Unknown adapter names raise ``UnknownInferenceAdapterError`` (HTTP 400 in API routes)
rather than silently falling back to generic — avoids masking typos in config.

How to use ``sam31`` vs ``generic``:
- Set project ``endpoint_config`` JSON to ``{"adapter": "sam31"}`` for **route-optimized**
  endpoints: queries ``serving_endpoints_data_plane`` (OAuth dataplane), not
  ``serving_endpoints.query``.
- For **standard** endpoints, use ``generic`` (default). Optional
  ``{"use_data_plane": true}`` or env ``USE_SERVING_DATA_PLANE=true`` makes
  ``generic`` use the dataplane client too.
- Set env ``SERVING_ENDPOINT_ADAPTER=sam31`` when projects do not set ``adapter``.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from .base import InferenceAdapter, UnknownInferenceAdapterError
from .generic import GenericAdapter
from .sam31 import Sam31Adapter

__all__ = [
    "InferenceAdapter",
    "UnknownInferenceAdapterError",
    "GenericAdapter",
    "Sam31Adapter",
    "get_adapter_for_config",
    "get_adapter_for_project",
]


def _normalize_adapter_name(raw: Any) -> Optional[str]:
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()
    return None


_GENERIC = GenericAdapter()
_SAM31 = Sam31Adapter()
_ADAPTER_REGISTRY: dict[str, InferenceAdapter] = {
    "generic": _GENERIC,
    "sam31": _SAM31,
}


def get_adapter_for_config(endpoint_config: Optional[dict]) -> InferenceAdapter:
    """Resolve adapter from merged project config (may be None) and env."""
    cfg = endpoint_config or {}
    name = _normalize_adapter_name(cfg.get("adapter"))
    if name is None:
        name = _normalize_adapter_name(os.environ.get("SERVING_ENDPOINT_ADAPTER", "generic"))
    if name is None:
        name = "generic"

    adapter = _ADAPTER_REGISTRY.get(name)
    if adapter is None:
        raise UnknownInferenceAdapterError(
            f"Unknown inference adapter {name!r}. "
            f"Known adapters: {', '.join(sorted(_ADAPTER_REGISTRY))}."
        )
    return adapter


def get_adapter_for_project(project: Any) -> InferenceAdapter:
    """Convenience: ``get_adapter_for_config(project.endpoint_config)``."""
    ec = getattr(project, "endpoint_config", None)
    return get_adapter_for_config(ec if isinstance(ec, dict) else None)
