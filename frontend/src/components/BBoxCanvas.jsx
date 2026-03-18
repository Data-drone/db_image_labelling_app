/**
 * BBoxCanvas — HTML5 canvas overlay for drawing/editing bounding boxes.
 *
 * Props:
 *   imageSrc: string — URL to the image
 *   boxes: Array<{id, label, classIndex, x, y, w, h}> — normalized 0-1 coords
 *   selectedBoxId: string|null — ID of currently selected box
 *   activeClassIndex: number — index into class_list for new boxes
 *   classList: string[] — project class list
 *   onBoxCreated: ({x, y, w, h}) => void
 *   onBoxUpdated: (id, {x, y, w, h}) => void
 *   onBoxSelected: (id|null) => void
 *   onBoxDeleted: (id) => void
 */

import { useRef, useState, useEffect, useCallback } from 'react';

const CLASS_COLORS = [
  '#4299e0', '#e05252', '#52e088', '#e0c452',
  '#b452e0', '#52d4e0', '#e08a52', '#e052b4',
];

const HANDLE_SIZE = 8;
const MIN_BOX_PX = 5;

export function getClassColor(classIndex) {
  return CLASS_COLORS[(classIndex >= 0 ? classIndex : 0) % CLASS_COLORS.length];
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
  const [imageRect, setImageRect] = useState({ x: 0, y: 0, w: 0, h: 0 });
  const [drawPreview, setDrawPreview] = useState(null); // {startCx, startCy, cx, cy}

  // Drag state (not in React state — updated on every mousemove)
  const dragRef = useRef(null);

  // Load image
  useEffect(() => {
    setImgLoaded(false);
    const img = imgRef.current;
    img.crossOrigin = 'anonymous';
    img.onload = () => setImgLoaded(true);
    img.onerror = () => console.error('BBoxCanvas: failed to load image');
    img.src = imageSrc;
  }, [imageSrc]);

  // Resize observer
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

  // Normalized <-> canvas pixel conversion
  const toCanvas = useCallback((nx, ny) => ({
    cx: imageRect.x + nx * imageRect.w,
    cy: imageRect.y + ny * imageRect.h,
  }), [imageRect]);

  const toNorm = useCallback((cx, cy) => ({
    nx: imageRect.w > 0 ? (cx - imageRect.x) / imageRect.w : 0,
    ny: imageRect.h > 0 ? (cy - imageRect.y) / imageRect.h : 0,
  }), [imageRect]);

  // ---------- Rendering ----------
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !imgLoaded) return;
    const ctx = canvas.getContext('2d');
    canvas.width = canvasSize.w;
    canvas.height = canvasSize.h;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Image (letterboxed)
    ctx.drawImage(imgRef.current, imageRect.x, imageRect.y, imageRect.w, imageRect.h);

    // Boxes
    for (const box of boxes) {
      const { cx: bx, cy: by } = toCanvas(box.x, box.y);
      const bw = box.w * imageRect.w;
      const bh = box.h * imageRect.h;
      const color = getClassColor(box.classIndex);
      const isSelected = box.id === selectedBoxId;

      // Fill
      ctx.fillStyle = color + (isSelected ? '40' : '26');
      ctx.fillRect(bx, by, bw, bh);

      // Border
      ctx.strokeStyle = color;
      ctx.lineWidth = isSelected ? 3 : 2;
      ctx.strokeRect(bx, by, bw, bh);

      // Label above box
      const label = classList[box.classIndex] || box.label;
      ctx.font = '11px Inter, sans-serif';
      const tm = ctx.measureText(label);
      const lh = 16;
      const ly = by - lh - 2;
      ctx.fillStyle = color;
      ctx.fillRect(bx, ly, tm.width + 8, lh);
      ctx.fillStyle = '#fff';
      ctx.fillText(label, bx + 4, ly + 12);

      // Resize handles (selected only)
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

    // Draw preview (in-progress box)
    if (drawPreview) {
      const color = getClassColor(activeClassIndex);
      const x = Math.min(drawPreview.startCx, drawPreview.cx);
      const y = Math.min(drawPreview.startCy, drawPreview.cy);
      const w = Math.abs(drawPreview.cx - drawPreview.startCx);
      const h = Math.abs(drawPreview.cy - drawPreview.startCy);
      ctx.fillStyle = color + '26';
      ctx.fillRect(x, y, w, h);
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.setLineDash([6, 3]);
      ctx.strokeRect(x, y, w, h);
      ctx.setLineDash([]);
    }
  }, [boxes, selectedBoxId, imgLoaded, canvasSize, imageRect, toCanvas, activeClassIndex, classList, drawPreview]);

  // ---------- Mouse interaction ----------
  const getMousePos = (e) => {
    const rect = canvasRef.current.getBoundingClientRect();
    return { cx: e.clientX - rect.left, cy: e.clientY - rect.top };
  };

  const hitTestHandles = (cx, cy) => {
    if (!selectedBoxId) return -1;
    const box = boxes.find(b => b.id === selectedBoxId);
    if (!box) return -1;
    const { cx: bx, cy: by } = toCanvas(box.x, box.y);
    const bw = box.w * imageRect.w;
    const bh = box.h * imageRect.h;
    const handles = getHandlePositions(bx, by, bw, bh);
    for (let i = 0; i < handles.length; i++) {
      if (Math.abs(cx - handles[i].x) <= HANDLE_SIZE && Math.abs(cy - handles[i].y) <= HANDLE_SIZE) {
        return i;
      }
    }
    return -1;
  };

  const hitTestBox = (cx, cy) => {
    for (let i = boxes.length - 1; i >= 0; i--) {
      const box = boxes[i];
      const { cx: bx, cy: by } = toCanvas(box.x, box.y);
      const bw = box.w * imageRect.w;
      const bh = box.h * imageRect.h;
      if (cx >= bx && cx <= bx + bw && cy >= by && cy <= by + bh) {
        return box;
      }
    }
    return null;
  };

  const handleMouseDown = (e) => {
    if (e.button !== 0) return;
    const pos = getMousePos(e);

    // Check handles first
    const handleIdx = hitTestHandles(pos.cx, pos.cy);
    if (handleIdx >= 0) {
      const box = boxes.find(b => b.id === selectedBoxId);
      const { cx: bx, cy: by } = toCanvas(box.x, box.y);
      const bw = box.w * imageRect.w;
      const bh = box.h * imageRect.h;
      dragRef.current = {
        type: 'resize',
        boxId: box.id,
        handleIndex: handleIdx,
        origBox: { bx, by, bw, bh },
        start: pos,
      };
      return;
    }

    // Check box body
    const hitBox = hitTestBox(pos.cx, pos.cy);
    if (hitBox) {
      onBoxSelected(hitBox.id);
      const { cx: bx, cy: by } = toCanvas(hitBox.x, hitBox.y);
      dragRef.current = {
        type: 'move',
        boxId: hitBox.id,
        offset: { dx: pos.cx - bx, dy: pos.cy - by },
        origNorm: { x: hitBox.x, y: hitBox.y },
      };
      return;
    }

    // Empty area — start drawing
    onBoxSelected(null);
    dragRef.current = { type: 'draw', start: pos };
    setDrawPreview({ startCx: pos.cx, startCy: pos.cy, cx: pos.cx, cy: pos.cy });
  };

  const handleMouseMove = (e) => {
    const drag = dragRef.current;
    if (!drag) return;
    const pos = getMousePos(e);

    if (drag.type === 'draw') {
      setDrawPreview(prev => prev ? { ...prev, cx: pos.cx, cy: pos.cy } : null);
    } else if (drag.type === 'move') {
      const newCx = pos.cx - drag.offset.dx;
      const newCy = pos.cy - drag.offset.dy;
      const { nx, ny } = toNorm(newCx, newCy);
      onBoxUpdated(drag.boxId, { x: nx, y: ny });
    } else if (drag.type === 'resize') {
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
    if (drag && drag.type === 'draw' && drawPreview) {
      const w = Math.abs(drawPreview.cx - drawPreview.startCx);
      const h = Math.abs(drawPreview.cy - drawPreview.startCy);
      if (w > MIN_BOX_PX && h > MIN_BOX_PX) {
        const x1 = Math.min(drawPreview.startCx, drawPreview.cx);
        const y1 = Math.min(drawPreview.startCy, drawPreview.cy);
        const tl = toNorm(x1, y1);
        onBoxCreated({
          x: Math.max(0, tl.nx),
          y: Math.max(0, tl.ny),
          w: Math.min(w / imageRect.w, 1 - Math.max(0, tl.nx)),
          h: Math.min(h / imageRect.h, 1 - Math.max(0, tl.ny)),
        });
      }
    }
    dragRef.current = null;
    setDrawPreview(null);
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
      {!imgLoaded && (
        <div style={{
          position: 'absolute', inset: 0, display: 'flex',
          alignItems: 'center', justifyContent: 'center',
          color: 'var(--text-muted)', fontSize: '0.85rem',
        }}>
          Loading image...
        </div>
      )}
      <canvas
        ref={canvasRef}
        style={{
          position: 'absolute', top: 0, left: 0,
          width: '100%', height: '100%',
          opacity: imgLoaded ? 1 : 0,
        }}
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

// Apply resize delta based on which handle is dragged
function applyResize(bx, by, bw, bh, handleIndex, dx, dy) {
  let x = bx, y = by, w = bw, h = bh;
  if (handleIndex === 0) { x += dx; y += dy; w -= dx; h -= dy; }
  else if (handleIndex === 1) { y += dy; h -= dy; }
  else if (handleIndex === 2) { w += dx; y += dy; h -= dy; }
  else if (handleIndex === 3) { w += dx; }
  else if (handleIndex === 4) { w += dx; h += dy; }
  else if (handleIndex === 5) { h += dy; }
  else if (handleIndex === 6) { x += dx; w -= dx; h += dy; }
  else if (handleIndex === 7) { x += dx; w -= dx; }
  if (w < 0) { x += w; w = -w; }
  if (h < 0) { y += h; h = -h; }
  return { x, y, w: Math.max(w, 10), h: Math.max(h, 10) };
}
