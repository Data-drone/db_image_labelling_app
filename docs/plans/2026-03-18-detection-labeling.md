# Detection Labeling Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add bounding box drawing, editing, and batch-save to the labeling view for detection projects.

**Architecture:** The existing LabelingView branches on `project.task_type`. Classification mode stays untouched. Detection mode replaces the image `<img>` with a `BBoxCanvas` component (HTML5 canvas overlay) and replaces the class-button list with a class selector + annotation list + Save & Next. A new backend endpoint accepts a batch of annotations in one request.

**Tech Stack:** React (JSX, hooks), HTML5 Canvas API, FastAPI, SQLAlchemy, Pydantic

**Design doc:** `docs/plans/2026-03-18-detection-labeling-design.md`

---

### Task 1: Backend — batch annotation schema and endpoint

**Files:**
- Modify: `backend/schemas.py` (add `AnnotationBatchCreate` after `AnnotationCreate` at line ~83)
- Modify: `backend/main.py` (add import + endpoint after the existing `annotate_sample` endpoint at line ~695)

**Step 1: Add AnnotationBatchCreate schema**

In `backend/schemas.py`, after the `AnnotationCreate` class, add:

```python
class AnnotationBatchCreate(BaseModel):
    annotations: list[AnnotationCreate]
```

**Step 2: Import new schema in main.py**

In `backend/main.py`, add `AnnotationBatchCreate` to the schemas import:

```python
from .schemas import (
    ProjectCreate, ProjectUpdate, ProjectOut, ProjectStats,
    SampleOut, SamplePage,
    AnnotationCreate, AnnotationBatchCreate, AnnotationOut,
)
```

**Step 3: Add annotate-batch endpoint**

In `backend/main.py`, after the existing `annotate_sample` endpoint (around line 695), add:

```python
@app.post(
    "/api/projects/{project_id}/samples/{sample_id}/annotate-batch",
    response_model=list[AnnotationOut],
)
def annotate_sample_batch(
    project_id: int,
    sample_id: int,
    payload: AnnotationBatchCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Save multiple annotations (bboxes) for a sample in one transaction."""
    sample = (
        db.query(ProjectSample)
        .filter_by(id=sample_id, project_id=project_id)
        .first()
    )
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found.")

    if not payload.annotations:
        raise HTTPException(status_code=400, detail="At least one annotation is required.")

    # Delete existing annotations for this sample (supports re-labeling)
    db.query(Annotation).filter_by(sample_id=sample_id, project_id=project_id).delete()

    user_email = _get_user_email(request)
    created = []
    for ann in payload.annotations:
        a = Annotation(
            sample_id=sample_id,
            project_id=project_id,
            label=ann.label,
            ann_type=ann.ann_type,
            bbox_json=ann.bbox_json,
            created_by=user_email,
        )
        db.add(a)
        created.append(a)

    sample.status = "labeled"
    sample.locked_by = None
    sample.locked_at = None

    db.commit()
    for a in created:
        db.refresh(a)

    return [AnnotationOut.model_validate(a) for a in created]
```

**Step 4: Verify backend starts**

Run: `cd /workspace/group/cv-react-deploy && python3 -c "from backend.schemas import AnnotationBatchCreate; print('OK')"`

**Step 5: Commit**

```bash
git add backend/schemas.py backend/main.py
git commit -m "feat: add batch annotation endpoint for detection labeling"
```

---

### Task 2: Frontend API client — add annotateSampleBatch

**Files:**
- Modify: `frontend/src/api/client.js` (add after `annotateSample` at line ~45)

**Step 1: Add the batch annotate function**

After the existing `annotateSample` export (line 45), add:

```javascript
export const annotateSampleBatch = (projectId, sampleId, annotations) =>
  api.post(`/projects/${projectId}/samples/${sampleId}/annotate-batch`, { annotations }).then(r => r.data);
```

**Step 2: Commit**

```bash
git add frontend/src/api/client.js
git commit -m "feat: add annotateSampleBatch API client function"
```

---

### Task 3: BBoxCanvas component — drawing and display

**Files:**
- Create: `frontend/src/components/BBoxCanvas.jsx`

This is the core canvas component. It handles:
- Rendering the image and all bounding boxes
- Drawing new boxes (mousedown → drag → mouseup)
- Selecting existing boxes (click inside)
- Moving boxes (drag selected box body)
- Resizing boxes (drag corner/edge handles)
- Delete via callback

