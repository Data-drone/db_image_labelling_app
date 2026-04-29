"""Import format adapters.

Each adapter is a pure function:
    parse(raw_bytes: bytes) -> (list[NormalizedImportItem], list[ImportErrorItem])

No DB access, no network. Adapters normalize their source format into
NormalizedImportItem objects that the import route writes to the DB.
"""

from dataclasses import dataclass, field

from ..schemas import AnnotationCreate, ImportErrorItem


@dataclass
class NormalizedImportItem:
    filename: str
    annotations: list[AnnotationCreate] = field(default_factory=list)


from . import coco, jsonl  # noqa: E402

ADAPTERS = {
    "coco": coco.parse,
    "jsonl": jsonl.parse,
}


def get_adapter(format_name: str):
    """Return the adapter for a format, or raise ValueError."""
    if format_name not in ADAPTERS:
        raise ValueError(
            f"Unknown format '{format_name}'. Supported: {sorted(ADAPTERS.keys())}"
        )
    return ADAPTERS[format_name]
