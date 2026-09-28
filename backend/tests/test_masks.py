"""Conformance tests for the COCO RLE codec in ``backend/masks.py``.

The important test here is ``TestGoldenVectors``: it compares our encoder
against output captured from real ``pycocotools``, not against our own
decoder. COCO's compressed RLE is a 5-bit-group, sign-extended, ASCII-offset
format with counts delta-encoded against the count two positions earlier, and
a hand-rolled encoder/decoder pair that is merely self-consistent will
round-trip perfectly while writing files no COCO reader can parse. Fixtures
are regenerated with ``python tools/gen_rle_fixtures.py``.

Uses stdlib ``unittest`` (pytest not guaranteed in the container).
"""
import json
import unittest
from pathlib import Path

import base64

import numpy as np

from backend import masks


def golden_bitmap(case):
    """The fixture's independently-generated row-major bitmap."""
    h, w = case["size"]
    bits = np.unpackbits(np.frombuffer(base64.b64decode(case["pixels_b64"]), np.uint8))
    return bits[: h * w].reshape(h, w)

FIXTURE = Path(__file__).parent / "fixtures" / "coco_rle_golden.json"
CASES = json.loads(FIXTURE.read_text())["cases"]


class TestGoldenVectors(unittest.TestCase):
    def test_fixtures_present(self):
        self.assertGreaterEqual(len(CASES), 17)

    def test_encode_matches_pycocotools(self):
        for case in CASES:
            with self.subTest(case["name"]):
                self.assertEqual(
                    masks.rle_to_string(case["counts"]), case["compressed"],
                )

    def test_decode_matches_pycocotools(self):
        for case in CASES:
            with self.subTest(case["name"]):
                self.assertEqual(
                    masks.rle_from_string(case["compressed"]), case["counts"],
                )

    def test_area_matches_pycocotools(self):
        for case in CASES:
            with self.subTest(case["name"]):
                self.assertEqual(masks.rle_area(case["counts"]), case["area"])

    def test_bbox_matches_pycocotools(self):
        for case in CASES:
            with self.subTest(case["name"]):
                got = [float(v) for v in masks.rle_to_bbox(case["counts"], *case["size"])]
                self.assertEqual(got, case["bbox"])


class TestGoldenBitmaps(unittest.TestCase):
    """Pin the exact pixels, not just self-consistency.

    ``counts_from_array(bitmap_from_counts(counts)) == counts`` holds for a
    codec that is consistently transposed or flipped, and area is invariant to
    both, so these assert against pycocotools' own decode instead.
    """

    def test_decoded_bitmap_matches_pycocotools(self):
        for case in CASES:
            with self.subTest(case["name"]):
                h, w = case["size"]
                got = masks.bitmap_from_counts(case["counts"], h, w)
                self.assertTrue(np.array_equal(got, golden_bitmap(case)))

    def test_encoding_pycocotools_bitmap_matches_golden_counts(self):
        for case in CASES:
            with self.subTest(case["name"]):
                h, w = case["size"]
                self.assertEqual(
                    masks.counts_from_array(golden_bitmap(case)),
                    (h, w, case["counts"]),
                )

    def test_fixture_set_would_catch_a_transposed_codec(self):
        """Guard the guard.

        A handful of fixtures are deliberately symmetric (empty, full, centred
        square, checkerboard) and cannot detect a transpose on their own. Most
        must be able to, or the assertions above prove less than they look.
        """
        catching = [
            case["name"] for case in CASES
            if case["size"][0] != case["size"][1]
            or not np.array_equal(golden_bitmap(case), golden_bitmap(case).T)
        ]
        self.assertGreaterEqual(len(catching), 12, f"only {catching} are asymmetric")


class TestBitmapRoundTrip(unittest.TestCase):
    def test_rasterise_and_reencode(self):
        for case in CASES:
            with self.subTest(case["name"]):
                h, w = case["size"]
                bitmap = masks.bitmap_from_counts(case["counts"], h, w)
                self.assertEqual(bitmap.shape, (h, w))
                self.assertEqual(int(bitmap.sum()), case["area"])
                self.assertEqual(
                    masks.counts_from_array(bitmap), (h, w, case["counts"]),
                )

    def test_vectorised_matches_pure_python(self):
        for case in CASES:
            with self.subTest(case["name"]):
                h, w = case["size"]
                bitmap = masks.bitmap_from_counts(case["counts"], h, w)
                slow = masks.counts_from_bitmap(bitmap.ravel(order="F"), h, w)
                self.assertEqual(slow, case["counts"])