**Step 1: Create BBoxCanvas.jsx**

Create `frontend/src/components/BBoxCanvas.jsx` with the following structure:

```jsx
/**
 * BBoxCanvas — HTML5 canvas overlay for drawing/editing bounding boxes.
 *
 * Props:
 *   imageSrc: string — URL to the image
 *   boxes: Array<{id, label, classIndex, x, y, w, h}> — normalized 0-1 coords
 *   selectedBoxId: string|null — ID of currently selected box
 *   activeClassIndex: number — index into class_list for new boxes
 *   classList: string[] — project class list
 *   onBoxCreated: ({x, y, w, h}) => void — called when a new box is drawn
 *   onBoxUpdated: (id, {x, y, w, h}) => void — called when a box is moved/resized
 *   onBoxSelected: (id|null) => void — called when selection changes
 *   onBoxDeleted: (id) => void — called on Delete key
 */

import { useRef, useState, useEffect, useCallback } from 'react';

const CLASS_COLORS = [
  '#4299e0', '#e05252', '#52e088', '#e0c452',
  '#b452e0', '#52d4e0', '#e08a52', '#e052b4',
];

const HANDLE_SIZE = 8;
const MIN_BOX_PX = 5;

export function getClassColor(classIndex) {
  return CLASS_COLORS[classIndex % CLASS_COLORS.length];
}

export default function BBoxCanvas({
  imageSrc,
  boxes,
  selectedBoxId,
  activeClassIndex,
  classList,
  onBoxCreated,
  onBoxUpdated,
  onBoxSelected,
  onBoxDeleted,
}) {
  const canvasRef = useRef(null);
  const containerRef = useRef(null);
  const imgRef = useRef(new Image());
  const [imgLoaded, setImgLoaded] = useState(false);
  const [canvasSize, setCanvasSize] = useState({ w: 0, h: 0 });
  // imageRect: the actual rendered image area within the canvas (letterboxed)
  const [imageRect, setImageRect] = useState({ x: 0, y: 0, w: 0, h: 0 });

  // Interaction state
  const dragRef = useRef(null); // { type: 'draw'|'move'|'resize', ... }

  // Load image
  useEffect(() => {
    setImgLoaded(false);
    const img = imgRef.current;
    img.crossOrigin = 'anonymous';
    img.onload = () => setImgLoaded(true);
    img.src = imageSrc;
  }, [imageSrc]);

  // Resize observer for container
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const ro = new ResizeObserver(entries => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        setCanvasSize({ w: Math.floor(width), h: Math.floor(height) });
      }
    });
    ro.observe(container);
    return () => ro.disconnect();
  }, []);

  // Compute letterboxed image rect
  useEffect(() => {
    if (!imgLoaded || canvasSize.w === 0) return;
    const img = imgRef.current;
    const scale = Math.min(canvasSize.w / img.naturalWidth, canvasSize.h / img.naturalHeight);
    const iw = img.naturalWidth * scale;
    const ih = img.naturalHeight * scale;
    setImageRect({
      x: (canvasSize.w - iw) / 2,
      y: (canvasSize.h - ih) / 2,
      w: iw,
      h: ih,
    });
  }, [imgLoaded, canvasSize]);

  // Convert normalized coords to canvas pixels
  const toCanvas = useCallback((nx, ny) => ({
    cx: imageRect.x + nx * imageRect.w,
    cy: imageRect.y + ny * imageRect.h,
  }), [imageRect]);

  // Convert canvas pixels to normalized coords
  const toNorm = useCallback((cx, cy) => ({
    nx: (cx - imageRect.x) / imageRect.w,
    ny: (cy - imageRect.y) / imageRect.h,
  }), [imageRect]);

  // Draw everything
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !imgLoaded) return;
    const ctx = canvas.getContext('2d');
    canvas.width = canvasSize.w;
    canvas.height = canvasSize.h;

    // Clear
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Draw image (letterboxed)
    ctx.drawImage(imgRef.current, imageRect.x, imageRect.y, imageRect.w, imageRect.h);

    // Draw boxes
    for (const box of boxes) {
      const { cx: bx, cy: by } = toCanvas(box.x, box.y);
      const bw = box.w * imageRect.w;
      const bh = box.h * imageRect.h;
      const color = getClassColor(box.classIndex);
      const isSelected = box.id === selectedBoxId;

      // Fill
      ctx.fillStyle = color + (isSelected ? '40' : '26'); // 25% or 15% opacity
      ctx.fillRect(bx, by, bw, bh);

      // Border
      ctx.strokeStyle = color;
      ctx.lineWidth = isSelected ? 3 : 2;
      ctx.strokeRect(bx, by, bw, bh);

      // Label
      const label = classList[box.classIndex] || box.label;
      ctx.font = '11px Inter, sans-serif';
      const tm = ctx.measureText(label);
      const lh = 16;
      const lx = bx;
      const ly = by - lh - 2;
      ctx.fillStyle = color;
      ctx.fillRect(lx, ly, tm.width + 8, lh);
      ctx.fillStyle = '#fff';
      ctx.fillText(label, lx + 4, ly + 12);

      // Resize handles (if selected)
      if (isSelected) {
        const handles = getHandlePositions(bx, by, bw, bh);
        ctx.fillStyle = '#fff';
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5;
        for (const h of handles) {
          ctx.fillRect(h.x - HANDLE_SIZE / 2, h.y - HANDLE_SIZE / 2, HANDLE_SIZE, HANDLE_SIZE);
          ctx.strokeRect(h.x - HANDLE_SIZE / 2, h.y - HANDLE_SIZE / 2, HANDLE_SIZE, HANDLE_SIZE);
        }
      }
    }

    // Draw in-progress box
    const drag = dragRef.current;
    if (drag && drag.type === 'draw' && drag.current) {
      const color = getClassColor(activeClassIndex);
      const x = Math.min(drag.start.cx, drag.current.cx);
      const y = Math.min(drag.start.cy, drag.current.cy);
      const w = Math.abs(drag.current.cx - drag.start.cx);
      const h = Math.abs(drag.current.cy - drag.start.cy);
      ctx.fillStyle = color + '26';
      ctx.fillRect(x, y, w, h);
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.setLineDash([6, 3]);
      ctx.strokeRect(x, y, w, h);
      ctx.setLineDash([]);
    }
  }, [boxes, selectedBoxId, imgLoaded, canvasSize, imageRect, toCanvas, activeClassIndex, classList]);

  // Mouse handlers
  const getMousePos = (e) => {
    const rect = canvasRef.current.getBoundingClientRect();
    return { cx: e.clientX - rect.left, cy: e.clientY - rect.top };
  };

  const hitTest = (cx, cy) => {
    // Check handles of selected box first
    if (selectedBoxId) {
      const box = boxes.find(b => b.id === selectedBoxId);
      if (box) {
        const { cx: bx, cy: by } = toCanvas(box.x, box.y);
        const bw = box.w * imageRect.w;
        const bh = box.h * imageRect.h;
        const handles = getHandlePositions(bx, by, bw, bh);
        for (let i = 0; i < handles.length; i++) {
          if (Math.abs(cx - handles[i].x) <= HANDLE_SIZE && Math.abs(cy - handles[i].y) <= HANDLE_SIZE) {
            return { type: 'handle', handleIndex: i, box };
          }
        }
      }
    }
    // Check boxes (reverse order = topmost first)
    for (let i = boxes.length - 1; i >= 0; i--) {
      const box = boxes[i];
      const { cx: bx, cy: by } = toCanvas(box.x, box.y);
      const bw = box.w * imageRect.w;
      const bh = box.h * imageRect.h;
      if (cx >= bx && cx <= bx + bw && cy >= by && cy <= by + bh) {
        return { type: 'box', box };
      }
    }
    return { type: 'empty' };
  };

  const handleMouseDown = (e) => {
    if (e.button !== 0) return;
    const pos = getMousePos(e);
    const hit = hitTest(pos.cx, pos.cy);

    if (hit.type === 'handle') {
      onBoxSelected(hit.box.id);
      const { cx: bx, cy: by } = toCanvas(hit.box.x, hit.box.y);
      const bw = hit.box.w * imageRect.w;
      const bh = hit.box.h * imageRect.h;
      dragRef.current = {
        type: 'resize',
        boxId: hit.box.id,
        handleIndex: hit.handleIndex,
        origBox: { bx, by, bw, bh },
        start: pos,
      };
    } else if (hit.type === 'box') {
      onBoxSelected(hit.box.id);
      const { cx: bx, cy: by } = toCanvas(hit.box.x, hit.box.y);
      dragRef.current = {
        type: 'move',
        boxId: hit.box.id,
        offset: { dx: pos.cx - bx, dy: pos.cy - by },
        origNorm: { x: hit.box.x, y: hit.box.y },
      };
    } else {
      onBoxSelected(null);
      dragRef.current = { type: 'draw', start: pos, current: null };
    }
  };

  const handleMouseMove = (e) => {
    const drag = dragRef.current;
    if (!drag) return;
    const pos = getMousePos(e);

    if (drag.type === 'draw') {
      drag.current = pos;
      // Trigger re-render by forcing canvas redraw
      const canvas = canvasRef.current;
      if (canvas) canvas.dispatchEvent(new Event('needsRedraw'));
    } else if (drag.type === 'move') {
      const nx = drag.origNorm.x + (pos.cx - (toCanvas(drag.origNorm.x, drag.origNorm.y).cx + drag.offset.dx - (pos.cx - pos.cx))) / imageRect.w;
      // Simpler: compute new top-left in canvas coords, convert to norm
      const newCx = pos.cx - drag.offset.dx;
      const newCy = pos.cy - drag.offset.dy;
      const { nx, ny } = toNorm(newCx, newCy);
      onBoxUpdated(drag.boxId, { x: nx, y: ny });
    } else if (drag.type === 'resize') {
      const box = boxes.find(b => b.id === drag.boxId);
      if (!box) return;
      const { bx, by, bw, bh } = drag.origBox;
      const dx = pos.cx - drag.start.cx;
      const dy = pos.cy - drag.start.cy;
      const newRect = applyResize(bx, by, bw, bh, drag.handleIndex, dx, dy);
      const tl = toNorm(newRect.x, newRect.y);
      const nw = newRect.w / imageRect.w;
      const nh = newRect.h / imageRect.h;
      onBoxUpdated(drag.boxId, { x: tl.nx, y: tl.ny, w: Math.abs(nw), h: Math.abs(nh) });
    }
  };

  const handleMouseUp = () => {
    const drag = dragRef.current;
    if (!drag) return;

    if (drag.type === 'draw' && drag.current) {
      const w = Math.abs(drag.current.cx - drag.start.cx);
      const h = Math.abs(drag.current.cy - drag.start.cy);
      if (w > MIN_BOX_PX && h > MIN_BOX_PX) {
        const x1 = Math.min(drag.start.cx, drag.current.cx);
        const y1 = Math.min(drag.start.cy, drag.current.cy);
        const tl = toNorm(x1, y1);
        onBoxCreated({
          x: Math.max(0, tl.nx),
          y: Math.max(0, tl.ny),
          w: Math.min(w / imageRect.w, 1 - tl.nx),
          h: Math.min(h / imageRect.h, 1 - tl.ny),
        });
      }
    }
    dragRef.current = null;
  };

  // Keyboard: Delete selected box
  useEffect(() => {
    const handler = (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      if ((e.key === 'Delete' || e.key === 'Backspace') && selectedBoxId) {
        e.preventDefault();
        onBoxDeleted(selectedBoxId);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [selectedBoxId, onBoxDeleted]);

  return (
    <div
      ref={containerRef}
      style={{ width: '100%', height: '100%', position: 'relative', cursor: 'crosshair' }}
    >
      <canvas
        ref={canvasRef}
        style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%' }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      />
    </div>
  );
}

// 8 handle positions: 4 corners + 4 edge midpoints
function getHandlePositions(bx, by, bw, bh) {
  return [
    { x: bx, y: by },                   // 0: top-left
    { x: bx + bw / 2, y: by },          // 1: top-mid
    { x: bx + bw, y: by },              // 2: top-right
    { x: bx + bw, y: by + bh / 2 },    // 3: mid-right
    { x: bx + bw, y: by + bh },         // 4: bottom-right
    { x: bx + bw / 2, y: by + bh },     // 5: bottom-mid
    { x: bx, y: by + bh },              // 6: bottom-left
    { x: bx, y: by + bh / 2 },          // 7: mid-left
  ];
}

// Apply resize delta to a rect based on which handle is being dragged
function applyResize(bx, by, bw, bh, handleIndex, dx, dy) {
  let x = bx, y = by, w = bw, h = bh;
  // Corners
  if (handleIndex === 0) { x += dx; y += dy; w -= dx; h -= dy; }        // TL
  else if (handleIndex === 2) { w += dx; y += dy; h -= dy; }             // TR
  else if (handleIndex === 4) { w += dx; h += dy; }                       // BR
  else if (handleIndex === 6) { x += dx; w -= dx; h += dy; }             // BL
  // Edges
  else if (handleIndex === 1) { y += dy; h -= dy; }                       // Top
  else if (handleIndex === 3) { w += dx; }                                 // Right
  else if (handleIndex === 5) { h += dy; }                                 // Bottom
  else if (handleIndex === 7) { x += dx; w -= dx; }                       // Left
  // Ensure positive dimensions
  if (w < 0) { x += w; w = -w; }
  if (h < 0) { y += h; h = -h; }
  return { x, y, w: Math.max(w, 10), h: Math.max(h, 10) };
}
```

