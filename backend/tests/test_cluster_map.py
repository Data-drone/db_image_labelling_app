"""HTTP tests for Cluster Map GET: cache, 202 background compute, errors."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.models import ClusterMapPointCache, ClusterMapRun, ProjectSample
from backend.tests.conftest import make_sample_volume, test_client


def _tiny_embedding(i: int, dim: int = 8) -> list[float]:
    vec = [0.0] * dim
    vec[i % dim] = 1.0
    vec[(i + 1) % dim] = 0.25
    return vec


# Projection stand-ins run in a spawned subprocess, so they must be
# module-level (picklable by reference) rather than mocks or bound methods.
def _fake_coords(embeddings):
    n = len(embeddings)
    return [(i / max(n - 1, 1), 0.5) for i in range(n)]


def _slow_coords(embeddings):
    time.sleep(60)
    return _fake_coords(embeddings)


def _exploding_coords(embeddings):
    raise ValueError("UMAP exploded")


class ClusterMapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.volume = make_sample_volume(self.root)
        self.context = test_client(self.root)
        self.client, self.main, _ = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

        created = self.client.post(
            "/api/projects",
            json={
                "name": "cluster-map",
                "task_type": "classification",
                "class_list": ["hat"],
                "source_volume": str(self.volume),
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        self.pid = created.json()["id"]

    def _session(self):
        from backend import deps
        return deps.get_session_factory()()

    def _embed(self, extra: int = 0):
        db = self._session()
        try:
            samples = (
                db.query(ProjectSample)
                .filter_by(project_id=self.pid)
                .order_by(ProjectSample.id)
                .all()
            )
            for i, sample in enumerate(samples):
                sample.embedding = _tiny_embedding(i)
            next_i = len(samples)
            for j in range(extra):
                db.add(
                    ProjectSample(
                        project_id=self.pid,
                        filepath=f"{self.volume}/extra-{j}.jpg",
                        filename=f"extra-{j}.jpg",
                        status="unlabeled",
                        embedding=_tiny_embedding(next_i + j),
                    )
                )
            db.commit()
        finally:
            db.close()

    def _get(self, force: bool = False):
        params = {"force": True} if force else None
        return self.client.get(f"/api/projects/{self.pid}/cluster-map", params=params)

    def _get_and_run_worker(self, projection=_fake_coords, force: bool = False):
        """GET with the thread start patched, then run the worker synchronously."""
        from backend.cluster_map import run_cluster_map_worker

        with patch("backend.cluster_map.start_cluster_map_worker") as start:
            resp = self._get(force=force)
        self.assertEqual(resp.status_code, 202, resp.text)
        start.assert_called_once()
        with patch("backend.cluster_map.compute_umap_projection", projection):
            run_cluster_map_worker(start.call_args.args[0], self.pid)
        return resp

    def test_no_embeddings_is_400(self):
        resp = self._get()
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertIn("No embeddings", resp.json()["detail"])

    def test_fewer_than_three_embeddings_is_400(self):
        db = self._session()
        try:
            sample = (
                db.query(ProjectSample)
                .filter_by(project_id=self.pid)
                .order_by(ProjectSample.id)
                .first()
            )
            sample.embedding = _tiny_embedding(0)
            db.commit()
        finally:
            db.close()
        resp = self._get()
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertIn("at least 3", resp.json()["detail"])

    def test_miss_returns_202_without_running_umap_on_request(self):
        self._embed()
        with patch("backend.cluster_map.start_cluster_map_worker") as start:
            with patch("backend.cluster_map.project_embeddings") as project:
                t0 = time.perf_counter()
                resp = self._get()
                elapsed = time.perf_counter() - t0
        self.assertEqual(resp.status_code, 202, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "computing")
        self.assertGreaterEqual(body["sample_count"], 3)
        self.assertLess(elapsed, 1.0)
        start.assert_called_once()
        project.assert_not_called()

    def test_cache_hit_skips_projection(self):
        self._embed()
        from backend.cluster_map import embedded_sample_ids, fingerprint_ids

        db = self._session()
        try:
            ids = embedded_sample_ids(db, self.pid)
            for i, sid in enumerate(ids):
                db.add(ClusterMapPointCache(
                    project_id=self.pid, sample_id=sid, x=0.1 * i, y=0.2,
                ))
            db.add(ClusterMapRun(
                project_id=self.pid, status="ready", sample_count=len(ids),
                sample_fingerprint=fingerprint_ids(ids),
            ))
            db.commit()
        finally:
            db.close()

        with patch("backend.cluster_map.start_cluster_map_worker") as start:
            resp = self._get()
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["cached"])
        self.assertEqual(body["total"], 3)
        self.assertIsNone(body["recompute_error"])
        start.assert_not_called()

    def test_cache_without_ready_run_is_a_miss(self):
        self._embed()
        db = self._session()
        try:
            sample = (
                db.query(ProjectSample)
                .filter_by(project_id=self.pid)
                .order_by(ProjectSample.id)
                .first()
            )
            db.add(ClusterMapPointCache(
                project_id=self.pid, sample_id=sample.id, x=0.1, y=0.2,
            ))
            db.commit()
        finally:
            db.close()

        with patch("backend.cluster_map.start_cluster_map_worker"):
            resp = self._get()
        self.assertEqual(resp.status_code, 202, resp.text)

    def test_worker_persists_points_and_second_get_is_cached(self):
        self._embed()
        self._get_and_run_worker()

        second = self._get()
        self.assertEqual(second.status_code, 200, second.text)
        body = second.json()
        self.assertTrue(body["cached"])
        self.assertEqual(len(body["points"]), 3)
        xs = [p["x"] for p in body["points"]]
        self.assertEqual(xs[0], 0.0)
        self.assertEqual(xs[-1], 1.0)

    def test_new_embeddings_invalidate_cache(self):
        self._embed()
        self._get_and_run_worker()
        self.assertEqual(self._get().status_code, 200)

        self._embed(extra=1)
        with patch("backend.cluster_map.start_cluster_map_worker") as start:
            resp = self._get()
        self.assertEqual(resp.status_code, 202, resp.text)
        start.assert_called_once()

    def test_unparseable_embedding_does_not_restart_projection(self):
        self._embed(extra=1)
        db = self._session()
        try:
            sample = (
                db.query(ProjectSample)
                .filter_by(project_id=self.pid)
                .order_by(ProjectSample.id.desc())
                .first()
            )
            sample.embedding = []
            db.commit()
        finally:
            db.close()

        self._get_and_run_worker()

        with patch("backend.cluster_map.start_cluster_map_worker") as start:
            resp = self._get()
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["total"], 3)
        start.assert_not_called()

    def test_failed_run_returns_409_until_force(self):
        self._embed()
        from backend.cluster_map import embedded_sample_ids, fingerprint_ids

        db = self._session()
        try:
            ids = embedded_sample_ids(db, self.pid)
            db.add(ClusterMapRun(
                project_id=self.pid,
                status="failed",
                sample_count=len(ids),
                sample_fingerprint=fingerprint_ids(ids),
                error_message="UMAP exploded",
            ))
            db.commit()
        finally:
            db.close()

        blocked = self._get()
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertIn("UMAP exploded", blocked.json()["detail"])

        with patch("backend.cluster_map.start_cluster_map_worker") as start:
            retry = self._get(force=True)
        self.assertEqual(retry.status_code, 202, retry.text)
        start.assert_called_once()

    def test_projection_error_is_reported(self):
        self._embed()
        self._get_and_run_worker(projection=_exploding_coords)
        failed = self._get()
        self.assertEqual(failed.status_code, 409, failed.text)
        self.assertIn("UMAP exploded", failed.json()["detail"])

    def test_failed_recompute_keeps_previous_map(self):
        self._embed()
        self._get_and_run_worker()

        with patch("backend.cluster_map.start_cluster_map_worker") as start:
            forced = self._get(force=True)
            while_running = self._get()
        self.assertEqual(forced.status_code, 202, forced.text)
        self.assertEqual(while_running.status_code, 202, while_running.text)

        db = self._session()
        try:
            self.assertEqual(
                db.query(ClusterMapPointCache).filter_by(project_id=self.pid).count(), 3,
            )
        finally:
            db.close()

        from backend.cluster_map import run_cluster_map_worker
        with patch("backend.cluster_map.compute_umap_projection", _exploding_coords):
            run_cluster_map_worker(start.call_args.args[0], self.pid)

        after = self._get()
        self.assertEqual(after.status_code, 200, after.text)
        body = after.json()
        self.assertEqual(body["total"], 3)
        self.assertIn("UMAP exploded", body["recompute_error"])

    def test_concurrent_gets_share_one_worker(self):
        self._embed()
        with patch("backend.cluster_map.start_cluster_map_worker") as start:
            a = self._get()
            b = self._get()
        self.assertEqual(a.status_code, 202)
        self.assertEqual(b.status_code, 202)
        self.assertEqual(start.call_count, 1)

    def test_force_does_not_start_second_worker_while_running(self):
        self._embed()
        with patch("backend.cluster_map.start_cluster_map_worker") as start:
            first = self._get()
            forced = self._get(force=True)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(forced.status_code, 202)
        self.assertEqual(start.call_count, 1)

    def test_active_run_is_not_marked_stale(self):
        from datetime import datetime, timedelta, timezone

        from backend import cluster_map

        run = ClusterMapRun(
            id=12345, project_id=self.pid, status="running",
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        self.assertTrue(cluster_map.run_is_stale(run))
        with patch.object(cluster_map, "_active_runs", {12345}):
            self.assertFalse(cluster_map.run_is_stale(run))

    def test_thousand_sample_get_returns_before_projection(self):
        self._embed(extra=997)
        with patch("backend.cluster_map.start_cluster_map_worker"):
            with patch("backend.cluster_map.project_embeddings") as project:
                t0 = time.perf_counter()
                resp = self._get()
                elapsed = time.perf_counter() - t0
        self.assertEqual(resp.status_code, 202, resp.text)
        self.assertEqual(resp.json()["sample_count"], 1000)
        self.assertLess(elapsed, 2.0)
        project.assert_not_called()

    def test_fevm_style_1257_cache_roundtrip(self):
        """Same sequence as #55: first GET 202, poll ready, second GET cached, force recomputes."""
        self._embed(extra=1254)
        t0 = time.perf_counter()
        first = self._get_and_run_worker()
        self.assertEqual(first.json()["sample_count"], 1257)

        cached = self._get()
        self.assertEqual(cached.status_code, 200, cached.text)
        self.assertTrue(cached.json()["cached"])
        self.assertEqual(cached.json()["total"], 1257)
        self.assertLess(time.perf_counter() - t0, 30.0)

        with patch("backend.cluster_map.start_cluster_map_worker") as start:
            forced = self._get(force=True)
        self.assertEqual(forced.status_code, 202, forced.text)
        start.assert_called_once()

    def test_background_thread_completes_without_blocking_get(self):
        self._embed()
        with patch("backend.cluster_map.compute_umap_projection", _fake_coords):
            first = self._get()
            self.assertEqual(first.status_code, 202, first.text)
            deadline = time.time() + 30
            status = first.status_code
            body = first.json()
            while time.time() < deadline:
                resp = self._get()
                status = resp.status_code
                body = resp.json()
                if status == 200:
                    break
                self.assertEqual(status, 202, resp.text)
                time.sleep(0.1)
        self.assertEqual(status, 200, body)
        self.assertTrue(body.get("cached"))
        self.assertEqual(len(body["points"]), 3)

    def test_worker_timeout_kills_projection_and_marks_failed(self):
        self._embed()
        t0 = time.perf_counter()
        with patch("backend.cluster_map.WORKER_TIMEOUT_SEC", 1.0):
            self._get_and_run_worker(projection=_slow_coords)
        self.assertLess(time.perf_counter() - t0, 20.0)

        failed = self._get()
        self.assertEqual(failed.status_code, 409, failed.text)
        self.assertIn("timed out", failed.json()["detail"].lower())


if __name__ == "__main__":
    unittest.main()
