# Detection Labeling — Design

Date: 2026-03-18

## Problem

The labeling view only implements classification (click class button → save → next).
Detection projects show the same classification UI — no bounding box drawing.

## Design

### Workflow: Select-then-draw with batch save

1. User selects active class from right panel (number keys 1-9 or click)
2. Click-and-drag on the image canvas to draw a bounding box — assigned to active class
3. Repeat for all objects in the image
4. Click "Save & Next" to commit all boxes in one API call, then advance to next sample
5. "Skip" still works as before

### Canvas component (`BBoxCanvas`)

- Renders on top of the image as an HTML5 `<canvas>` overlay
- Coordinate system: normalized 0-1 relative to image dimensions (stored in bbox_json)
- Display: scale normalized coords to canvas pixel size on render

**Drawing mode (default):**
- Crosshair cursor
- mousedown → start corner, drag → rectangle preview, mouseup → commit box to local state
- Minimum 5px threshold to prevent accidental click-draws

**Select mode (click existing box):**
- Click inside a box → select it (highlighted border, resize handles on 4 corners + 4 edges)
- Drag box body → reposition
- Drag handle → resize
- Delete key → remove selected box
- Click empty area → deselect, back to draw mode

**Rendering:**
- Each class gets a color from a fixed palette (8 colors, cycling)
- Boxes: 2px solid border in class color, 15% opacity fill
- Selected box: 3px border, brighter fill, resize handles as small squares
- Class label text above each box (small, class color background)

### Right panel (detection mode)

Replaces the classification class-button list when `project.task_type === 'detection'`:

```
┌─────────────────────┐
│  image_0042.jpg      │
│  /Volumes/...        │
│─────────────────────│
│  Active Class        │
│  [1] ● car          │  ← selected (highlighted)
│  [2] ● person       │
│  [3] ● truck        │
│  [+ Add class...]   │
│─────────────────────│
│  Annotations (3)     │
│  ● car    120×80  ✕  │
│  ● person  60×90  ✕  │
│  ● car    200×50  ✕  │
│─────────────────────│
│  [Save & Next]       │
│  [Skip (S)]          │
│  [1-9] class [S] skip│
│  [Del] remove box    │
│  [Esc] back          │
└─────────────────────┘
```

- Click annotation row → select that box on canvas
- Click ✕ → delete that annotation
- "Save & Next" disabled when 0 annotations drawn

### Backend

**New endpoint:** `POST /api/projects/{project_id}/samples/{sample_id}/annotate-batch`

Request body:
```json
{
  "annotations": [
    {"label": "car", "ann_type": "bbox", "bbox_json": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}},
    {"label": "person", "ann_type": "bbox", "bbox_json": {"x": 0.5, "y": 0.1, "w": 0.15, "h": 0.6}}
  ]
}
```

- Deletes any existing annotations for this sample first (supports re-labeling)
- Creates all new annotations in one transaction
- Marks sample as "labeled"
- Returns list of created AnnotationOut objects

**New schema:** `AnnotationBatchCreate` — `annotations: list[AnnotationCreate]`

### Bbox coordinate format

All coordinates normalized 0-1 relative to original image dimensions:
- `x`: left edge (0 = left, 1 = right)
- `y`: top edge (0 = top, 1 = bottom)
- `w`: width as fraction of image width
- `h`: height as fraction of image height

This makes coordinates resolution-independent — same values regardless of canvas size or zoom.

### Color palette

```
#4299e0 (blue), #e05252 (red), #52e088 (green), #e0c452 (yellow),
#b452e0 (purple), #52d4e0 (cyan), #e08a52 (orange), #e052b4 (pink)
```

Class index `% 8` picks the color. Consistent across canvas and annotation list.

### Keyboard shortcuts (detection mode)

- `1-9`: Switch active class
- `S`: Skip sample
- `Delete` / `Backspace`: Remove selected box
- `Escape`: Deselect box, or if none selected, navigate back
- `Enter`: Save & Next (when annotations exist)

### Loading existing annotations

When the `next` endpoint returns a sample, its `annotations` array (from SampleOut schema) is already populated via the SQLAlchemy relationship. The frontend loads these into the local box state for editing. This enables re-labeling previously annotated images.

### What stays the same

- Classification mode is completely unchanged
- LabelingView checks `project.task_type` and renders either classification buttons or detection canvas
- Top bar (progress, project name) identical for both modes
- Skip, keyboard shortcuts for class selection, add-class input all shared

### Files to create/modify

- `frontend/src/components/BBoxCanvas.jsx` — new canvas component
- `frontend/src/pages/LabelingView.jsx` — branch on task_type, detection panel
- `frontend/src/api/client.js` — add `annotateSampleBatch` function
- `backend/main.py` — add `annotate-batch` endpoint
- `backend/schemas.py` — add `AnnotationBatchCreate` schema