**NOTE:** The above is a complete starting implementation. The move handler has a variable shadowing bug (`nx` declared twice) — the implementing agent should fix this by removing the first computation and keeping only the simpler version:

```javascript
} else if (drag.type === 'move') {
  const newCx = pos.cx - drag.offset.dx;
  const newCy = pos.cy - drag.offset.dy;
  const { nx, ny } = toNorm(newCx, newCy);
  onBoxUpdated(drag.boxId, { x: nx, y: ny });
}
```

Also, the `handleMouseMove` for 'draw' type uses a custom event for redraw. A simpler approach is to use a `drawPreview` state that triggers re-render:

Replace the draw branch in `handleMouseMove` with a state-based approach. Add a `drawPreview` state:
```javascript
const [drawPreview, setDrawPreview] = useState(null);
```
In `handleMouseMove` draw case, call `setDrawPreview(pos)` instead of the custom event.
In `handleMouseUp`, call `setDrawPreview(null)`.
In the render effect, use `drawPreview` instead of `dragRef.current.current`.

**Step 2: Commit**

```bash
git add frontend/src/components/BBoxCanvas.jsx
git commit -m "feat: add BBoxCanvas component for detection labeling"
```

---

### Task 4: LabelingView — branch on task_type for detection mode

**Files:**
- Modify: `frontend/src/pages/LabelingView.jsx`

