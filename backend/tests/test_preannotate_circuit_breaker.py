"""Tests for stopping pre-annotate Job runs when the serving endpoint is unavailable."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.preannotate import EndpointCircuitBreaker


def _http_error(status_code: int) -> Exception:
    exc = Exception(f"HTTP {status_code}")
    exc.status_code = status_code
    return exc


class TestEndpointCircuitBreaker(unittest.TestCase):
    def test_trips_after_consecutive_unavailable_errors(self):
        breaker = EndpointCircuitBreaker(3)

        self.assertFalse(breaker.record_failure(TimeoutError("slow")))
        self.assertFalse(breaker.record_failure(_http_error(503)))
        self.assertTrue(breaker.record_failure(_http_error(504)))
        self.assertTrue(breaker.tripped)
        self.assertEqual(breaker.consecutive, 3)

    def test_success_resets_streak(self):
        breaker = EndpointCircuitBreaker(3)

        for _ in range(5):
            breaker.record_failure(TimeoutError("slow"))
            breaker.record_failure(TimeoutError("slow"))
            breaker.record_success()

        self.assertFalse(breaker.tripped)
        self.assertEqual(breaker.consecutive, 0)

    def test_non_retryable_error_resets_streak(self):
        breaker = EndpointCircuitBreaker(2)

        breaker.record_failure(TimeoutError("slow"))
        breaker.record_failure(ValueError("bad request"))
        self.assertFalse(breaker.record_failure(TimeoutError("slow")))
        self.assertEqual(breaker.consecutive, 1)

    def test_zero_threshold_disables(self):
        breaker = EndpointCircuitBreaker(0)

        for _ in range(10):
            self.assertFalse(breaker.record_failure(TimeoutError("slow")))


class TestPreannotateWorkerCircuitBreaker(unittest.TestCase):
    def _seed(self, tmp: Path, sample_count: int) -> int:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from backend.models import Base, LabelingProject, PreannotateRun, ProjectSample

        engine = create_engine(os.environ["DATABASE_URL"])
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        try:
            project = LabelingProject(
                name="breaker-proj",
                task_type="classification",
                class_list=["cat", "dog"],
                source_volume=str(tmp),
                serving_endpoint="broken-endpoint",
            )
            session.add(project)
            session.flush()
            for i in range(sample_count):
                path = tmp / f"img{i}.jpg"
                path.write_bytes(b"\xff\xd8\xff\xd9")
                session.add(ProjectSample(
                    project_id=project.id,
                    filepath=str(path),
                    filename=path.name,
                ))
            run = PreannotateRun(project_id=project.id)
            session.add(run)
            session.commit()
            return run.id
        finally:
            session.close()
            engine.dispose()

    def _run_worker(self, run_id: int, predict_side_effect):
        from backend.jobs import preannotate_worker

        with (
            patch("backend.inference.predict_sample", side_effect=predict_side_effect) as predict,
            patch.object(preannotate_worker, "BATCH_SIZE", 2),
            patch.object(preannotate_worker.logging, "basicConfig"),
        ):
            preannotate_worker.main(run_id)
        return predict

    def _load_run(self, run_id: int):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from backend.models import PreannotateRun

        engine = create_engine(os.environ["DATABASE_URL"])
        session = sessionmaker(bind=engine)()
        try:
            return session.get(PreannotateRun, run_id)
        finally:
            session.close()
            engine.dispose()

    def _env(self, tmp: Path, **extra):
        env = {
            "USE_LAKEBASE": "false",
            "DATABASE_URL": f"sqlite:///{tmp / 'worker.db'}",
            **extra,
        }
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("EMBEDDING_ENDPOINT", None)
        os.environ.pop("SERVING_ENDPOINT", None)

    def test_stops_run_across_batches_when_endpoint_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            self._env(tmp)
            run_id = self._seed(tmp, sample_count=10)

            predict = self._run_worker(run_id, TimeoutError("endpoint down"))
            run = self._load_run(run_id)

        self.assertEqual(predict.call_count, 3)
        self.assertEqual(run.status, "failed")
        self.assertEqual(run.failed, 3)
        self.assertEqual(run.completed, 0)
        self.assertIn("Stopped after 3 consecutive samples", run.error_message)
        self.assertIn("7 sample(s) were not attempted", run.error_message)

    def test_threshold_is_configurable(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            self._env(tmp, PRE_ANNOTATE_MAX_CONSECUTIVE_UNAVAILABLE="5")
            run_id = self._seed(tmp, sample_count=10)

            predict = self._run_worker(run_id, TimeoutError("endpoint down"))
            run = self._load_run(run_id)

        self.assertEqual(predict.call_count, 5)
        self.assertIn("5 sample(s) were not attempted", run.error_message)

    def test_non_retryable_errors_process_every_sample(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            self._env(tmp)
            run_id = self._seed(tmp, sample_count=10)

            predict = self._run_worker(run_id, ValueError("bad payload"))
            run = self._load_run(run_id)

        self.assertEqual(predict.call_count, 10)
        self.assertEqual(run.status, "failed")
        self.assertEqual(run.failed, 10)
        self.assertNotIn("Stopped after", run.error_message)


if __name__ == "__main__":
    unittest.main()