class TestValidation(unittest.TestCase):
    def _valid(self):
        return {"size": [4, 4], "counts": [2, 3, 11]}

    def test_accepts_valid_mask(self):
        h, w, counts = masks.validate_uncompressed(self._valid(), 4, 4)
        self.assertEqual((h, w, counts), (4, 4, [2, 3, 11]))

    def test_accepts_compressed_counts(self):
        # A mask read back from the API can be resubmitted unchanged.
        compressed = {"size": [4, 4], "counts": masks.rle_to_string([2, 3, 11])}
        _, _, counts = masks.validate_uncompressed(compressed, 4, 4)
        self.assertEqual(counts, [2, 3, 11])

    def test_rejects_size_mismatch_with_image(self):
        with self.assertRaisesRegex(masks.MaskValidationError, "does not match image"):
            masks.validate_uncompressed(self._valid(), 8, 8)

    def test_rejects_wrong_pixel_sum(self):
        with self.assertRaisesRegex(masks.MaskValidationError, "!="):
            masks.validate_uncompressed({"size": [4, 4], "counts": [2, 3, 4]})

    def test_rejects_negative_counts(self):
        with self.assertRaisesRegex(masks.MaskValidationError, "non-negative"):
            masks.validate_uncompressed({"size": [4, 4], "counts": [20, -4]})

    def test_rejects_empty_mask(self):
        with self.assertRaisesRegex(masks.MaskValidationError, "empty"):
            masks.validate_uncompressed({"size": [4, 4], "counts": [16]})

    def test_rejects_non_integer_counts(self):
        with self.assertRaisesRegex(masks.MaskValidationError, "integers"):
            masks.validate_uncompressed({"size": [4, 4], "counts": [2.5, 3, 10.5]})

    def test_rejects_booleans_as_counts(self):
        with self.assertRaisesRegex(masks.MaskValidationError, "integers"):
            masks.validate_uncompressed({"size": [2, 2], "counts": [True, 3]})

    def test_rejects_more_runs_than_pixels(self):
        # Bounded before allocating anything proportional to the pixel count.
        with self.assertRaisesRegex(masks.MaskValidationError, "more runs than pixels"):
            masks.validate_uncompressed({"size": [2, 2], "counts": [1] * 99})

    def test_rejects_oversized_dimensions(self):
        with self.assertRaisesRegex(masks.MaskValidationError, "limit"):
            masks.validate_uncompressed({"size": [999999, 999999], "counts": [1]})

    def test_rejects_pixel_count_over_limit(self):
        side = 8000
        with self.assertRaisesRegex(masks.MaskValidationError, "limit"):
            masks.validate_uncompressed({"size": [side, side], "counts": [1]})

    def test_rejects_malformed_shapes(self):
        for bad in (None, [], "x", {}, {"size": [4], "counts": [16]},
                    {"size": [4, 4]}, {"size": [0, 4], "counts": [0]},
                    {"size": ["a", "b"], "counts": [1]}):
            with self.subTest(bad=bad):
                with self.assertRaises(masks.MaskValidationError):
                    masks.validate_uncompressed(bad)


class TestWireAndStorage(unittest.TestCase):
    def test_storage_is_compressed_and_wire_is_not(self):
        counts = [2, 3, 11]
        stored = masks.to_storage(counts, 4, 4)
        self.assertIsInstance(stored["counts"], str)
        self.assertEqual(masks.to_wire(stored), {"size": [4, 4], "counts": counts})

    def test_wire_degrades_to_none_rather_than_raising(self):
        # An unreadable mask must not take down the whole labeling view.
        for bad in (None, "x", 5, {}, {"size": [4, 4]},
                    {"size": [4, 4], "counts": 7},
                    {"size": "nope", "counts": "abc"}):
            with self.subTest(bad=bad):
                self.assertIsNone(masks.to_wire(bad))

    def test_normalized_bbox_is_derived_from_the_mask(self):
        # 10x10 image, foreground rows 3..6 / cols 3..6.
        h = w = 10
        bitmap = masks.bitmap_from_counts(
            next(c["counts"] for c in CASES if c["name"] == "centre_square_10x10"), h, w,
        )
        _, _, counts = masks.counts_from_array(bitmap)
        self.assertEqual(
            masks.normalized_bbox(counts, h, w),
            {"x": 0.3, "y": 0.3, "w": 0.4, "h": 0.4},
        )


if __name__ == "__main__":
    unittest.main()