This is the largest change. The LabelingView needs to:
1. Import `BBoxCanvas`, `getClassColor`, and `annotateSampleBatch`
2. Add detection-specific state (boxes, selectedBoxId, activeClassIndex)
3. When `task_type === 'detection'`, render BBoxCanvas instead of `<img>`, and render detection right panel instead of classification buttons
4. Add Save & Next handler that calls `annotateSampleBatch`
5. Load existing annotations from sample into local box state
6. Update keyboard shortcuts (Enter = Save & Next, Delete = remove box)

**Step 1: Add imports**

At the top of `LabelingView.jsx`, add to existing imports:

```javascript
import BBoxCanvas, { getClassColor } from '../components/BBoxCanvas';
```

And add `annotateSampleBatch` to the API client import:

```javascript
import {
  fetchProject,
  fetchProjectStats,
  fetchNextSample,
  annotateSample,
  annotateSampleBatch,
  skipSample,
  sampleImageUrl,
  addProjectClass,
} from '../api/client';
```

**Step 2: Add detection state**

After the existing state declarations (line ~32), add:

```javascript
// Detection mode state
const [boxes, setBoxes] = useState([]);
const [selectedBoxId, setSelectedBoxId] = useState(null);
const [activeClassIndex, setActiveClassIndex] = useState(0);
const nextBoxId = useRef(0);
```

