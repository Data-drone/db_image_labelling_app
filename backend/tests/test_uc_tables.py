"""Unit tests for UC table image-source helpers and project create."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.tests.conftest import test_client
from backend.uc_tables import (
    TableScanResult,
    TableSourceError,
    attach_labeled_delta_table,
    normalize_image_path,
    parse_table_fqn,
    quote_uc_id,
    scan_table_for_samples,
    validate_image_path,
    validate_source_filter,
    volume_hint_from_path,
)


class TestParseAndValidate(unittest.TestCase):
    def test_labeled_delta_table_reads_source_and_volume_labels(self):
        project = MagicMock(
            id=42,
            source_type="table",
            source_table="main.cv.image_catalog",
            image_path_column="image_path",
        )
        workspace = MagicMock()
        metadata = {
            "format": "hf_jsonl",
            "snapshot_id": "snapshot-1",
            "exported_at": "2026-09-28T00:00:00Z",
            "lineage": {},
        }

        with patch("backend.uc_tables._execute_sql") as execute:
            table = attach_labeled_delta_table(
                project,
                "/Volumes/main/cv/exports/run-1",
                [{"image_path": "/Volumes/main/cv/images/a.jpg", "annotations": []}],
                workspace,
                metadata,
            )

        self.assertEqual(
            table,
            "main.cv.image_catalog_labeled_p42_snapshot_1",
        )
        upload_path = workspace.files.upload.call_args.args[0]
        self.assertEqual(upload_path, "/Volumes/main/cv/exports/run-1/lineage.jsonl")
        sql = execute.call_args.args[0]
        self.assertIn("CREATE OR REPLACE TABLE `main`.`cv`.`image_catalog_labeled_p42_snapshot_1`", sql)
        self.assertIn("FROM `main`.`cv`.`image_catalog` src", sql)
        self.assertIn("read_files('/Volumes/main/cv/exports/run-1/lineage.jsonl'", sql)
        self.assertEqual(metadata["lineage"]["labeled_table_uc"], table)

    def test_parse_fqn(self):
        self.assertEqual(
            parse_table_fqn("main.cv.image_catalog"),
            ("main", "cv", "image_catalog"),
        )
        self.assertEqual(
            parse_table_fqn("`main`.`cv`.`image_catalog`"),
            ("main", "cv", "image_catalog"),
        )
        with self.assertRaises(ValueError):
            parse_table_fqn("only.two")
        with self.assertRaises(ValueError):
            parse_table_fqn("a.b.c;drop")

    def test_quote(self):
        self.assertEqual(quote_uc_id("cv"), "`cv`")

    def test_filter(self):
        self.assertEqual(validate_source_filter("split = 'train'"), "split = 'train'")
        self.assertIsNone(validate_source_filter("  "))
        with self.assertRaises(ValueError):
            validate_source_filter("1=1; DROP TABLE x")
        with self.assertRaises(ValueError):
            validate_source_filter("1=1 -- comment")
        with self.assertRaises(ValueError):
            validate_source_filter("a UNION SELECT 1")

    def test_image_path(self):
        p = "/Volumes/cat/sch/vol/nested/a.jpg"
        self.assertEqual(validate_image_path(p), p)
        self.assertEqual(volume_hint_from_path(p), "/Volumes/cat/sch/vol")
        self.assertEqual(
            validate_image_path("dbfs:/Volumes/cat/sch/vol/nested/a.jpg"),
            p,
        )
        self.assertEqual(
            validate_image_path("dbfs:///Volumes/cat/sch/vol/nested/a.jpg"),
            p,
        )
        self.assertEqual(
            normalize_image_path("dbfs:/Volumes/cat/sch/vol/a.jpg"),
            "/Volumes/cat/sch/vol/a.jpg",
        )
        with self.assertRaises(ValueError):
            validate_image_path("/tmp/a.jpg")
        with self.assertRaises(ValueError):
            validate_image_path("/Volumes/cat/sch/vol/../etc/passwd")
        with self.assertRaises(ValueError):
            validate_image_path("/Volumes/a/b/c")  # no file under volume


class _FakeResult:
    def __init__(self, rows, next_chunk_index=None):
        self.data_array = rows
        self.next_chunk_index = next_chunk_index


class _FakeResp:
    def __init__(self, rows, next_chunk_index=None):
        self.result = _FakeResult(rows, next_chunk_index)
        self.statement_id = "stmt-1"
        self.status = None


class TestScanTable(unittest.TestCase):
    def test_scan_inserts_unique_samples(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from backend.models import Base, LabelingProject

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        project = LabelingProject(
            name="t", task_type="classification", class_list=["a"],
            source_volume="", source_type="table",
            source_table="main.cv.imgs",
        )
        db.add(project)
        db.flush()

        rows = [
            ["/Volumes/c/s/v/a.jpg"],
            ["/Volumes/c/s/v/b.jpg"],
            ["/Volumes/c/s/v/a.jpg"],
        ]
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = _FakeResp(rows)

        with patch.dict(os.environ, {"SQL_WAREHOUSE_ID": "wh-1"}):
            with patch("backend.uc_tables._get_workspace_client", return_value=client):
                result = scan_table_for_samples(
                    db, project.id, "main.cv.imgs", "image_path", None,
                )
        self.assertEqual(result.added, 2)
        self.assertEqual(result.skipped_existing, 0)
        self.assertEqual(result.volume_hint, "/Volumes/c/s/v")
        from backend.models import ProjectSample
        samples = db.query(ProjectSample).all()
        self.assertEqual({s.filename for s in samples}, {"a.jpg", "b.jpg"})

        # Auto Loader incremental: existing labels kept, only new path added
        rows2 = [
            ["dbfs:/Volumes/c/s/v/a.jpg"],
            ["/Volumes/c/s/v/b.jpg"],
            ["dbfs:///Volumes/c/s/v/c.jpg"],
        ]
        client.statement_execution.execute_statement.return_value = _FakeResp(rows2)
        with patch.dict(os.environ, {"SQL_WAREHOUSE_ID": "wh-1"}):
            with patch("backend.uc_tables._get_workspace_client", return_value=client):
                result2 = scan_table_for_samples(
                    db, project.id, "main.cv.imgs", "image_path", None,
                )
        self.assertEqual(result2.added, 1)
        self.assertEqual(result2.skipped_existing, 2)
        samples = db.query(ProjectSample).all()
        self.assertEqual({s.filename for s in samples}, {"a.jpg", "b.jpg", "c.jpg"})

    def test_invalid_path_fails_scan(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from backend.models import Base, LabelingProject

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        project = LabelingProject(
            name="t", task_type="classification", class_list=["a"],
            source_volume="", source_type="table", source_table="main.cv.imgs",
        )
        db.add(project)
        db.flush()
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = _FakeResp(
            [["s3://bucket/a.jpg"]],
        )
        with patch.dict(os.environ, {"SQL_WAREHOUSE_ID": "wh-1"}):
            with patch("backend.uc_tables._get_workspace_client", return_value=client):
                with self.assertRaises(TableSourceError):
                    scan_table_for_samples(db, project.id, "main.cv.imgs")

    def test_missing_warehouse(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from backend.models import Base, LabelingProject

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = sessionmaker(bind=engine)()
        project = LabelingProject(
            name="t", task_type="classification", class_list=["a"],
            source_volume="", source_type="table", source_table="main.cv.imgs",
        )
        db.add(project)
        db.flush()
        env = {k: v for k, v in os.environ.items()
               if k not in ("SQL_WAREHOUSE_ID", "DATABRICKS_WAREHOUSE_ID")}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(TableSourceError) as ctx:
                scan_table_for_samples(db, project.id, "main.cv.imgs")
        self.assertEqual(ctx.exception.status_code, 400)


class TestProjectTableSourceHttp(unittest.TestCase):
    def test_create_from_table_mocked_scan(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            with test_client(tmp) as (c, _main, _tmp):
                with patch(
                    "backend.routes.projects.scan_table_for_samples",
                    return_value=TableScanResult(added=2, volume_hint="/Volumes/c/s/v"),
                ):
                    r = c.post("/api/projects", json={
                        "name": "from-table",
                        "task_type": "classification",
                        "class_list": ["cat"],
                        "source_type": "table",
                        "source_table": "main.cv.image_catalog",
                    })
                self.assertEqual(r.status_code, 200, r.text)
                body = r.json()
                self.assertEqual(body["source_type"], "table")
                self.assertEqual(body["source_table"], "main.cv.image_catalog")
                self.assertEqual(body["source_volume"], "/Volumes/c/s/v")
                self.assertEqual(body["sample_count"], 2)

    def test_create_volume_still_works(self):
        from backend.tests.conftest import make_sample_volume
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            vol = make_sample_volume(tmp)
            with test_client(tmp) as (c, _main, _tmp):
                r = c.post("/api/projects", json={
                    "name": "from-vol",
                    "task_type": "classification",
                    "class_list": ["cat"],
                    "source_volume": str(vol),
                })
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(r.json()["source_type"], "volume")
                self.assertEqual(r.json()["sample_count"], 3)

    def test_table_requires_fqn(self):
        with tempfile.TemporaryDirectory() as td:
            with test_client(Path(td)) as (c, _main, _tmp):
                r = c.post("/api/projects", json={
                    "name": "bad",
                    "task_type": "classification",
                    "class_list": ["cat"],
                    "source_type": "table",
                })
                self.assertEqual(r.status_code, 422)

    def test_patch_table_requires_confirm(self):
        from backend.tests.conftest import make_sample_volume
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            vol = make_sample_volume(tmp)
            with test_client(tmp) as (c, _main, _tmp):
                created = c.post("/api/projects", json={
                    "name": "p",
                    "task_type": "classification",
                    "class_list": ["cat"],
                    "source_volume": str(vol),
                })
                pid = created.json()["id"]
                r = c.patch(f"/api/projects/{pid}", json={
                    "source_type": "table",
                    "source_table": "main.cv.other",
                })
                self.assertEqual(r.status_code, 400, r.text)
                with patch(
                    "backend.routes.projects.scan_table_for_samples",
                    return_value=TableScanResult(added=1, volume_hint="/Volumes/c/s/v"),
                ):
                    ok = c.patch(f"/api/projects/{pid}", json={
                        "source_type": "table",
                        "source_table": "main.cv.other",
                        "confirm_source_change": True,
                    })
                self.assertEqual(ok.status_code, 200, ok.text)
                self.assertEqual(ok.json()["source_table"], "main.cv.other")

    def test_clone_copies_table_fields(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            with test_client(tmp) as (c, _main, _tmp):
                with patch(
                    "backend.routes.projects.scan_table_for_samples",
                    return_value=TableScanResult(added=0, volume_hint=None),
                ):
                    created = c.post("/api/projects", json={
                        "name": "orig",
                        "task_type": "detection",
                        "class_list": ["box"],
                        "source_type": "table",
                        "source_table": "main.cv.cams",
                        "image_path_column": "image_path",
                        "source_filter": "split = 'train'",
                    })
                pid = created.json()["id"]
                cloned = c.post(f"/api/projects/{pid}/clone")
                self.assertEqual(cloned.status_code, 200, cloned.text)
                body = cloned.json()
                self.assertEqual(body["source_type"], "table")
                self.assertEqual(body["source_table"], "main.cv.cams")
                self.assertEqual(body["source_filter"], "split = 'train'")

    def test_sync_volume_adds_new_files_only(self):
        from backend.tests.conftest import make_sample_volume
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            vol = make_sample_volume(tmp)
            with test_client(tmp) as (c, _main, _tmp):
                created = c.post("/api/projects", json={
                    "name": "vol-sync",
                    "task_type": "classification",
                    "class_list": ["cat"],
                    "source_volume": str(vol),
                })
                pid = created.json()["id"]
                self.assertEqual(created.json()["sample_count"], 3)
                again = c.post(f"/api/projects/{pid}/sync-source")
                self.assertEqual(again.status_code, 200, again.text)
                self.assertEqual(again.json()["added"], 0)
                self.assertEqual(again.json()["sample_count"], 3)
                (vol / "d.jpg").write_bytes(b"\xff\xd8\xff\xd9")
                synced = c.post(f"/api/projects/{pid}/sync-source")
                self.assertEqual(synced.status_code, 200, synced.text)
                self.assertEqual(synced.json()["added"], 1)
                self.assertEqual(synced.json()["sample_count"], 4)

    def test_sync_table_uses_scan_result(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            with test_client(tmp) as (c, _main, _tmp):
                with patch(
                    "backend.routes.projects.scan_table_for_samples",
                    return_value=TableScanResult(
                        added=0, skipped_existing=0, volume_hint="/Volumes/c/s/v",
                    ),
                ):
                    created = c.post("/api/projects", json={
                        "name": "tbl-sync",
                        "task_type": "classification",
                        "class_list": ["cat"],
                        "source_type": "table",
                        "source_table": "main.cv.imgs",
                    })
                pid = created.json()["id"]
                with patch(
                    "backend.routes.projects.scan_table_for_samples",
                    return_value=TableScanResult(
                        added=4, skipped_existing=12, volume_hint="/Volumes/c/s/v",
                    ),
                ):
                    synced = c.post(f"/api/projects/{pid}/sync-source")
                self.assertEqual(synced.status_code, 200, synced.text)
                body = synced.json()
                self.assertEqual(body["added"], 4)
                self.assertEqual(body["skipped_existing"], 12)


class TestBrowseTables(unittest.TestCase):
    def test_preview_rejects_bad_fqn(self):
        with tempfile.TemporaryDirectory() as td:
            with test_client(Path(td)) as (c, _main, _tmp):
                r = c.get("/api/tables/preview", params={"full_name": "nope"})
                self.assertEqual(r.status_code, 400)

    def test_list_tables_mocked(self):
        with tempfile.TemporaryDirectory() as td:
            with test_client(Path(td)) as (c, _main, _tmp):
                t = MagicMock()
                t.name = "image_catalog"
                mock_w = MagicMock()
                mock_w.tables.list.return_value = [t]
                with patch("backend.routes.browse._get_workspace_client", return_value=mock_w):
                    r = c.get("/api/tables", params={"catalog": "main", "schema": "cv"})
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(r.json(), ["image_catalog"])


class TestImportCreateRejectedForTable(unittest.TestCase):
    def test_create_missing_not_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            with test_client(tmp) as (c, _main, _tmp):
                with patch(
                    "backend.routes.projects.scan_table_for_samples",
                    return_value=TableScanResult(added=0, volume_hint="/Volumes/c/s/v"),
                ):
                    created = c.post("/api/projects", json={
                        "name": "tproj",
                        "task_type": "classification",
                        "class_list": ["cat"],
                        "source_type": "table",
                        "source_table": "main.cv.imgs",
                    })
                pid = created.json()["id"]
                labels = tmp / "labels.jsonl"
                labels.write_text(
                    '{"filename":"ghost.jpg","annotations":'
                    '[{"label":"cat","ann_type":"classification"}]}\n'
                )
                r = c.post(
                    f"/api/projects/{pid}/import",
                    json={
                        "volume_path": str(labels),
                        "format": "jsonl",
                        "on_missing_sample": "create",
                    },
                    headers={"X-Test-Allow-Local-Path": "1"},
                )
                self.assertEqual(r.status_code, 400, r.text)
                self.assertIn("table-backed", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
