"""
HTTP-level tests for POST /api/projects/{id}/import.

Uses SQLite backend and local-filesystem stand-ins for UC Volumes.
Uses stdlib ``unittest`` (pytest not guaranteed in the container).

The endpoint normally requires ``volume_path`` to start with ``/Volumes/``.
Tests use the test-only ``X-Test-Allow-Local-Path: 1`` header to exercise
the happy path against a local ``tmp_path`` directory. Production never
sets that header.
"""
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from backend.tests.conftest import make_sample_volume, test_client


@contextmanager
def _client():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        with test_client(tmp) as (c, _main, _tmp):
            yield c, tmp


def _create_project(c, source_volume, **over):
    body = {
        "name": "t",
        "description": "",
        "task_type": "classification",
        "class_list": ["cat", "dog"],
        "source_volume": str(source_volume),
    }
    body.update(over)
    r = c.post("/api/projects", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _write_jsonl(path: Path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


BYPASS_HDR = {"X-Test-Allow-Local-Path": "1"}


class TestImportEndpointSkeleton(unittest.TestCase):
    def test_projects_list_reachable(self):
        with _client() as (c, _tmp):
            r = c.get("/api/projects")
            self.assertEqual(r.status_code, 200)


class TestImportHappyPath(unittest.TestCase):
    def test_import_happy_path(self):
        with _client() as (c, tmp):
            vol = make_sample_volume(tmp)
            pid = _create_project(c, vol)
            labels = tmp / "labels.jsonl"
            _write_jsonl(labels, [
                {"filename": "a.jpg", "annotations": [
                    {"label": "cat", "ann_type": "classification"}]},
                {"filename": "b.jpg", "annotations": [
                    {"label": "dog", "ann_type": "classification"}]},
            ])
            r = c.post(
                f"/api/projects/{pid}/import",
                json={"volume_path": str(labels), "format": "jsonl"},
                headers=BYPASS_HDR,
            )
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual(body["samples_touched"], 2)
            self.assertEqual(body["annotations_created"], 2)


class TestImportPathValidation(unittest.TestCase):
    """Critical #2: volume_path must start with /Volumes/ in production."""

    def test_rejects_non_volume_path(self):
        with _client() as (c, tmp):
            vol = make_sample_volume(tmp)
            pid = _create_project(c, vol)
            labels = tmp / "labels.jsonl"
            _write_jsonl(labels, [])
            r = c.post(
                f"/api/projects/{pid}/import",
                json={"volume_path": str(labels), "format": "jsonl"},
                # no bypass header
            )
            self.assertEqual(r.status_code, 400)
            detail = r.json()["detail"].lower()
            self.assertTrue(
                "volume" in detail or "path" in detail,
                f"unexpected detail: {r.json()['detail']}",
            )

    def test_rejects_path_traversal(self):
        with _client() as (c, tmp):
            vol = make_sample_volume(tmp)
            pid = _create_project(c, vol)
            r = c.post(
                f"/api/projects/{pid}/import",
                json={
                    "volume_path": "/Volumes/a/b/../../etc/passwd",
                    "format": "jsonl",
                },
            )
            self.assertEqual(r.status_code, 400)

    def test_rejects_backslash(self):
        with _client() as (c, tmp):
            vol = make_sample_volume(tmp)
            pid = _create_project(c, vol)
            r = c.post(
                f"/api/projects/{pid}/import",
                json={
                    "volume_path": "/Volumes/a\\b/labels.jsonl",
                    "format": "jsonl",
                },
            )
            self.assertEqual(r.status_code, 400)


class TestImportSizeLimits(unittest.TestCase):
    """Critical #3: 200 MB byte cap."""

    def test_rejects_oversized_file(self):
        with _client() as (c, tmp):
            vol = make_sample_volume(tmp)
            pid = _create_project(c, vol)
            big = tmp / "big.jsonl"
            # Sparse file: 250 MB logical, tiny on disk.
            with open(big, "wb") as f:
                f.seek(250 * 1024 * 1024)
                f.write(b"x")
            r = c.post(
                f"/api/projects/{pid}/import",
                json={"volume_path": str(big), "format": "jsonl"},
                headers=BYPASS_HDR,
            )
            self.assertEqual(r.status_code, 400)
            detail = r.json()["detail"].lower()
            self.assertTrue(
                "size" in detail or "too large" in detail,
                f"unexpected detail: {r.json()['detail']}",
            )


class TestImportLiteralValidation(unittest.TestCase):
    """Important #9: Pydantic Literal → 422 on bad enum values."""

    def test_invalid_format_returns_422(self):
        with _client() as (c, tmp):
            vol = make_sample_volume(tmp)
            pid = _create_project(c, vol)
            labels = tmp / "labels.jsonl"
            _write_jsonl(labels, [])
            r = c.post(
                f"/api/projects/{pid}/import",
                json={"volume_path": str(labels), "format": "yolo"},
                headers=BYPASS_HDR,
            )
            self.assertEqual(r.status_code, 422)

    def test_invalid_on_missing_sample_returns_422(self):
        with _client() as (c, tmp):
            vol = make_sample_volume(tmp)
            pid = _create_project(c, vol)
            labels = tmp / "labels.jsonl"
            _write_jsonl(labels, [])
            r = c.post(
                f"/api/projects/{pid}/import",
                json={
                    "volume_path": str(labels),
                    "format": "jsonl",
                    "on_missing_sample": "bogus",
                },
                headers=BYPASS_HDR,
            )
            self.assertEqual(r.status_code, 422)


class TestImportDuplicateFilenames(unittest.TestCase):
    """Important #4: duplicate filenames in one import are rejected."""

    def test_rejects_duplicate_filenames(self):
        with _client() as (c, tmp):
            vol = make_sample_volume(tmp)
            pid = _create_project(c, vol)
            labels = tmp / "labels.jsonl"
            _write_jsonl(labels, [
                {"filename": "a.jpg", "annotations": [
                    {"label": "cat", "ann_type": "classification"}]},
                {"filename": "a.jpg", "annotations": [
                    {"label": "dog", "ann_type": "classification"}]},
            ])
            r = c.post(
                f"/api/projects/{pid}/import",
                json={"volume_path": str(labels), "format": "jsonl"},
                headers=BYPASS_HDR,
            )
            self.assertEqual(r.status_code, 422)
            errs = r.json()["errors"]
            self.assertTrue(any("duplicate" in e["reason"].lower() for e in errs))


class TestImportReplaceWithZeroAnnotations(unittest.TestCase):
    """Important #7: replace-with-zero-annotations sets status=unlabeled."""

    def test_replace_with_zero_anns_sets_unlabeled(self):
        with _client() as (c, tmp):
            vol = make_sample_volume(tmp)
            pid = _create_project(c, vol)

            first = tmp / "first.jsonl"
            _write_jsonl(first, [
                {"filename": "a.jpg", "annotations": [
                    {"label": "cat", "ann_type": "classification"}]},
            ])
            r = c.post(
                f"/api/projects/{pid}/import",
                json={"volume_path": str(first), "format": "jsonl"},
                headers=BYPASS_HDR,
            )
            self.assertEqual(r.status_code, 200)

            second = tmp / "second.jsonl"
            _write_jsonl(second, [{"filename": "a.jpg", "annotations": []}])
            r = c.post(
                f"/api/projects/{pid}/import",
                json={
                    "volume_path": str(second),
                    "format": "jsonl",
                    "on_existing_annotations": "replace",
                },
                headers=BYPASS_HDR,
            )
            self.assertEqual(r.status_code, 200)

            r = c.get(f"/api/projects/{pid}/samples?limit=10")
            items = r.json()["items"]
            a_row = next(it for it in items if it["filename"] == "a.jpg")
            self.assertEqual(a_row["status"], "unlabeled")


class TestImportBboxInvariants(unittest.TestCase):
    """Important #8: bbox must fit inside [0,1] and have positive size."""

    def test_rejects_bbox_out_of_frame(self):
        with _client() as (c, tmp):
            vol = make_sample_volume(tmp)
            pid = _create_project(c, vol, task_type="detection",
                                  class_list=["cat"])
            labels = tmp / "labels.jsonl"
            _write_jsonl(labels, [
                {"filename": "a.jpg", "annotations": [{
                    "label": "cat", "ann_type": "bbox",
                    "bbox_json": {"x": 0.8, "y": 0.8, "w": 0.5, "h": 0.5},
                }]},
            ])
            r = c.post(
                f"/api/projects/{pid}/import",
                json={"volume_path": str(labels), "format": "jsonl"},
                headers=BYPASS_HDR,
            )
            self.assertEqual(r.status_code, 422)

    def test_rejects_zero_size_bbox(self):
        with _client() as (c, tmp):
            vol = make_sample_volume(tmp)
            pid = _create_project(c, vol, task_type="detection",
                                  class_list=["cat"])
            labels = tmp / "labels.jsonl"
            _write_jsonl(labels, [
                {"filename": "a.jpg", "annotations": [{
                    "label": "cat", "ann_type": "bbox",
                    "bbox_json": {"x": 0.1, "y": 0.1, "w": 0.0, "h": 0.2},
                }]},
            ])
            r = c.post(
                f"/api/projects/{pid}/import",
                json={"volume_path": str(labels), "format": "jsonl"},
                headers=BYPASS_HDR,
            )
            self.assertEqual(r.status_code, 422)


class TestImportFilenameNormalization(unittest.TestCase):
    """Important #6: filenames must be basenames (no separators / ..)."""

    def test_rejects_filename_with_slash(self):
        with _client() as (c, tmp):
            vol = make_sample_volume(tmp)
            pid = _create_project(c, vol)
            labels = tmp / "labels.jsonl"
            _write_jsonl(labels, [
                {"filename": "sub/a.jpg", "annotations": [
                    {"label": "cat", "ann_type": "classification"}]},
            ])
            r = c.post(
                f"/api/projects/{pid}/import",
                json={"volume_path": str(labels), "format": "jsonl"},
                headers=BYPASS_HDR,
            )
            self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