**Step 3: Initialize boxes from loaded sample annotations**

After the `loadNext` function loads a sample, populate boxes from existing annotations. Modify the `loadNext` callback to also set boxes:

Inside the `if (next)` block of `loadNext`, after `setSample(next)`:

```javascript
// Load existing annotations into box state (for re-labeling)
if (next.annotations && next.annotations.length > 0 && project?.task_type === 'detection') {
  const existingBoxes = next.annotations
    .filter(a => a.ann_type === 'bbox' && a.bbox_json)
    .map(a => ({
      id: `existing-${nextBoxId.current++}`,
      label: a.label,
      classIndex: project.class_list.indexOf(a.label),
      ...a.bbox_json,
    }));
  setBoxes(existingBoxes);
} else {
  setBoxes([]);
}
setSelectedBoxId(null);
```

**Step 4: Add detection handlers**

After the `handleAddClass` function, add:

```javascript
// Detection: box CRUD
const handleBoxCreated = (rect) => {
  const id = `box-${nextBoxId.current++}`;
  setBoxes(prev => [...prev, {
    id,
    label: project.class_list[activeClassIndex] || '',
    classIndex: activeClassIndex,
    ...rect,
  }]);
  setSelectedBoxId(id);
};

const handleBoxUpdated = (id, updates) => {
  setBoxes(prev => prev.map(b => b.id === id ? { ...b, ...updates } : b));
};

const handleBoxDeleted = (id) => {
  setBoxes(prev => prev.filter(b => b.id !== id));
  if (selectedBoxId === id) setSelectedBoxId(null);
};

// Detection: Save & Next
const handleSaveBoxes = async () => {
  if (!sample || saving || boxes.length === 0) return;
  setSaving(true);
  try {
    const annotations = boxes.map(b => ({
      label: b.label,
      ann_type: 'bbox',
      bbox_json: { x: b.x, y: b.y, w: b.w, h: b.h },
    }));
    await annotateSampleBatch(projectId, sample.id, annotations);
    loadStats();
    await loadNext();
  } catch (err) {
    console.error('Save failed:', err);
  } finally {
    setSaving(false);
  }
};
```

