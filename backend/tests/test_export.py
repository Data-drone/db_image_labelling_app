"""Export contracts, large reference datasets, and training handoff checks."""

import io
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image

from backend import deps
from backend.models import Annotation, LabelingProject, ProjectSample
from backend.tests.conftest import test_client


class TestDatasetExport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client_context = test_client(Path(self.temp.name))
        self.client, _, _ = self.client_context.__enter__()
        self.addCleanup(self.client_context.__exit__, None, None, None)
        self.env = patch.dict("os.environ", {
            "EXPORT_VOLUME_PATH": "/Volumes/catalog/schema/output",
            "FINETUNE_SUPPORTS_REFERENCE_DATASETS": "false",
            "FINETUNE_DATABRICKS_JOB_ID": "123",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.workspace = MagicMock()
        self.files = {}
        self.workspace.files.upload.side_effect = self.upload
        self.workspace.files.download.side_effect = lambda path: SimpleNamespace(contents=io.BytesIO(self.files[path]))
        self.workspace.files.list_directory_contents.return_value = []
        for module in ("export", "exports_list", "finetune_runs"):
            workspace_patch = patch(f"backend.routes.{module}._get_workspace_client", return_value=self.workspace)
            workspace_patch.start()
            self.addCleanup(workspace_patch.stop)

    def upload(self, path, content, overwrite):
        if not overwrite and path in self.files:
            raise FileExistsError(path)
        self.files[path] = content.read()

    def seed(self, count=2, task_type="classification"):
        with deps.get_session_factory()() as db:
            project = LabelingProject(
                name='Cats / café', task_type=task_type, class_list=['cat', 'dog, "large"'],
                source_volume="/Volumes/catalog/schema/images", version=3,
            )
            db.add(project)
            db.flush()
            samples = [ProjectSample(
                project_id=project.id, filepath=f"{project.source_volume}/{index}.png",
                filename=f"{index}.png", status="labeled",
            ) for index in range(count)]
            db.add_all(samples)
            db.flush()
            db.add_all([Annotation(
                project_id=project.id, sample_id=sample.id,
                label=project.class_list[index % 2],
                ann_type="bbox" if task_type == "detection" else "classification",
                bbox_json={"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.5} if task_type == "detection" else None,
                is_draft=False,
            ) for index, sample in enumerate(samples)])
            db.commit()
            return project.id

    def prepare(self, project_id, **overrides):
        return self.client.post(f"/api/projects/{project_id}/export", json={"mode": "reference", **overrides}, headers={"X-Forwarded-Email": "labeler@example.com"})

    def test_6500_samples_write_only_three_files_without_image_io(self):
        project_id = self.seed(6500)
        with patch("backend.routes.export.read_image_bytes", side_effect=AssertionError("image read")) as read_image:
            response = self.prepare(project_id)
        self.assertEqual(response.status_code, 200, response.text)
        read_image.assert_not_called()
        self.workspace.files.download.assert_not_called()
        self.workspace.files.list_directory_contents.assert_not_called()
        self.assertEqual(self.workspace.files.upload.call_count, 3)
        result = response.json()
        self.assertEqual(result["images"], 6500)
        path = result["export_path"]
        records = [json.loads(line) for line in self.files[f"{path}/train.jsonl"].splitlines()]
        self.assertEqual(len(records), 6500)
        self.assertEqual(records[0]["image"], "/Volumes/catalog/schema/images/0.png")
        self.assertEqual([record["label"] for record in records[:4]], [0, 1, 0, 1])
        classes = json.loads(self.files[f"{path}/classes.json"])
        self.assertEqual(classes["names"], ['cat', 'dog, "large"'])
        self.assertEqual(classes["label2id"], {'cat': 0, 'dog, "large"': 1})
        metadata = json.loads(self.files[f"{path}/metadata.json"])
        self.assertEqual(metadata["exported_by"], "labeler@example.com")
        self.assertEqual(metadata["lineage"]["sample_count"], 6500)
        self.assertFalse(metadata["images_copied"])
        self.assertFalse(metadata["source_images_verified"])
        self.assertEqual(list(self.files)[-1], f"{path}/metadata.json")

    def test_excludes_unlabeled_samples_and_draft_predictions(self):
        project_id = self.seed()
        with deps.get_session_factory()() as db:
            samples = db.query(ProjectSample).order_by(ProjectSample.id).all()
            samples[1].status = "pre_labeled"
            db.add(Annotation(project_id=project_id, sample_id=samples[0].id, label="dog", ann_type="classification", is_draft=True))
            db.commit()
        response = self.prepare(project_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["images"], 1)

    @unittest.skipUnless(importlib.util.find_spec("datasets"), "Install datasets to run the Hugging Face integration check")
    def test_huggingface_loading_code_decodes_images_and_preserves_class_ids(self):
        project_id = self.seed()
        result = self.prepare(project_id).json()
        export_dir = Path(self.temp.name) / "snapshot"
        export_dir.mkdir()
        for filename in ("classes.json", "train.jsonl"):
            (export_dir / filename).write_bytes(self.files[f"{result['export_path']}/{filename}"])
        rows = [json.loads(line) for line in (export_dir / "train.jsonl").read_text().splitlines()]
        for index, row in enumerate(rows):
            local_image = Path(self.temp.name) / f"{index}.png"
            Image.new("RGB", (20, 10)).save(local_image)
            row["image"] = str(local_image)
        (export_dir / "train.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
        code = result["huggingface_code"].replace(repr(result["export_path"]), repr(str(export_dir)))
        namespace = {}
        with patch.dict("os.environ", {"HF_DATASETS_CACHE": str(Path(self.temp.name) / "cache")}):
            exec(code, namespace)
        dataset = namespace["dataset"]
        self.assertEqual(len(dataset), 2)
        self.assertEqual(dataset[0]["image"].size, (20, 10))
        self.assertEqual(dataset.features["label"].int2str(dataset[1]["label"]), 'dog, "large"')

    def test_invalid_labels_fail_before_any_upload(self):
        for invalid_kind in ("missing", "unknown", "duplicate", "bbox", "draft", "local_path"):
            with self.subTest(invalid_kind=invalid_kind):
                if invalid_kind != "missing":
                    with deps.get_session_factory()() as db:
                        db.query(Annotation).delete()
                        db.query(ProjectSample).delete()
                        db.query(LabelingProject).delete()
                        db.commit()
                project_id = self.seed(1)
                with deps.get_session_factory()() as db:
                    annotation = db.query(Annotation).first()
                    if invalid_kind == "missing":
                        db.delete(annotation)
                    elif invalid_kind == "unknown":
                        annotation.label = "not a project class"
                    elif invalid_kind == "duplicate":
                        db.add(Annotation(project_id=project_id, sample_id=annotation.sample_id, label="cat", ann_type="classification"))
                    elif invalid_kind == "bbox":
                        annotation.ann_type = "bbox"
                    elif invalid_kind == "draft":
                        annotation.is_draft = True
                    else:
                        db.query(ProjectSample).first().filepath = "/tmp/image.png"
                    db.commit()
                response = self.prepare(project_id)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["detail"]["invalid_sample_count"], 1)
                self.workspace.files.upload.assert_not_called()

    def test_snapshot_is_unique_and_previous_labels_do_not_change(self):
        project_id = self.seed(1)
        first = self.prepare(project_id).json()
        with deps.get_session_factory()() as db:
            db.query(Annotation).first().label = 'dog, "large"'
            db.commit()
        second = self.prepare(project_id).json()
        self.assertNotEqual(first["export_path"], second["export_path"])
        self.assertEqual(json.loads(self.files[first["export_path"] + "/train.jsonl"])["label"], 0)
        self.assertEqual(json.loads(self.files[second["export_path"] + "/train.jsonl"])["label"], 1)

    def test_bad_paths_and_unsupported_task_types_rejected(self):
        project_id = self.seed()
        for path in ("/tmp/export", "/Volumes/a/b", "/Volumes/a/b/c/../x", "/Volumes/a/b/c\\x"):
            response = self.prepare(project_id, export_volume=path)
            self.assertEqual(response.status_code, 400, response.text)
        with deps.get_session_factory()() as db:
            db.query(LabelingProject).first().task_type = "segmentation"
            db.commit()
        self.assertEqual(self.prepare(project_id).status_code, 400)
        self.assertEqual(self.prepare(999).status_code, 404)
        self.workspace.files.upload.assert_not_called()

    def test_3000_detection_samples_write_two_files_without_image_io(self):
        project_id = self.seed(3000, task_type="detection")
        with patch("backend.routes.export.read_image_bytes", side_effect=AssertionError("image read")) as read_image:
            response = self.prepare(project_id)
        self.assertEqual(response.status_code, 200, response.text)
        read_image.assert_not_called()
        self.workspace.files.download.assert_not_called()
        self.workspace.files.list_directory_contents.assert_not_called()
        self.assertEqual(self.workspace.files.upload.call_count, 2)

        result = response.json()
        self.assertEqual(result["format"], "coco_reference")
        self.assertEqual(result["images"], 3000)
        self.assertEqual(result["annotations"], 3000)
        export_path = result["export_path"]
        self.workspace.files.create_directory.assert_called_once_with(export_path)
        self.assertEqual(
            set(self.files),
            {f"{export_path}/annotations.json", f"{export_path}/metadata.json"},
        )
        coco = json.loads(self.files[f"{export_path}/annotations.json"])
        self.assertEqual(len(coco["images"]), 3000)
        self.assertEqual(len(coco["annotations"]), 3000)
        self.assertEqual(coco["images"][0]["file_name"], "/Volumes/catalog/schema/images/0.png")
        self.assertNotIn("width", coco["images"][0])
        self.assertEqual(coco["annotations"][0]["bbox_normalized"], [0.1, 0.2, 0.5, 0.5])
        self.assertEqual(coco["annotations"][0]["category_id"], 1)
        self.assertEqual(coco["annotations"][1]["category_id"], 2)
        self.assertEqual(coco["categories"], [
            {"id": 1, "name": "cat"},
            {"id": 2, "name": 'dog, "large"'},
        ])
        self.assertNotIn("bbox", coco["annotations"][0])
        metadata = json.loads(self.files[f"{export_path}/metadata.json"])
        self.assertEqual(metadata["bbox_format"], "relative_xywh")
        self.assertTrue(metadata["requires_dimension_materialization"])
        self.assertEqual(list(self.files)[-1], f"{export_path}/metadata.json")

    def test_detection_rejects_non_finite_boolean_and_out_of_source_boxes(self):
        for bbox, filepath in (
            ({"x": float("nan"), "y": 0.2, "w": 0.5, "h": 0.5}, None),
            ({"x": True, "y": 0.2, "w": 0.5, "h": 0.5}, None),
            ({"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.5}, "/Volumes/catalog/schema/other/0.png"),
        ):
            with self.subTest(bbox=bbox, filepath=filepath):
                with deps.get_session_factory()() as db:
                    db.query(Annotation).delete()
                    db.query(ProjectSample).delete()
                    db.query(LabelingProject).delete()
                    db.commit()
                project_id = self.seed(1, task_type="detection")
                with deps.get_session_factory()() as db:
                    db.query(Annotation).first().bbox_json = bbox
                    if filepath:
                        db.query(ProjectSample).first().filepath = filepath
                    db.commit()
                response = self.prepare(project_id)
                self.assertEqual(response.status_code, 422, response.text)
                self.workspace.files.upload.assert_not_called()
                self.workspace.files.create_directory.assert_not_called()

    def test_detection_rejects_malformed_and_out_of_bounds_boxes(self):
        invalid_boxes = (
            {"x": 0.1, "y": 0.2, "w": 0, "h": 0.5},
            {"x": -0.1, "y": 0.2, "w": 0.5, "h": 0.5},
            {"x": 0.6, "y": 0.2, "w": 0.5, "h": 0.5},
            {"x": 0.1, "y": 0.2, "w": 0.5},
        )
        for bbox in invalid_boxes:
            with self.subTest(bbox=bbox):
                with deps.get_session_factory()() as db:
                    db.query(Annotation).delete()
                    db.query(ProjectSample).delete()
                    db.query(LabelingProject).delete()
                    db.commit()
                project_id = self.seed(1, task_type="detection")
                with deps.get_session_factory()() as db:
                    db.query(Annotation).first().bbox_json = bbox
                    db.commit()

                response = self.prepare(project_id)

                self.assertEqual(response.status_code, 422, response.text)
                self.workspace.files.upload.assert_not_called()
                self.workspace.files.create_directory.assert_not_called()

    def test_detection_includes_confirmed_negative_images_and_excludes_drafts(self):
        project_id = self.seed(2, task_type="detection")
        with deps.get_session_factory()() as db:
            samples = db.query(ProjectSample).order_by(ProjectSample.id).all()
            db.query(Annotation).filter_by(sample_id=samples[0].id).delete()
            db.query(Annotation).filter_by(sample_id=samples[1].id).update({"is_draft": True})
            db.commit()

        response = self.prepare(project_id)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["images"], 2)
        self.assertEqual(response.json()["annotations"], 0)
        coco = json.loads(self.files[response.json()["export_path"] + "/annotations.json"])
        self.assertEqual(len(coco["images"]), 2)
        self.assertEqual(coco["annotations"], [])

    def test_detection_loading_code_materializes_standard_pixel_coco(self):
        project_id = self.seed(1, task_type="detection")
        result = self.prepare(project_id).json()
        export_dir = Path(self.temp.name) / "detection_snapshot"
        export_dir.mkdir()
        (export_dir / "annotations.json").write_bytes(
            self.files[f"{result['export_path']}/annotations.json"]
        )
        image_path = Path(self.temp.name) / "image.png"
        Image.new("RGB", (20, 10)).save(image_path)
        coco = json.loads((export_dir / "annotations.json").read_text())
        coco["images"][0]["file_name"] = str(image_path)
        (export_dir / "annotations.json").write_text(json.dumps(coco))

        namespace = {}
        exec(result["loading_code"].replace(repr(result["export_path"]), repr(str(export_dir))), namespace)
        materialized = namespace["coco"]
        self.assertEqual(materialized["images"][0]["width"], 20)
        self.assertEqual(materialized["images"][0]["height"], 10)
        self.assertEqual(materialized["annotations"][0]["bbox"], [2.0, 2.0, 10.0, 5.0])
        self.assertEqual(materialized["annotations"][0]["area"], 50.0)

    def test_failed_upload_never_publishes_ready_metadata(self):
        project_id = self.seed()
        def failing_upload(path, content, overwrite):
            if path.endswith("classes.json"):
                raise RuntimeError("Volume unavailable")
            self.upload(path, content, overwrite)
        self.workspace.files.upload.side_effect = failing_upload
        response = self.prepare(project_id)
        self.assertEqual(response.status_code, 502)
        self.assertFalse(any(path.endswith("metadata.json") for path in self.files))

    def test_history_finds_new_and_legacy_destinations(self):
        project_id = self.seed()
        result = self.prepare(project_id).json()
        root = "/Volumes/catalog/schema/output"
        folder = result["export_path"].rsplit("/", 1)[1]
        metadata = self.files[result["export_path"] + "/metadata.json"]
        self.files[f"{root}/exports/legacy/metadata.json"] = metadata
        self.workspace.files.list_directory_contents.side_effect = lambda path: [
            SimpleNamespace(name=name, is_directory=True)
            for name in ([folder, "exports", "incomplete"] if path == root + "/" else ["legacy"])
        ]
        response = self.client.get(f"/api/projects/{project_id}/exports")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()), 2)
        self.assertIn(result["export_path"], [item["export_path"] for item in response.json()])
        self.assertTrue(all(item["huggingface_code"] for item in response.json()))

    def test_reference_training_requires_explicit_job_support(self):
        project_id = self.seed()
        result = self.prepare(project_id).json()
        with patch("backend.routes.finetune_runs.trigger_finetune_job", return_value=456) as submit:
            response = self.client.post(f"/api/projects/{project_id}/finetune", json={"export_path": result["export_path"]})
            self.assertEqual(response.status_code, 400, response.text)
            submit.assert_not_called()
            with patch.dict("os.environ", {"FINETUNE_SUPPORTS_REFERENCE_DATASETS": "true"}):
                response = self.client.post(f"/api/projects/{project_id}/finetune", json={"export_path": result["export_path"]})
            self.assertEqual(response.status_code, 200, response.text)
            submit.assert_called_once()

    def test_training_rejects_incomplete_foreign_and_malformed_metadata(self):
        project_id = self.seed()
        result = self.prepare(project_id).json()
        metadata_path = result["export_path"] + "/metadata.json"
        for metadata in (
            {"project_id": project_id, "format": "hf_jsonl", "status": "writing"},
            {"project_id": project_id + 1, "format": "hf_jsonl", "status": "ready"},
            [],
        ):
            self.files[metadata_path] = json.dumps(metadata).encode()
            with patch("backend.routes.finetune_runs.trigger_finetune_job") as submit:
                response = self.client.post(f"/api/projects/{project_id}/finetune", json={"export_path": result["export_path"]})
                self.assertEqual(response.status_code, 400, response.text)
                submit.assert_not_called()

    def test_default_destination_matches_history_and_app_config(self):
        project_id = self.seed()
        for export_path, demo_path, expected in (
            ("/Volumes/a/b/out/", "/Volumes/a/b/demo", "/Volumes/a/b/out"),
            ("", "/Volumes/a/b/demo", "/Volumes/a/b/demo/exports"),
            ("", "", "/Volumes/catalog/schema/images/exports"),
        ):
            with patch.dict("os.environ", {"EXPORT_VOLUME_PATH": export_path, "DEMO_VOLUME_PATH": demo_path}):
                result = self.prepare(project_id).json()
                self.assertTrue(result["export_path"].startswith(expected + "/"))
                config = self.client.get("/api/config").json()
                if export_path or demo_path:
                    self.assertEqual(config["export_default_path"], expected)
                self.client.get(f"/api/projects/{project_id}/exports")
                self.workspace.files.list_directory_contents.assert_called_with(expected + "/")

    def test_default_api_export_remains_copy_and_coco_still_works(self):
        project_id = self.seed(1)
        image_buffer = io.BytesIO()
        Image.new("RGB", (20, 10)).save(image_buffer, format="PNG")
        for task_type in ("classification", "detection"):
            with deps.get_session_factory()() as db:
                db.query(LabelingProject).first().task_type = task_type
                annotation = db.query(Annotation).first()
                if task_type == "detection":
                    annotation.ann_type = "bbox"
                    annotation.bbox_json = {"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.5}
                db.commit()
            with patch("backend.routes.export.read_image_bytes", return_value=image_buffer.getvalue()) as read_image:
                response = self.client.post(f"/api/projects/{project_id}/export", json={"export_volume": "/Volumes/catalog/schema/output"})
            self.assertEqual(response.status_code, 200, response.text)
            read_image.assert_called_once()
            self.assertEqual(response.json()["format"], "csv" if task_type == "classification" else "coco")
            if task_type == "detection":
                coco = json.loads(self.files[response.json()["export_path"] + "/annotations.json"])
                self.assertEqual(coco["annotations"][0]["bbox"], [2.0, 2.0, 10.0, 5.0])


if __name__ == "__main__":
    unittest.main()
