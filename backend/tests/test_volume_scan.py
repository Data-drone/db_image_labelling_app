"""Bounded recursive volume scanning and matching preview counts."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import Base, LabelingProject, ProjectSample
from backend.tests.conftest import test_client
from backend.volumes import (
    MAX_SCAN_IMAGES,
    discover_images,
    effective_scan_depth,
    iter_images,
    scan_volume_for_samples,
)


def _touch_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8\xff\xd9")


def _tree(root: Path) -> None:
    _touch_image(root / "top.jpg")
    _touch_image(root / "notes.txt")  # ignored
    _touch_image(root / "a" / "cat.jpg")
    _touch_image(root / "b" / "cat.jpg")
    _touch_image(root / "a" / "deep" / "inner.jpg")
    _touch_image(root / "a" / "deep" / "deeper" / "too_deep.jpg")


class EffectiveDepthTests(unittest.TestCase):
    def test_off_is_selected_directory_only(self):
        self.assertEqual(effective_scan_depth(False, 8), 0)

    def test_on_clamps_to_max(self):
        self.assertEqual(effective_scan_depth(True, 99), 8)
        self.assertEqual(effective_scan_depth(True, -2), 0)
        self.assertEqual(effective_scan_depth(True, None), 3)


class DiscoverImagesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        _tree(self.root)

    def test_default_is_top_level_only(self):
        names = [img.filename for img in iter_images(str(self.root), max_depth=0)]
        self.assertEqual(names, ["top.jpg"])

    def test_nested_and_duplicate_basenames(self):
        found = discover_images(str(self.root), max_depth=3)
        names = sorted(img.filename for img in found.images)
        self.assertIn("top.jpg", names)
        self.assertIn("a/cat.jpg", names)
        self.assertIn("b/cat.jpg", names)
        self.assertIn("a/deep/inner.jpg", names)
        self.assertIn("a/deep/deeper/too_deep.jpg", names)
        self.assertEqual(len([n for n in names if n.endswith("cat.jpg")]), 2)

    def test_depth_limit_skips_deeper_files(self):
        found = discover_images(str(self.root), max_depth=1)
        names = {img.filename for img in found.images}
        self.assertIn("a/cat.jpg", names)
        self.assertIn("b/cat.jpg", names)
        self.assertNotIn("a/deep/inner.jpg", names)
        self.assertNotIn("a/deep/deeper/too_deep.jpg", names)

    def test_listing_failure_keeps_partial_results(self):
        real_scandir = os.scandir

        def fake_scandir(path):
            if Path(path).name == "b":
                raise OSError("permission denied: b")
            return real_scandir(path)

        with mock.patch("os.scandir", side_effect=fake_scandir):
            found = discover_images(str(self.root), max_depth=2)
        names = {img.filename for img in found.images}
        self.assertIn("top.jpg", names)
        self.assertIn("a/cat.jpg", names)
        self.assertNotIn("b/cat.jpg", names)
        self.assertIn("permission denied", found.error or "")


class ScanVolumeForSamplesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        _tree(self.root)
        engine = create_engine(f"sqlite:///{self.root / 'scan.db'}")
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        with self.Session() as db:
            project = LabelingProject(
                name="scan",
                task_type="classification",
                class_list=["cat"],
                source_volume=str(self.root),
            )
            db.add(project)
            db.commit()
            self.project_id = project.id

    def test_rescan_does_not_duplicate(self):
        with self.Session() as db:
            first = scan_volume_for_samples(
                db, self.project_id, str(self.root), include_subfolders=True, max_depth=3,
            )
            db.commit()
        with self.Session() as db:
            second = scan_volume_for_samples(
                db, self.project_id, str(self.root), include_subfolders=True, max_depth=3,
            )
            db.commit()
            rows = db.query(ProjectSample).filter_by(project_id=self.project_id).all()
        self.assertGreater(first, 1)
        self.assertEqual(second, 0)
        filenames = sorted(r.filename for r in rows)
        self.assertEqual(len(filenames), len(set(filenames)))
        self.assertEqual(len({r.filepath for r in rows}), len(rows))

    def test_top_level_default_skips_nested(self):
        with self.Session() as db:
            added = scan_volume_for_samples(db, self.project_id, str(self.root))
            db.commit()
            rows = db.query(ProjectSample).filter_by(project_id=self.project_id).all()
        self.assertEqual(added, 1)
        self.assertEqual([r.filename for r in rows], ["top.jpg"])


class ProjectScanApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        _tree(self.root)
        self.context = test_client(self.root)
        self.client, _, _ = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def _create(self, **over):
        body = {
            "name": over.pop("name", "nested-scan"),
            "task_type": "classification",
            "class_list": ["cat"],
            "source_volume": str(self.root),
        }
        body.update(over)
        created = self.client.post("/api/projects", json=body)
        self.assertEqual(created.status_code, 200, created.text)
        return created.json()

    def test_create_default_stays_top_level(self):
        project = self._create()
        self.assertFalse(project["scan_include_subfolders"])
        samples = self.client.get(
            f"/api/projects/{project['id']}/samples", params={"page_size": 100},
        ).json()
        names = {row["filename"] for row in samples["items"]}
        self.assertEqual(names, {"top.jpg"})

    def test_create_recursive_matches_preview_and_survives_sync(self):
        preview = discover_images(str(self.root), max_depth=2)
        project = self._create(
            name="deep",
            scan_include_subfolders=True,
            scan_max_depth=2,
        )
        self.assertTrue(project["scan_include_subfolders"])
        self.assertEqual(project["scan_max_depth"], 2)
        samples = self.client.get(
            f"/api/projects/{project['id']}/samples", params={"page_size": 100},
        ).json()
        names = {row["filename"] for row in samples["items"]}
        expected = {img.filename for img in preview.images}
        self.assertEqual(names, expected)
        self.assertIn("a/cat.jpg", names)
        self.assertIn("b/cat.jpg", names)
        self.assertNotIn("a/deep/deeper/too_deep.jpg", names)

        synced = self.client.post(f"/api/projects/{project['id']}/sync-source")
        self.assertEqual(synced.status_code, 200, synced.text)
        self.assertEqual(synced.json()["added"], 0)
        again = self.client.get(
            f"/api/projects/{project['id']}/samples", params={"page_size": 100},
        ).json()
        self.assertEqual(len(again["items"]), len(names))

    def test_enabling_subfolders_on_existing_project_adds_nested(self):
        project = self._create(name="later")
        patched = self.client.patch(
            f"/api/projects/{project['id']}",
            json={"scan_include_subfolders": True, "scan_max_depth": 3},
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        samples = self.client.get(
            f"/api/projects/{project['id']}/samples", params={"page_size": 100},
        ).json()
        names = {row["filename"] for row in samples["items"]}
        self.assertIn("top.jpg", names)
        self.assertIn("a/cat.jpg", names)

    def test_clone_keeps_scan_flags(self):
        project = self._create(
            name="to-clone",
            scan_include_subfolders=True,
            scan_max_depth=1,
        )
        cloned = self.client.post(f"/api/projects/{project['id']}/clone")
        self.assertEqual(cloned.status_code, 200, cloned.text)
        self.assertTrue(cloned.json()["scan_include_subfolders"])
        self.assertEqual(cloned.json()["scan_max_depth"], 1)


class BrowseCountApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        _tree(self.root)
        self.context = test_client(self.root)
        self.client, _, _ = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def test_count_matches_discover_and_browse_stays_shallow(self):
        listed = self.client.get("/api/browse", params={"path": str(self.root)})
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["total_files"], 1)
        for folder in listed.json()["folders"]:
            self.assertEqual(folder["image_count"], 0)

        shallow = self.client.get("/api/browse/count", params={"path": str(self.root)})
        self.assertEqual(shallow.status_code, 200, shallow.text)
        self.assertEqual(shallow.json()["image_count"], 1)
        self.assertFalse(shallow.json()["include_subfolders"])

        deep = self.client.get(
            "/api/browse/count",
            params={"path": str(self.root), "include_subfolders": "true", "max_depth": 3},
        )
        self.assertEqual(deep.status_code, 200, deep.text)
        expected = len(discover_images(str(self.root), max_depth=3).images)
        self.assertEqual(deep.json()["image_count"], expected)
        self.assertGreater(expected, 1)


class TruncationTests(unittest.TestCase):
    def test_limit_sets_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(3):
                _touch_image(root / f"{i}.jpg")
            found = discover_images(str(root), max_depth=0, limit=2)
            self.assertEqual(len(found.images), 2)
            self.assertTrue(found.truncated)
            self.assertLess(2, MAX_SCAN_IMAGES)