**Step 5: Update keyboard shortcuts**

Replace the existing keyboard handler `useEffect` (the one that handles number keys and skip) with a version that branches on task type:

```javascript
useEffect(() => {
  if (!project || !sample) return;
  const isDetection = project.task_type === 'detection';

  const handler = (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

    // Number keys 1-9: class selection
    if (e.key >= '1' && e.key <= '9') {
      const idx = parseInt(e.key) - 1;
      if (idx < project.class_list.length) {
        if (isDetection) {
          setActiveClassIndex(idx);
        } else {
          handleClassify(project.class_list[idx]);
        }
      }
      return;
    }
    if (e.key === 's' || e.key === 'S') {
      e.preventDefault();
      handleSkip();
    } else if (e.key === 'Enter' && isDetection) {
      e.preventDefault();
      handleSaveBoxes();
    } else if (e.key === 'Escape') {
      if (isDetection && selectedBoxId) {
        setSelectedBoxId(null);
      } else {
        navigate(`/projects/${projectId}`);
      }
    }
  };
  window.addEventListener('keydown', handler);
  return () => window.removeEventListener('keydown', handler);
}, [project, sample, saving, projectId, navigate, selectedBoxId, activeClassIndex, boxes]);
```

**Step 6: Replace center image area**

In the JSX, replace the center image `<div>` (the one with `flex: 3` that contains the `<img>` tag) with a conditional:

```jsx
{/* Center: Image or BBoxCanvas */}
<div
  style={{
    flex: 3,
    background: 'var(--bg-secondary)',
    borderRadius: 8,
    border: '1px solid var(--border-color)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
    position: 'relative',
  }}
>
  {loading ? (
    <Spinner label="Loading next image..." />
  ) : sample ? (
    project.task_type === 'detection' ? (
      <BBoxCanvas
        imageSrc={sampleImageUrl(projectId, sample.id)}
        boxes={boxes}
        selectedBoxId={selectedBoxId}
        activeClassIndex={activeClassIndex}
        classList={project.class_list}
        onBoxCreated={handleBoxCreated}
        onBoxUpdated={handleBoxUpdated}
        onBoxSelected={setSelectedBoxId}
        onBoxDeleted={handleBoxDeleted}
      />
    ) : (
      <>
        {!imageLoaded && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Spinner size={40} label="" />
          </div>
        )}
        <img
          src={sampleImageUrl(projectId, sample.id)}
          alt={sample.filename}
          onLoad={() => setImageLoaded(true)}
          style={{
            maxWidth: '100%',
            maxHeight: '100%',
            objectFit: 'contain',
            opacity: imageLoaded ? 1 : 0,
            transition: 'opacity 0.2s',
          }}
        />
      </>
    )
  ) : null}
</div>
```

**Step 7: Replace right panel content**

Replace the right panel's class buttons section with a conditional. When `project.task_type === 'detection'`, render:

