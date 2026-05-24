"""HTTP-level tests for saving, reading back and exporting masks.

Uses the SQLite fallback app and a local directory standing in for a UC
Volume. Uses stdlib ``unittest`` (pytest not guaranteed in the container).
"""
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from backend import masks
from backend.tests.conftest import make_image_volume, test_client

BYPASS_HDR = {"X-Test-Allow-Local-Path": "1"}


@contextmanager
def _client():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        with test_client(tmp) as (c, _main, _tmp):
            yield c, tmp


def _create_project(c, source_volume, task_type="segmentation"):
    r = c.post("/api/projects", json={
        "name": "seg",
        "description": "",
        "task_type": task_type,
        "class_list": ["defect", "scratch"],
        "source_volume": str(source_volume),
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _samples(c, pid):
    r = c.get(f"/api/projects/{pid}/samples", params={"page_size": 100})
    assert r.status_code == 200, r.text
    return {s["filename"]: s for s in r.json()["items"]}


def _rect_mask(img_w, img_h, x0, y0, x1, y1):
    """Uncompressed RLE for an axis-aligned rectangle, built the way the
    browser does it: stamp a bitmap, then run-length encode it."""
    import numpy as np
    bitmap = np.zeros((img_h, img_w), dtype=np.uint8)
    bitmap[y0:y1, x0:x1] = 1
    h, w, counts = masks.counts_from_array(bitmap)
    return {"size": [h, w], "counts": counts}


class TestSegmentationProject(unittest.TestCase):
    def test_segmentation_is_an_accepted_task_type(self):
        with _client() as (c, tmp):
            pid = _create_project(c, make_image_volume(tmp))
            r = c.get(f"/api/projects/{pid}")
            self.assertEqual(r.json()["task_type"], "segmentation")

    def test_unknown_task_type_is_rejected(self):
        with _client() as (c, tmp):
            r = c.post("/api/projects", json={
                "name": "x", "description": "", "task_type": "segmentaton",
                "class_list": ["a"], "source_volume": str(make_image_volume(tmp)),
            })
            self.assertEqual(r.status_code, 400)
            self.assertIn("task_type", r.json()["detail"])


class TestMaskSave(unittest.TestCase):
    def test_save_and_read_back_round_trip(self):
        with _client() as (c, tmp):
            pid = _create_project(c, make_image_volume(tmp))
            sample = _samples(c, pid)["a.png"]  # 16x12
            mask = _rect_mask(16, 12, 4, 3, 8, 9)

            r = c.post(
                f"/api/projects/{pid}/samples/{sample['id']}/annotate-batch",
                json={"annotations": [
                    {"label": "defect", "ann_type": "mask", "mask_json": mask},
                ]},
            )
            self.assertEqual(r.status_code, 200, r.text)
            out = r.json()[0]

            # Clients speak uncompressed RLE in both directions.
            self.assertEqual(out["mask_json"], mask)
            self.assertEqual(out["ann_type"], "mask")
            # The bbox is derived server-side from the mask, not sent by the
            # client, so the two can never disagree.
            self.assertEqual(out["bbox_json"], {
                "x": round(4 / 16, 6), "y": round(3 / 12, 6),
                "w": round(4 / 16, 6), "h": round(6 / 12, 6),
            })

    def test_client_supplied_bbox_is_overridden_by_the_mask(self):
        with _client() as (c, tmp):
            pid = _create_project(c, make_image_volume(tmp))
            sample = _samples(c, pid)["a.png"]
            r = c.post(
                f"/api/projects/{pid}/samples/{sample['id']}/annotate-batch",
                json={"annotations": [{
                    "label": "defect", "ann_type": "mask",
                    "bbox_json": {"x": 0.9, "y": 0.9, "w": 0.05, "h": 0.05},
                    "mask_json": _rect_mask(16, 12, 0, 0, 8, 6),
                }]},
            )
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()[0]["bbox_json"],
                             {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5})

    def test_mask_survives_a_replacement_batch_save(self):
        # annotate-batch deletes every annotation and re-inserts, so a client
        # that echoes annotations back must not lose the mask on the way.
        with _client() as (c, tmp):
            pid = _create_project(c, make_image_volume(tmp))
            sid = _samples(c, pid)["a.png"]["id"]
            mask = _rect_mask(16, 12, 2, 2, 10, 10)
            url = f"/api/projects/{pid}/samples/{sid}/annotate-batch"

            first = c.post(url, json={"annotations": [
                {"label": "defect", "ann_type": "mask", "mask_json": mask}]})
            self.assertEqual(first.status_code, 200, first.text)

            echoed = [{
                "label": a["label"], "ann_type": a["ann_type"],
                "bbox_json": a["bbox_json"], "mask_json": a["mask_json"],
            } for a in first.json()]
            second = c.post(url, json={"annotations": echoed})
            self.assertEqual(second.status_code, 200, second.text)
            self.assertEqual(second.json()[0]["mask_json"], mask)

    def test_wrong_size_mask_is_rejected(self):
        with _client() as (c, tmp):
            pid = _create_project(c, make_image_volume(tmp))
            sid = _samples(c, pid)["a.png"]["id"]  # really 16x12
            r = c.post(
                f"/api/projects/{pid}/samples/{sid}/annotate-batch",
                json={"annotations": [{
                    "label": "defect", "ann_type": "mask",
                    "mask_json": _rect_mask(32, 24, 0, 0, 4, 4),
                }]},
            )
            self.assertEqual(r.status_code, 422)
            self.assertIn("does not match image", r.json()["detail"])

    def test_empty_mask_is_rejected(self):
        with _client() as (c, tmp):
            pid = _create_project(c, make_image_volume(tmp))
            sid = _samples(c, pid)["a.png"]["id"]
            r = c.post(
                f"/api/projects/{pid}/samples/{sid}/annotate-batch",
                json={"annotations": [{
                    "label": "defect", "ann_type": "mask",
                    "mask_json": {"size": [12, 16], "counts": [192]},
                }]},
            )
            self.assertEqual(r.status_code, 422)
            self.assertIn("empty", r.json()["detail"])

    def test_a_bad_mask_does_not_delete_existing_annotations(self):
        # Masks are validated before the delete-then-insert, so a rejected
        # batch leaves the sample exactly as it was.
        with _client() as (c, tmp):
            pid = _create_project(c, make_image_volume(tmp))
            sid = _samples(c, pid)["a.png"]["id"]
            url = f"/api/projects/{pid}/samples/{sid}/annotate-batch"
            good = _rect_mask(16, 12, 1, 1, 5, 5)
            c.post(url, json={"annotations": [
                {"label": "defect", "ann_type": "mask", "mask_json": good}]})

            r = c.post(url, json={"annotations": [
                {"label": "scratch", "ann_type": "mask", "mask_json": good},
                {"label": "defect", "ann_type": "mask",
                 "mask_json": {"size": [12, 16], "counts": [1, 2]}},
            ]})
            self.assertEqual(r.status_code, 422)

            still = c.get(f"/api/projects/{pid}/samples/{sid}").json()
            self.assertEqual(len(still["annotations"]), 1)
            self.assertEqual(still["annotations"][0]["mask_json"], good)


class TestSampleDimensions(unittest.TestCase):
    def test_dimensions_are_cached_by_serving_the_image(self):
        with _client() as (c, tmp):
            pid = _create_project(c, make_image_volume(tmp))
            sid = _samples(c, pid)["a.png"]["id"]
            self.assertIsNone(_samples(c, pid)["a.png"]["width"])

            self.assertEqual(
                c.get(f"/api/projects/{pid}/samples/{sid}/image").status_code, 200)
            after = _samples(c, pid)["a.png"]
            self.assertEqual((after["width"], after["height"]), (16, 12))

    def test_exif_rotated_image_uses_display_orientation(self):
        # Stored 40x20 with orientation 6; a browser renders it 20x40, and the
        # mask grid has to agree with the browser or every mask lands sideways.
        with _client() as (c, tmp):
            pid = _create_project(c, make_image_volume(tmp))
            sid = _samples(c, pid)["exif_orient.jpg"]["id"]
            r = c.post(
                f"/api/projects/{pid}/samples/{sid}/annotate-batch",
                json={"annotations": [{
                    "label": "defect", "ann_type": "mask",
                    "mask_json": _rect_mask(20, 40, 0, 0, 10, 20),
                }]},
            )
            self.assertEqual(r.status_code, 200, r.text)
            after = _samples(c, pid)["exif_orient.jpg"]
            self.assertEqual((after["width"], after["height"]), (20, 40))

    def test_unreadable_image_refuses_the_mask_rather_than_guessing(self):
        with _client() as (c, tmp):
            vol = make_image_volume(tmp)
            (vol / "broken.png").write_bytes(b"not an image")
            pid = _create_project(c, vol)
            sid = _samples(c, pid)["broken.png"]["id"]
            r = c.post(
                f"/api/projects/{pid}/samples/{sid}/annotate-batch",
                json={"annotations": [{
                    "label": "defect", "ann_type": "mask",
                    "mask_json": _rect_mask(16, 12, 0, 0, 4, 4),
                }]},
            )
            self.assertEqual(r.status_code, 422)
            self.assertIn("dimensions", r.json()["detail"])


class TestMaskHistory(unittest.TestCase):
    def test_history_records_old_and_new_masks(self):
        with _client() as (c, tmp):
            pid = _create_project(c, make_image_volume(tmp))
            sid = _samples(c, pid)["a.png"]["id"]
            url = f"/api/projects/{pid}/samples/{sid}/annotate-batch"
            first = _rect_mask(16, 12, 0, 0, 4, 4)
            second = _rect_mask(16, 12, 8, 6, 12, 10)
            c.post(url, json={"annotations": [
                {"label": "defect", "ann_type": "mask", "mask_json": first}]})
            c.post(url, json={"annotations": [
                {"label": "defect", "ann_type": "mask", "mask_json": second}]})

            # The history endpoint returns newest first; sort by id so the
            # assertions read in the order the edits happened.
            hist = sorted(
                c.get(f"/api/projects/{pid}/samples/{sid}/history").json(),
                key=lambda h: h["id"],
            )
            created = [h for h in hist if h["action"] == "create"]
            deleted = [h for h in hist if h["action"] == "delete"]
            self.assertEqual([h["new_mask_json"] for h in created], [first, second])
            self.assertEqual([h["old_mask_json"] for h in deleted], [first])


class TestMaskImport(unittest.TestCase):
    def test_import_accepts_mask_annotations(self):
        import json
        with _client() as (c, tmp):
            vol = make_image_volume(tmp)
            pid = _create_project(c, vol)
            mask = _rect_mask(16, 12, 4, 0, 12, 6)
            labels = tmp / "labels.jsonl"
            labels.write_text(json.dumps({
                "filename": "a.png",
                "annotations": [
                    {"label": "defect", "ann_type": "mask", "mask_json": mask}],
            }) + "\n")
            r = c.post(
                f"/api/projects/{pid}/import",
                json={"volume_path": str(labels), "format": "jsonl"},
                headers=BYPASS_HDR,
            )
            self.assertEqual(r.status_code, 200, r.text)
            sid = _samples(c, pid)["a.png"]["id"]
            got = c.get(f"/api/projects/{pid}/samples/{sid}").json()["annotations"]
            self.assertEqual(got[0]["mask_json"], mask)
            self.assertEqual(got[0]["bbox_json"], {
                "x": 0.25, "y": 0.0, "w": 0.5, "h": 0.5,
            })

    def test_import_rejects_a_mask_that_does_not_fit_the_image(self):
        import json
        with _client() as (c, tmp):
            vol = make_image_volume(tmp)
            pid = _create_project(c, vol)
            labels = tmp / "labels.jsonl"
            labels.write_text(json.dumps({
                "filename": "a.png",
                "annotations": [{
                    "label": "defect", "ann_type": "mask",
                    "mask_json": _rect_mask(64, 64, 0, 0, 4, 4),
                }],
            }) + "\n")
            r = c.post(
                f"/api/projects/{pid}/import",
                json={"volume_path": str(labels), "format": "jsonl"},
                headers=BYPASS_HDR,
            )
            # Reported as a row-level validation error, not a mid-commit crash.
            self.assertEqual(r.status_code, 422, r.text)
            self.assertIn("does not match image", r.json()["errors"][0]["reason"])

    def test_import_rejects_mask_on_a_classification_project(self):
        import json
        with _client() as (c, tmp):
            vol = make_image_volume(tmp)
            pid = _create_project(c, vol, task_type="classification")
            labels = tmp / "labels.jsonl"
            labels.write_text(json.dumps({
                "filename": "a.png",
                "annotations": [{
                    "label": "defect", "ann_type": "mask",
                    "mask_json": _rect_mask(16, 12, 0, 0, 4, 4),
                }],
            }) + "\n")
            r = c.post(
                f"/api/projects/{pid}/import",
                json={"volume_path": str(labels), "format": "jsonl"},
                headers=BYPASS_HDR,
            )
            self.assertEqual(r.status_code, 422, r.text)
            self.assertIn(
                "classification project cannot accept mask",
                r.json()["errors"][0]["reason"],
            )


if __name__ == "__main__":
    unittest.main()
