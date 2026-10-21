"""Archive, restore, and delete labeling projects."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.models import ClusterMapPointCache, ClusterMapRun, PreannotateRun
from backend.tests.conftest import make_sample_volume, test_client


class TestProjectLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.volume = make_sample_volume(self.root)
        self.context = test_client(self.root)
        self.client, self.main, _ = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def _create(self, name="lifecycle"):
        created = self.client.post(
            "/api/projects",
            json={
                "name": name,
                "task_type": "classification",
                "class_list": ["cat", "dog"],
                "source_volume": str(self.volume),
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        return created.json()

    def test_archive_hides_from_default_list_and_unarchive_restores(self):
        project = self._create()
        pid = project["id"]
        self.assertIsNone(project.get("archived_at"))

        listed = self.client.get("/api/projects")
        self.assertEqual(listed.status_code, 200)
        self.assertIn(pid, {row["id"] for row in listed.json()})

        archived = self.client.post(f"/api/projects/{pid}/archive")
        self.assertEqual(archived.status_code, 200, archived.text)
        self.assertIsNotNone(archived.json()["archived_at"])

        hidden = self.client.get("/api/projects")
        self.assertNotIn(pid, {row["id"] for row in hidden.json()})

        shown = self.client.get("/api/projects", params={"include_archived": True})
        self.assertIn(pid, {row["id"] for row in shown.json()})

        restored = self.client.post(f"/api/projects/{pid}/unarchive")
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertIsNone(restored.json()["archived_at"])
        visible = self.client.get("/api/projects")
        self.assertIn(pid, {row["id"] for row in visible.json()})

    def test_delete_removes_project_and_related_runs(self):
        from backend import deps

        project = self._create("delete-me")
        pid = project["id"]
        with deps.get_session_factory()() as db:
            db.add(PreannotateRun(project_id=pid, status="succeeded"))
            db.add(ClusterMapRun(project_id=pid, status="ready", sample_count=0))
            db.commit()

        deleted = self.client.delete(f"/api/projects/{pid}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        missing = self.client.get(f"/api/projects/{pid}")
        self.assertEqual(missing.status_code, 404)
        listed = self.client.get("/api/projects", params={"include_archived": True})
        self.assertNotIn(pid, {row["id"] for row in listed.json()})

        with deps.get_session_factory()() as db:
            leftover = db.query(PreannotateRun).filter_by(project_id=pid).count()
            self.assertEqual(leftover, 0)
            self.assertEqual(db.query(ClusterMapRun).filter_by(project_id=pid).count(), 0)
            self.assertEqual(db.query(ClusterMapPointCache).filter_by(project_id=pid).count(), 0)

    def test_non_owner_cannot_archive_or_delete_ordinary_project(self):
        created = self.client.post(
            "/api/projects",
            headers={"X-Forwarded-Email": "owner@example.com"},
            json={
                "name": "ordinary",
                "task_type": "classification",
                "class_list": ["cat"],
                "source_volume": str(self.volume),
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        pid = created.json()["id"]
        headers = {"X-Forwarded-Email": "intruder@example.com"}
        archive = self.client.post(f"/api/projects/{pid}/archive", headers=headers)
        self.assertEqual(archive.status_code, 403)
        unarchive = self.client.post(f"/api/projects/{pid}/unarchive", headers=headers)
        self.assertEqual(unarchive.status_code, 403)
        delete = self.client.delete(f"/api/projects/{pid}", headers=headers)
        self.assertEqual(delete.status_code, 403)
        still_there = self.client.get(f"/api/projects/{pid}")
        self.assertEqual(still_there.status_code, 200)

        owned = self.client.post(
            f"/api/projects/{pid}/archive",
            headers={"X-Forwarded-Email": "owner@example.com"},
        )
        self.assertEqual(owned.status_code, 200, owned.text)

    def test_consensus_non_owner_cannot_archive_or_delete(self):
        created = self.client.post(
            "/api/projects",
            headers={"X-Forwarded-Email": "owner@example.com"},
            json={
                "name": "owned",
                "task_type": "classification",
                "class_list": ["cat"],
                "source_volume": str(self.volume),
                "consensus_enabled": True,
                "consensus_reviewer_emails": ["reviewer@example.com"],
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        pid = created.json()["id"]
        headers = {"X-Forwarded-Email": "reviewer@example.com"}
        archive = self.client.post(f"/api/projects/{pid}/archive", headers=headers)
        self.assertEqual(archive.status_code, 403)
        delete = self.client.delete(f"/api/projects/{pid}", headers=headers)
        self.assertEqual(delete.status_code, 403)

    def test_all_versions_archive_and_delete_cover_clones(self):
        root = self._create("lineage")
        cloned = self.client.post(f"/api/projects/{root['id']}/clone")
        self.assertEqual(cloned.status_code, 200, cloned.text)
        child_id = cloned.json()["id"]

        archived = self.client.post(
            f"/api/projects/{root['id']}/archive",
            params={"all_versions": True},
        )
        self.assertEqual(archived.status_code, 200, archived.text)
        hidden = self.client.get("/api/projects")
        ids = {row["id"] for row in hidden.json()}
        self.assertNotIn(root["id"], ids)
        self.assertNotIn(child_id, ids)

        deleted = self.client.delete(
            f"/api/projects/{root['id']}",
            params={"all_versions": True},
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        listed = self.client.get("/api/projects", params={"include_archived": True})
        leftover = {row["id"] for row in listed.json()}
        self.assertNotIn(root["id"], leftover)
        self.assertNotIn(child_id, leftover)