```jsx
{/* Class selector (detection mode) */}
<div style={{ flex: 1, overflowY: 'auto' }}>
  <h4 style={{ fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.5rem', color: 'var(--text-secondary)' }}>
    Active Class
  </h4>
  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
    {project.class_list.map((cls, i) => (
      <button
        key={cls}
        className="btn-secondary"
        onClick={() => setActiveClassIndex(i)}
        style={{
          textAlign: 'left',
          fontSize: '0.85rem',
          padding: '0.5rem 0.75rem',
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          border: i === activeClassIndex ? `2px solid ${getClassColor(i)}` : undefined,
          background: i === activeClassIndex ? getClassColor(i) + '20' : undefined,
        }}
      >
        <span style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 22,
          height: 22,
          borderRadius: 4,
          background: getClassColor(i) + '33',
          color: getClassColor(i),
          fontSize: '0.75rem',
          fontWeight: 700,
          flexShrink: 0,
        }}>
          {i + 1}
        </span>
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: getClassColor(i), flexShrink: 0 }} />
        {cls}
      </button>
    ))}
  </div>

  {/* Annotation list */}
  <div style={{ marginTop: '1rem' }}>
    <h4 style={{ fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.5rem', color: 'var(--text-secondary)' }}>
      Annotations ({boxes.length})
    </h4>
    {boxes.length === 0 ? (
      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>
        Draw boxes on the image
      </div>
    ) : (
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
        {boxes.map((box) => (
          <div
            key={box.id}
            onClick={() => setSelectedBoxId(box.id)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.4rem',
              padding: '0.3rem 0.5rem',
              borderRadius: 4,
              fontSize: '0.8rem',
              cursor: 'pointer',
              background: box.id === selectedBoxId ? 'var(--bg-hover)' : 'transparent',
              border: box.id === selectedBoxId ? '1px solid var(--border-hover)' : '1px solid transparent',
            }}
          >
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: getClassColor(box.classIndex), flexShrink: 0 }} />
            <span style={{ flex: 1 }}>{box.label}</span>
            <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
              {Math.round(box.w * 100)}×{Math.round(box.h * 100)}
            </span>
            <button
              onClick={(e) => { e.stopPropagation(); handleBoxDeleted(box.id); }}
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--text-muted)',
                cursor: 'pointer',
                padding: '0 0.2rem',
                fontSize: '0.75rem',
              }}
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    )}
  </div>
</div>
```

And replace the Skip button area with:

```jsx
{/* Save & Next (detection) or Skip */}
{project.task_type === 'detection' && (
  <button
    className="btn-primary"
    onClick={handleSaveBoxes}
    disabled={saving || boxes.length === 0}
    style={{ width: '100%', fontSize: '0.85rem', marginBottom: '0.5rem' }}
  >
    {saving ? 'Saving...' : `Save & Next (${boxes.length})`}
  </button>
)}

<button
  className="btn-secondary"
  onClick={handleSkip}
  disabled={saving}
  style={{ width: '100%', fontSize: '0.85rem', marginBottom: '0.75rem' }}
>
  Skip (S)
</button>

<div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
  [1-{Math.min(9, project.class_list.length)}] class &middot; [S] skip
  {project.task_type === 'detection' && <> &middot; [Enter] save &middot; [Del] remove</>}
  {project.task_type !== 'detection' && <> &middot; label</>}
  &middot; [Esc] back
</div>
```

**Step 8: Commit**

```bash
git add frontend/src/pages/LabelingView.jsx
git commit -m "feat: add detection labeling mode with bbox drawing to LabelingView"
```

---

### Task 5: Build, deploy, and verify

**Files:** None (operational)

**Step 1: Build frontend**

```bash
cd /workspace/group/cv-react-deploy/frontend && npm run build
```

Expected: Build succeeds with no errors.

**Step 2: Upload to Databricks workspace**

Upload all changed files (backend + frontend dist) to `/Workspace/Users/brian.law@databricks.com/apps/cv-explorer-react` using the same upload script pattern used previously.

**Step 3: Deploy**

```python
w.apps.deploy('cv-explorer-react', app_deployment=AppDeployment(source_code_path=ws_path))
```

**Step 4: Verify app is running**

Check `app.app_status.state == RUNNING` and `active_deployment.status.state == SUCCEEDED`.

**Step 5: Final commit and push**

```bash
git add -A
git commit -m "feat: detection labeling with bbox canvas, batch save, and editing"
git push origin main
```
