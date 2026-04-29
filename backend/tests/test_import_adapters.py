"""
Adapter-level tests. Pure functions, no DB, no HTTP.

Uses stdlib ``unittest`` (pytest not guaranteed in the container).
"""
import unittest

from backend.import_adapters import get_adapter
from backend.import_adapters.coco import parse as coco_parse
from backend.import_adapters.jsonl import parse as jsonl_parse


class TestGetAdapter(unittest.TestCase):
    def test_known_formats(self):
        self.assertIs(get_adapter("jsonl"), jsonl_parse)
        self.assertIs(get_adapter("coco"), coco_parse)

    def test_unknown_raises(self):
        with self.assertRaises(ValueError):
            get_adapter("yolo")


if __name__ == "__main__":
    unittest.main()
