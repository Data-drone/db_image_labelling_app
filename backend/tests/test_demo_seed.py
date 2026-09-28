"""Tests for the optional startup demo seeder.

Uses a local directory as the "volume" -- ``scan_volume_for_samples`` accepts
a plain path when it is not a ``/Volumes/...`` one, so these tests need no
network and no UC.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.demo_seed import _parse, seed_demo_projects
from backend.models import Base, LabelingProject, ProjectSample


def _images(dir_path: Path, n: int) -> None:
    for i in range(n):
        (dir_path / f"img_{i:02d}.jpg").write_bytes(b"not-a-real-jpeg")
    (dir_path / "notes.txt").write_text("ignored: not an image")


class SeedSpecParsingTests(unittest.TestCase):
    def test_parses_single_entry(self):
        got = _parse("Demo|segmentation|scratch, corrosion |/Volumes/a/b/c")
        self.assertEqual(got, [{
            "name": "Demo",
            "task_type": "segmentation",
            "class_list": ["scratch", "corrosion"],
            "source_volume": "/Volumes/a/b/c",
        }])

    def test_parses_multiple_entries(self):
        got = _parse("A|detection|x|/v/a ; B|classification|y,z|/v/b")
        self.assertEqual([g["name"] for g in got], ["A", "B"])
        self.assertEqual(got[1]["class_list"], ["y", "z"])

    def test_drops_malformed_entries_without_failing(self):
        got = _parse("too|few|fields ; ok|detection|a|/v/a ; |detection|a|/v/a ;;")
        self.assertEqual([g["name"] for g in got], ["ok"])

    def test_drops_entry_with_no_classes(self):
        self.assertEqual(_parse("A|detection| , |/v/a"), [])


class SeedDemoProjectsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.images = self.tmp / "images"
        self.images.mkdir()
        engine = create_engine(f"sqlite:///{self.tmp / 'seed.db'}")
        Base.metadata.create_all(engine)
        self.sf = sessionmaker(bind=engine)
        self._prev = os.environ.get("SEED_DEMO_PROJECTS")

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("SEED_DEMO_PROJECTS", None)
        else:
            os.environ["SEED_DEMO_PROJECTS"] = self._prev
        self._tmp.cleanup()

    def _seed(self, spec):
        os.environ["SEED_DEMO_PROJECTS"] = spec
        seed_demo_projects(self.sf)

    def _spec(self, name="Demo"):
        return f"{name}|segmentation|scratch,corrosion|{self.images}"

    def test_noop_when_unset(self):
        os.environ.pop("SEED_DEMO_PROJECTS", None)
        seed_demo_projects(self.sf)
        with self.sf() as db:
            self.assertEqual(db.query(LabelingProject).count(), 0)

    def test_creates_project_and_samples(self):
        _images(self.images, 3)
        self._seed(self._spec())
        with self.sf() as db:
            p = db.query(LabelingProject).one()
            self.assertEqual(p.task_type, "segmentation")
            self.assertEqual(p.class_list, ["scratch", "corrosion"])
            self.assertEqual(p.created_by, "demo-seed")
            # Only the images, not notes.txt.
            self.assertEqual(db.query(ProjectSample).count(), 3)

    def test_idempotent_and_non_destructive(self):
        _images(self.images, 2)
        self._seed(self._spec())
        with self.sf() as db:
            db.query(ProjectSample).delete()  # stand in for user edits
            db.commit()
        self._seed(self._spec())
        with self.sf() as db:
            self.assertEqual(db.query(LabelingProject).count(), 1)
            # A second run must not re-add samples to an existing project.
            self.assertEqual(db.query(ProjectSample).count(), 0)

    def test_empty_source_leaves_no_half_built_project(self):
        self._seed(self._spec())  # images dir is empty
        with self.sf() as db:
            self.assertEqual(db.query(LabelingProject).count(), 0)

    def test_missing_source_is_survivable(self):
        self._seed(f"Demo|segmentation|a|{self.tmp / 'nope'}")
        with self.sf() as db:
            self.assertEqual(db.query(LabelingProject).count(), 0)

    def test_seeds_remaining_projects_after_one_fails(self):
        _images(self.images, 1)
        self._seed(f"Bad|segmentation|a|{self.tmp / 'nope'} ; {self._spec('Good')}")
        with self.sf() as db:
            self.assertEqual([p.name for p in db.query(LabelingProject).all()], ["Good"])


if __name__ == "__main__":
    unittest.main()
