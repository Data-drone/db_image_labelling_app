# Export Dataset — Design Doc

## Overview

One-click export from the Project Dashboard. Queries Lakebase for all labeled
annotations, builds COCO JSON (detection) or a simple CSV (classification),
copies images to a UC Volume export directory.

## User Flow

1. User opens Project Dashboard
2. Clicks "Export Dataset" button
3. Picks format: COCO (detection) / ImageFolder+CSV (classification)
4. App shows spinner while exporting
5. On completion: displays the Volume path + summary (N images, N annotations)

## Backend

### `POST /api/projects/{project_id}/export`

Request body:
```json
{
  "format": "coco" | "yolo",
  "export_volume": "/Volumes/brian_gen_ai/cv_explorer/exports"
}
```

The endpoint:
1. Queries all labeled samples + annotations for the project
2. Builds output directory: `{export_volume}/{project_name}_v{version}_{timestamp}/`
3. For detection (COCO):
   - Creates `annotations.json` with COCO format
   - Copies images into `images/` subdirectory
4. For classification:
   - Creates `labels.csv` with columns: filename, label
   - Copies images into `images/` subdirectory
5. Writes `metadata.json`: project_id, name, version, task_type, class_list,
   sample_count, annotation_count, export_timestamp, source_volume
6. Returns the export path + summary stats

### COCO Format

```json
{
  "info": {"description": "CV Explorer export", "version": "1.0", ...},
  "images": [
    {"id": 1, "file_name": "img001.jpg", "width": 640, "height": 480}
  ],
  "annotations": [
    {"id": 1, "image_id": 1, "category_id": 0, "bbox": [x, y, w, h]}
  ],
  "categories": [
    {"id": 0, "name": "cat"}, {"id": 1, "name": "dog"}
  ]
}
```

Note: COCO bbox format is `[x_min, y_min, width, height]` in absolute pixels.
Our stored format is normalized (0-1), so we need image dimensions to convert.

### Image Dimensions

We need width/height for COCO. Options:
- Read from image file (PIL) during export — reliable, slightly slower
- This is fine for MVP since we're already copying images

### File I/O

Uses `w.files.upload()` to write to UC Volumes (same SDK pattern as existing
volume reads). Images are copied from source volume to export volume.

## Frontend

### ProjectDashboard Changes

Add "Export Dataset" button next to "Start Labeling" in the header.

On click:
- Show modal/inline UI with format selector + export volume path input
- Default export volume: derive from project's source_volume
  (e.g. source `/Volumes/a/b/images` → export `/Volumes/a/b/exports`)
- Submit → show progress spinner
- On success → show path + stats
- On error → show error message

## Scope

MVP — no background jobs, no progress streaming, no YOLO format (COCO only
for detection, CSV for classification). Synchronous HTTP request.
