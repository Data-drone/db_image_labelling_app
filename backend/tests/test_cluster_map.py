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

    def _fake_coords(self, embeddings):
        n = len(embeddings)
        return [(i / max(n - 1, 1), 0.5) for i in range(n)]

    def test_no_embeddings_is_400(self):
        resp = self.client.get(f"/api/projects/{self.pid}/cluster-map")
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
        resp = self.client.get(f"/api/projects/{self.pid}/cluster-map")
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertIn("at least 3", resp.json()["detail"])

    def test_miss_returns_202_without_running_umap_on_request(self):
        self._embed()
        with patch("backend.cluster_map.start_cluster_map_worker") as start:
            with patch("backend.cluster_map.compute_umap_projection") as umap:
                t0 = time.perf_counter()
                resp = self.client.get(f"/api/projects/{self.pid}/cluster-map")
                elapsed = time.perf_counter() - t0
        self.assertEqual(resp.status_code, 202, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "computing")
        self.assertGreaterEqual(body["sample_count"], 3)
        self.assertLess(elapsed, 1.0)
        start.assert_called_once()
        umap.assert_not_called()

    def test_cache_hit_skips_projection(self):
        self._embed()
        db = self._session()
        try:
            samples = (
                db.query(ProjectSample)
                .filter_by(project_id=self.pid)
                .order_by(ProjectSample.id)
                .all()
            )
            for i, s in enumerate(samples):
                db.add(ClusterMapPointCache(
                    project_id=self.pid, sample_id=s.id, x=0.1 * i, y=0.2,
                ))
            db.add(ClusterMapRun(
                project_id=self.pid, status="ready", sample_count=len(samples),
            ))
            db.commit()
        finally:
            db.close()

        with patch("backend.cluster_map.start_cluster_map_worker") as start:
            with patch("backend.cluster_map.compute_umap_projection") as umap:
                resp = self.client.get(f"/api/projects/{self.pid}/cluster-map")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["cached"])
        self.assertEqual(body["total"], 3)
        start.assert_not_called()
        umap.assert_not_called()

    def test_partial_cache_is_a_miss(self):
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
            resp = self.client.get(f"/api/projects/{self.pid}/cluster-map")
        self.assertEqual(resp.status_code, 202, resp.text)

    def test_worker_persists_points_and_second_get_is_cached(self):
        self._embed()
        with patch(
            "backend.cluster_map.compute_umap_projection",
            side_effect=self._fake_coords,
        ):
            with patch("backend.cluster_map.start_cluster_map_worker") as start:
                first = self.client.get(f"/api/projects/{self.pid}/cluster-map")
                self.assertEqual(first.status_code, 202, first.text)
                run_id = start.call_args.args[0]
                from backend.cluster_map import run_cluster_map_worker
                run_cluster_map_worker(run_id, self.pid)

        second = self.client.get(f"/api/projects/{self.pid}/cluster-map")
        self.assertEqual(second.status_code, 200, second.text)
        body = second.json()
        self.assertTrue(body["cached"])
        self.assertEqual(len(body["points"]), 3)
        xs = [p["x"] for p in body["points"]]
        self.assertEqual(xs[0], 0.0)
        self.assertEqual(xs[-1], 1.0)

    def test_failed_run_returns_409_until_force(self):
        self._embed()
        from backend.cluster_map import fingerprint_ids, embedded_sample_ids

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

        blocked = self.client.get(f"/api/projects/{self.pid}/cluster-map")
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertIn("UMAP exploded", blocked.json()["detail"])

        with patch("backend.cluster_map.start_cluster_map_worker") as start:
            retry = self.client.get(
                f"/api/projects/{self.pid}/cluster-map", params={"force": True},
            )
        self.assertEqual(retry.status_code, 202, retry.text)
        start.assert_called_once()

    def test_concurrent_gets_share_one_worker(self):
        self._embed()
        with patch("backend.cluster_map.start_cluster_map_worker") as start:
            a = self.client.get(f"/api/projects/{self.pid}/cluster-map")
            b = self.client.get(f"/api/projects/{self.pid}/cluster-map")
        self.assertEqual(a.status_code, 202)
        self.assertEqual(b.status_code, 202)
        self.assertEqual(start.call_count, 1)

    def test_force_does_not_start_second_worker_while_running(self):
        self._embed()
        with patch("backend.cluster_map.start_cluster_map_worker") as start:
            first = self.client.get(f"/api/projects/{self.pid}/cluster-map")
            forced = self.client.get(
                f"/api/projects/{self.pid}/cluster-map", params={"force": True},
            )
        self.assertEqual(first.status_code, 202)
        self.assertEqual(forced.status_code, 202)
        self.assertEqual(start.call_count, 1)

    def test_thousand_sample_get_returns_before_projection(self):
        self._embed(extra=997)
        with patch("backend.cluster_map.start_cluster_map_worker"):
            with patch(
                "backend.cluster_map.compute_umap_projection",
                side_effect=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("UMAP ran")),
            ):
                t0 = time.perf_counter()
                resp = self.client.get(f"/api/projects/{self.pid}/cluster-map")
                elapsed = time.perf_counter() - t0
        self.assertEqual(resp.status_code, 202, resp.text)
        self.assertEqual(resp.json()["sample_count"], 1000)
        self.assertLess(elapsed, 2.0)

    def test_fevm_style_1257_cache_roundtrip(self):
        """Same sequence as #55: first GET 202, poll ready, second GET cached, force recomputes."""
        self._embed(extra=1254)
        with patch(
            "backend.cluster_map.compute_umap_projection",
            side_effect=self._fake_coords,
        ):
            with patch("backend.cluster_map.start_cluster_map_worker") as start:
                t0 = time.perf_counter()
                first = self.client.get(f"/api/projects/{self.pid}/cluster-map")
                first_elapsed = time.perf_counter() - t0
                self.assertEqual(first.status_code, 202, first.text)
                self.assertEqual(first.json()["sample_count"], 1257)
                self.assertLess(first_elapsed, 2.0)
                run_id = start.call_args.args[0]
            from backend.cluster_map import run_cluster_map_worker
            run_cluster_map_worker(run_id, self.pid)

        cached = self.client.get(f"/api/projects/{self.pid}/cluster-map")
        self.assertEqual(cached.status_code, 200, cached.text)
        self.assertTrue(cached.json()["cached"])
        self.assertEqual(cached.json()["total"], 1257)

        with patch("backend.cluster_map.start_cluster_map_worker") as start:
            forced = self.client.get(
                f"/api/projects/{self.pid}/cluster-map", params={"force": True},
            )
        self.assertEqual(forced.status_code, 202, forced.text)
        start.assert_called_once()

    def test_background_thread_completes_without_blocking_get(self):
        self._embed()
        with patch(
            "backend.cluster_map.compute_umap_projection",
            side_effect=self._fake_coords,
        ):
            first = self.client.get(f"/api/projects/{self.pid}/cluster-map")
            self.assertEqual(first.status_code, 202, first.text)
            deadline = time.time() + 10
            status = first.status_code
            body = first.json()
            while time.time() < deadline:
                resp = self.client.get(f"/api/projects/{self.pid}/cluster-map")
                status = resp.status_code
                body = resp.json()
                if status == 200:
                    break
                self.assertEqual(status, 202, resp.text)
                time.sleep(0.1)
        self.assertEqual(status, 200, body)
        self.assertTrue(body.get("cached"))
        self.assertEqual(len(body["points"]), 3)

    def test_worker_timeout_marks_failed(self):
        self._embed()
        with patch("backend.cluster_map.WORKER_TIMEOUT_SEC", 0.05):
            with patch(
                "backend.cluster_map.compute_umap_projection",
                side_effect=lambda embeddings: time.sleep(1) or self._fake_coords(embeddings),
            ):
                with patch("backend.cluster_map.start_cluster_map_worker") as start:
                    resp = self.client.get(f"/api/projects/{self.pid}/cluster-map")
                    self.assertEqual(resp.status_code, 202)
                    run_id = start.call_args.args[0]
                from backend.cluster_map import run_cluster_map_worker
                run_cluster_map_worker(run_id, self.pid)

        failed = self.client.get(f"/api/projects/{self.pid}/cluster-map")
        self.assertEqual(failed.status_code, 409, failed.text)
        self.assertIn("timed out", failed.json()["detail"].lower())
