/**
 * MaskCanvas — brush/eraser mask editor for segmentation projects.
 *
 * Sibling of BBoxCanvas: same letterboxing, same class palette, same
 * "container + ResizeObserver + canvas" shape. Differences that matter:
 *
 *   - The source of truth is a row-major `Uint8Array(w * h)` at the image's
 *     native resolution, NOT the canvas. The canvas is display-only. Reading a
 *     full-resolution canvas back with getImageData on every mousemove is the
 *     obvious implementation and it stalls the GPU pipeline on large photos.
 *   - Three stacked canvases instead of one, so brightness/contrast can be a
 *     plain CSS filter on the image layer without tinting the mask overlay.
 *   - Brush size is in *image* pixels, so zooming in gives you finer strokes
 *     rather than the same coarse ones.
 *
 * Props:
 *   imageSrc: string
 *   imageWidth, imageHeight: number|null — backend dimensions, in the
 *     orientation the browser renders (i.e. post-EXIF). Used only as a
 *     cross-check; naturalWidth/Height wins because that is what is on screen.
 *   masks: Array<{id, label, classIndex, mask_json, isDraft}> — mask_json is
 *     uncompressed COCO RLE ({size, counts}) as returned by the API.
 *   activeMaskId: string|number|null — the one the brush edits
 *   activeClassIndex: number, classList: string[]
 *   tool: 'brush' | 'eraser' | 'polygon'
 *   brushSize: number — diameter in image pixels (brush and eraser only)
 *   brightness, contrast: number — 1 = unchanged
 *   onMaskUpdated: (id, {size, counts}|null) => void — null when erased empty
 *   onMaskSelected: (id) => void
 *   onHistoryChange: ({canUndo, canRedo}) => void
 *
 * Imperative handle: undo(), redo(), clear(), resetView(), zoomBy(factor),
 * cancelPolygon()
 */

import {
  useRef, useState, useEffect, useCallback, useImperativeHandle, forwardRef,
} from 'react';
import { getClassColor } from './BBoxCanvas';
import { encodeMask, decodeMask, maskIsEmpty, countsOf, sizeOf } from '../utils/rle';

const UNDO_LIMIT = 20;
/** Click within this many *screen* px of the first vertex to close a polygon. */
const CLOSE_SNAP_PX = 10;
const MIN_ZOOM = 1;
const MAX_ZOOM = 16;

/** '#rrggbb' -> {r,g,b}. */
function hexToRgb(hex) {
  return {
    r: parseInt(hex.slice(1, 3), 16),
    g: parseInt(hex.slice(3, 5), 16),
    b: parseInt(hex.slice(5, 7), 16),
  };
}

/** Full-resolution RGBA canvas for one mask, used as a drawImage source. */
function overlayCanvasFor(buf, width, height, hex, alpha) {
  const cv = document.createElement('canvas');
  cv.width = width;
  cv.height = height;
  const data = cv.getContext('2d').createImageData(width, height);
  const { r, g, b } = hexToRgb(hex);
  const px = data.data;
  for (let i = 0; i < buf.length; i++) {
    if (!buf[i]) continue;
    const o = i * 4;
    px[o] = r; px[o + 1] = g; px[o + 2] = b; px[o + 3] = alpha;
  }
  cv.getContext('2d').putImageData(data, 0, 0);
  return cv;
}

const MaskCanvas = forwardRef(function MaskCanvas({
  imageSrc,
  imageWidth = null,
  imageHeight = null,
  masks = [],
  activeMaskId = null,
  activeClassIndex = 0,
  classList = [],
  tool = 'brush',
  brushSize = 24,
  brightness = 1,
  contrast = 1,
  onMaskUpdated,
  onMaskSelected,
  onHistoryChange,
}, ref) {
  const containerRef = useRef(null);
  const imageCanvasRef = useRef(null);   // photo, CSS-filtered
  const staticCanvasRef = useRef(null);  // every mask except the active one
  const activeCanvasRef = useRef(null);  // active mask + brush cursor; takes events

  const imgRef = useRef(new Image());
  const [imgLoaded, setImgLoaded] = useState(false);
  const [canvasSize, setCanvasSize] = useState({ w: 0, h: 0 });
  const [dims, setDims] = useState({ w: 0, h: 0 }); // native image pixels
  const [view, setView] = useState({ zoom: 1, panX: 0, panY: 0 });

  // Active mask working state. Refs, not state: mutated on every mousemove.
  const bufRef = useRef(null);           // Uint8Array(w*h), source of truth
  const maskCanvasRef = useRef(null);    // full-res RGBA mirror of bufRef
  const maskDataRef = useRef(null);      // its ImageData, kept in sync
  const loadedKeyRef = useRef(null);     // which mask bufRef currently holds
  const strokeRef = useRef(null);        // {dirty: Map<index, prevValue>, lastX, lastY}
  const undoRef = useRef([]);
  const redoRef = useRef([]);
  const cursorRef = useRef(null);        // {cx, cy} in canvas px, or null
  const polyRef = useRef([]);            // in-progress polygon, image-space points
  const staticOverlayRef = useRef(null); // composited non-active masks, full-res
  const panRef = useRef(null);

  const activeMask = masks.find((m) => String(m.id) === String(activeMaskId)) || null;
  const activeColor = getClassColor(activeMask ? activeMask.classIndex : activeClassIndex);

  const reportHistory = useCallback(() => {
    onHistoryChange?.({ canUndo: undoRef.current.length > 0, canRedo: redoRef.current.length > 0 });
  }, [onHistoryChange]);

  // ---------- Image loading ----------
  useEffect(() => {
    setImgLoaded(false);
    const img = imgRef.current;
    img.crossOrigin = 'anonymous';
    img.onload = () => {
      setDims({ w: img.naturalWidth, h: img.naturalHeight });
      setImgLoaded(true);
    };
    img.onerror = () => console.error('MaskCanvas: failed to load image');
    img.src = imageSrc;
  }, [imageSrc]);

  // The mask grid must match what is on screen, so naturalWidth wins. A
  // mismatch means the backend cached dimensions before EXIF handling landed,
  // or the file changed underneath; either way the save will 422 and the user
  // deserves a console breadcrumb rather than a silent coordinate shift.
  useEffect(() => {
    if (!imgLoaded || !imageWidth || !imageHeight) return;
    if (imageWidth !== dims.w || imageHeight !== dims.h) {
      console.warn(
        `MaskCanvas: backend reports ${imageWidth}x${imageHeight} but the image ` +
        `renders ${dims.w}x${dims.h}; drawing against the rendered size.`,
      );
    }
  }, [imgLoaded, imageWidth, imageHeight, dims]);

  // ---------- Resize ----------
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        setCanvasSize({ w: Math.floor(width), h: Math.floor(height) });
      }
    });
    ro.observe(container);
    return () => ro.disconnect();
  }, []);

  // ---------- Letterbox + zoom/pan -> image rect in canvas px ----------
  const rectOf = useCallback((v) => {
    if (!dims.w || !canvasSize.w) return { x: 0, y: 0, w: 0, h: 0 };
    const base = Math.min(canvasSize.w / dims.w, canvasSize.h / dims.h);
    const scale = base * v.zoom;
    const iw = dims.w * scale;
    const ih = dims.h * scale;
    return { x: (canvasSize.w - iw) / 2 + v.panX, y: (canvasSize.h - ih) / 2 + v.panY, w: iw, h: ih };
  }, [dims, canvasSize]);

  const imageRect = rectOf(view);
  const rectRef = useRef(imageRect);
  rectRef.current = imageRect;

  /** Canvas px -> image px (may fall outside the image; callers clamp). */
  const toImage = useCallback((cx, cy) => {
    const r = rectRef.current;
    if (!r.w) return { ix: 0, iy: 0 };
    return { ix: ((cx - r.x) / r.w) * dims.w, iy: ((cy - r.y) / r.h) * dims.h };
  }, [dims]);

  // ---------- Load the active mask into the working buffer ----------
  // Keyed on mask identity, not contents: re-syncing on every `masks` change
  // would fight the parent's state update after our own onMaskUpdated.
  useEffect(() => {
    if (!dims.w) return;
    const key = `${imageSrc}|${activeMaskId}|${dims.w}x${dims.h}`;
    if (loadedKeyRef.current === key) return;
    loadedKeyRef.current = key;

    const counts = countsOf(activeMask?.mask_json);
    let buf;
    if (counts) {
      const [mh, mw] = sizeOf(activeMask.mask_json, dims.h, dims.w);
      if (mh === dims.h && mw === dims.w) {
        buf = decodeMask(counts, dims.w, dims.h);
      } else {
        console.warn(`MaskCanvas: stored mask is ${mw}x${mh}, image is ${dims.w}x${dims.h}; starting blank.`);
        buf = new Uint8Array(dims.w * dims.h);
      }
    } else {
      buf = new Uint8Array(dims.w * dims.h);
    }
    bufRef.current = buf;

    const cv = document.createElement('canvas');
    cv.width = dims.w;
    cv.height = dims.h;
    maskCanvasRef.current = cv;
    maskDataRef.current = cv.getContext('2d').createImageData(dims.w, dims.h);
    repaintMirror();

    undoRef.current = [];
    redoRef.current = [];
    polyRef.current = [];
    reportHistory();
    renderActive();
    // repaintMirror/renderActive are stable-by-construction closures over refs
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [imageSrc, activeMaskId, dims, activeMask]);

  /** Rebuild the whole full-res RGBA mirror from bufRef. */
  const repaintMirror = useCallback(() => {
    const cv = maskCanvasRef.current;
    const data = maskDataRef.current;
    const buf = bufRef.current;
    if (!cv || !data || !buf) return;
    const { r, g, b } = hexToRgb(activeColor);
    const px = data.data;
    for (let i = 0; i < buf.length; i++) {
      const o = i * 4;
      if (buf[i]) {
        px[o] = r; px[o + 1] = g; px[o + 2] = b; px[o + 3] = 150;
      } else {
        px[o + 3] = 0;
      }
    }
    cv.getContext('2d').putImageData(data, 0, 0);
  }, [activeColor]);

  // Recolour when the active mask's class changes.
  useEffect(() => {
    if (!bufRef.current) return;
    repaintMirror();
    renderActive();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeColor]);

  // ---------- Rendering ----------
  const renderImage = useCallback(() => {
    const canvas = imageCanvasRef.current;
    if (!canvas || !imgLoaded) return;
    canvas.width = canvasSize.w;
    canvas.height = canvasSize.h;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    // Nearest-neighbour when magnified so mask edges line up with the pixels
    // they actually cover instead of a smoothed approximation of them.
    ctx.imageSmoothingEnabled = view.zoom < 2;
    const r = rectRef.current;
    ctx.drawImage(imgRef.current, r.x, r.y, r.w, r.h);
  }, [imgLoaded, canvasSize, view.zoom]);

  const renderStatic = useCallback(() => {
    const canvas = staticCanvasRef.current;
    if (!canvas) return;
    canvas.width = canvasSize.w;
    canvas.height = canvasSize.h;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const src = staticOverlayRef.current;
    if (!src) return;
    ctx.imageSmoothingEnabled = view.zoom < 2;
    const r = rectRef.current;
    ctx.drawImage(src, r.x, r.y, r.w, r.h);
  }, [canvasSize, view.zoom]);

  const renderActive = useCallback(() => {
    const canvas = activeCanvasRef.current;
    if (!canvas) return;
    if (canvas.width !== canvasSize.w || canvas.height !== canvasSize.h) {
      canvas.width = canvasSize.w;
      canvas.height = canvasSize.h;
    }
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const r = rectRef.current;
    if (maskCanvasRef.current && r.w) {
      ctx.imageSmoothingEnabled = view.zoom < 2;
      ctx.drawImage(maskCanvasRef.current, r.x, r.y, r.w, r.h);
    }
    // In-progress polygon: vertices plus a rubber band to the cursor.
    const poly = polyRef.current;
    const cursor = cursorRef.current;
    if (poly.length > 0 && r.w && dims.w) {
      const px = (pt) => ({ x: r.x + (pt.ix / dims.w) * r.w, y: r.y + (pt.iy / dims.h) * r.h });
      ctx.beginPath();
      const first = px(poly[0]);
      ctx.moveTo(first.x, first.y);
      for (let i = 1; i < poly.length; i++) {
        const q = px(poly[i]);
        ctx.lineTo(q.x, q.y);
      }
      if (cursor) ctx.lineTo(cursor.cx, cursor.cy);
      ctx.strokeStyle = activeColor;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([5, 3]);
      ctx.stroke();
      ctx.setLineDash([]);
      for (const pt of poly) {
        const q = px(pt);
        ctx.beginPath();
        ctx.arc(q.x, q.y, 3, 0, Math.PI * 2);
        ctx.fillStyle = '#fff';
        ctx.fill();
        ctx.strokeStyle = activeColor;
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
      return; // no brush ring while placing vertices
    }

    // Brush cursor: a circle the true size of the stroke it will lay down.
    const cur = cursorRef.current;
    if (cur && r.w && dims.w && tool !== 'polygon') {
      const radius = (brushSize / 2) * (r.w / dims.w);
      ctx.beginPath();
      ctx.arc(cur.cx, cur.cy, Math.max(2, radius), 0, Math.PI * 2);
      ctx.strokeStyle = tool === 'eraser' ? '#ffffff' : activeColor;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.strokeStyle = '#00000080';
      ctx.lineWidth = 0.75;
      ctx.stroke();
    }
  }, [canvasSize, view.zoom, brushSize, tool, activeColor, dims]);

  useEffect(renderImage, [renderImage, view]);
  useEffect(renderStatic, [renderStatic, view]);
  useEffect(renderActive, [renderActive, view]);

  // Composite the non-active masks once per mask-list change, not per frame.
  useEffect(() => {
    if (!dims.w) {
      staticOverlayRef.current = null;
      return;
    }
    const others = masks.filter((m) => String(m.id) !== String(activeMaskId) && countsOf(m.mask_json));
    if (others.length === 0) {
      staticOverlayRef.current = null;
      renderStatic();
      return;
    }
    const cv = document.createElement('canvas');
    cv.width = dims.w;
    cv.height = dims.h;
    const ctx = cv.getContext('2d');
    for (const m of others) {
      const [mh, mw] = sizeOf(m.mask_json, dims.h, dims.w);
      if (mh !== dims.h || mw !== dims.w) continue;
      const buf = decodeMask(countsOf(m.mask_json), dims.w, dims.h);
      // Drafts read as a fainter wash so a model suggestion is visibly
      // provisional, matching the dashed borders BBoxCanvas uses.
      const layer = overlayCanvasFor(buf, dims.w, dims.h, getClassColor(m.classIndex), m.isDraft ? 70 : 110);
      ctx.drawImage(layer, 0, 0);
    }
    staticOverlayRef.current = cv;
    renderStatic();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [masks, activeMaskId, dims]);

  // ---------- Painting ----------
  /** Stamp a filled disc into bufRef and its RGBA mirror. Returns a dirty rect. */
  const stampDisc = useCallback((ix, iy, radius, erase, dirty) => {
    const w = dims.w, h = dims.h;
    const buf = bufRef.current;
    const data = maskDataRef.current;
    if (!buf || !data) return null;
    const r = Math.max(0.5, radius);
    const x0 = Math.max(0, Math.floor(ix - r));
    const x1 = Math.min(w - 1, Math.ceil(ix + r));
    const y0 = Math.max(0, Math.floor(iy - r));
    const y1 = Math.min(h - 1, Math.ceil(iy + r));
    if (x1 < x0 || y1 < y0) return null;
    const rr = r * r;
    const val = erase ? 0 : 1;
    const { r: cr, g: cg, b: cb } = hexToRgb(activeColor);
    const px = data.data;
    let minX = w, minY = h, maxX = -1, maxY = -1;
    for (let y = y0; y <= y1; y++) {
      const dy = y + 0.5 - iy;
      const dySq = dy * dy;
      for (let x = x0; x <= x1; x++) {
        const dx = x + 0.5 - ix;
        if (dx * dx + dySq > rr) continue;
        const i = y * w + x;
        if (buf[i] === val) continue;
        if (dirty && !dirty.has(i)) dirty.set(i, buf[i]);
        buf[i] = val;
        const o = i * 4;
        if (val) {
          px[o] = cr; px[o + 1] = cg; px[o + 2] = cb; px[o + 3] = 150;
        } else {
          px[o + 3] = 0;
        }
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
    if (maxX < 0) return null;
    return { x: minX, y: minY, w: maxX - minX + 1, h: maxY - minY + 1 };
  }, [dims, activeColor]);

  /**
   * Even-odd scanline fill of a closed polygon, straight into the mask buffer.
   *
   * Harvested from the retired Konva canvas, which drew polygons but stored
   * them as a `polygon_json` field no backend route ever read. Working in
   * image space also sidesteps the scale bug that version had: it converted
   * vertices to normalized coords and then applied the display scale a second
   * time, so polygons landed offset whenever the viewport was not 1:1.
   */
  const fillPolygon = useCallback((points, erase, dirty) => {
    const w = dims.w, h = dims.h;
    const buf = bufRef.current;
    const data = maskDataRef.current;
    if (!buf || !data || points.length < 3) return null;
    const val = erase ? 0 : 1;
    const { r: cr, g: cg, b: cb } = hexToRgb(activeColor);
    const px = data.data;
    const yMin = Math.max(0, Math.floor(Math.min(...points.map(pt => pt.iy))));
    const yMax = Math.min(h - 1, Math.ceil(Math.max(...points.map(pt => pt.iy))));
    let minX = w, minY = h, maxX = -1, maxY = -1;
    const xs = [];
    for (let y = yMin; y <= yMax; y++) {
      const cy = y + 0.5;
      xs.length = 0;
      for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
        const a = points[i], b = points[j];
        if ((a.iy > cy) === (b.iy > cy)) continue;
        xs.push(a.ix + ((cy - a.iy) / (b.iy - a.iy)) * (b.ix - a.ix));
      }
      xs.sort((m, n) => m - n);
      for (let k = 0; k + 1 < xs.length; k += 2) {
        const x0 = Math.max(0, Math.ceil(xs[k] - 0.5));
        const x1 = Math.min(w - 1, Math.floor(xs[k + 1] - 0.5));
        for (let x = x0; x <= x1; x++) {
          const i = y * w + x;
          if (buf[i] === val) continue;
          if (dirty && !dirty.has(i)) dirty.set(i, buf[i]);
          buf[i] = val;
          const o = i * 4;
          if (val) {
            px[o] = cr; px[o + 1] = cg; px[o + 2] = cb; px[o + 3] = 150;
          } else {
            px[o + 3] = 0;
          }
          if (x < minX) minX = x;
          if (x > maxX) maxX = x;
          if (y < minY) minY = y;
          if (y > maxY) maxY = y;
        }
      }
    }
    if (maxX < 0) return null;
    return { x: minX, y: minY, w: maxX - minX + 1, h: maxY - minY + 1 };
  }, [dims, activeColor]);

  const flushRect = useCallback((rect) => {
    if (!rect) return;
    maskCanvasRef.current
      .getContext('2d')
      .putImageData(maskDataRef.current, 0, 0, rect.x, rect.y, rect.w, rect.h);
  }, []);

  const union = (a, b) => {
    if (!a) return b;
    if (!b) return a;
    const x = Math.min(a.x, b.x), y = Math.min(a.y, b.y);
    return { x, y, w: Math.max(a.x + a.w, b.x + b.w) - x, h: Math.max(a.y + a.h, b.y + b.h) - y };
  };

  /** Stamp along a segment so fast drags leave a line, not dots. */
  const stampSegment = useCallback((x0, y0, x1, y1, radius, erase, dirty) => {
    const dist = Math.hypot(x1 - x0, y1 - y0);
    const step = Math.max(1, radius / 2);
    const n = Math.max(1, Math.ceil(dist / step));
    let rect = null;
    for (let k = 1; k <= n; k++) {
      const t = k / n;
      rect = union(rect, stampDisc(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, radius, erase, dirty));
    }
    return rect;
  }, [stampDisc]);

  // ---------- Undo / redo ----------
  const pushUndo = useCallback((dirty) => {
    if (!dirty || dirty.size === 0) return;
    const indices = new Int32Array(dirty.size);
    const prev = new Uint8Array(dirty.size);
    const next = new Uint8Array(dirty.size);
    const buf = bufRef.current;
    let k = 0;
    for (const [i, before] of dirty) {
      indices[k] = i;
      prev[k] = before;
      next[k] = buf[i];
      k++;
    }
    // Diffs, not snapshots: 20 full copies of a 12-megapixel mask is 240 MB,
    // whereas a stroke's diff is bounded by the area it painted.
    undoRef.current.push({ indices, prev, next });
    if (undoRef.current.length > UNDO_LIMIT) undoRef.current.shift();
    redoRef.current = [];
    reportHistory();
  }, [reportHistory]);

  const applyDiff = useCallback((diff, values) => {
    const buf = bufRef.current;
    const w = dims.w;
    let minX = Infinity, minY = Infinity, maxX = -1, maxY = -1;
    for (let k = 0; k < diff.indices.length; k++) {
      const i = diff.indices[k];
      buf[i] = values[k];
      const x = i % w, y = (i / w) | 0;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
    if (maxX < 0) return;
    // Repaint the mirror over the affected box only.
    const data = maskDataRef.current;
    const px = data.data;
    const { r: cr, g: cg, b: cb } = hexToRgb(activeColor);
    for (let y = minY; y <= maxY; y++) {
      for (let x = minX; x <= maxX; x++) {
        const i = y * w + x;
        const o = i * 4;
        if (buf[i]) {
          px[o] = cr; px[o + 1] = cg; px[o + 2] = cb; px[o + 3] = 150;
        } else {
          px[o + 3] = 0;
        }
      }
    }
    flushRect({ x: minX, y: minY, w: maxX - minX + 1, h: maxY - minY + 1 });
    renderActive();
  }, [dims, activeColor, flushRect, renderActive]);

  const emit = useCallback(() => {
    const buf = bufRef.current;
    if (!buf || !activeMaskId) return;
    if (maskIsEmpty(buf)) {
      onMaskUpdated?.(activeMaskId, null);
      return;
    }
    onMaskUpdated?.(activeMaskId, {
      size: [dims.h, dims.w],
      counts: encodeMask(buf, dims.w, dims.h),
    });
  }, [activeMaskId, dims, onMaskUpdated]);

  useImperativeHandle(ref, () => ({
    undo() {
      const diff = undoRef.current.pop();
      if (!diff) return;
      applyDiff(diff, diff.prev);
      redoRef.current.push(diff);
      reportHistory();
      emit();
    },
    redo() {
      const diff = redoRef.current.pop();
      if (!diff) return;
      applyDiff(diff, diff.next);
      undoRef.current.push(diff);
      reportHistory();
      emit();
    },
    clear() {
      const buf = bufRef.current;
      if (!buf) return;
      const dirty = new Map();
      for (let i = 0; i < buf.length; i++) {
        if (buf[i]) {
          dirty.set(i, 1);
          buf[i] = 0;
        }
      }
      if (dirty.size === 0) return;
      pushUndo(dirty);
      repaintMirror();
      renderActive();
      emit();
    },
    cancelPolygon() {
      if (polyRef.current.length === 0) return false;
      polyRef.current = [];
      renderActive();
      return true;
    },
    resetView() {
      setView({ zoom: 1, panX: 0, panY: 0 });
    },
    zoomBy(factor) {
      setView((v) => ({ ...v, zoom: Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, v.zoom * factor)) }));
    },
  }), [applyDiff, emit, pushUndo, repaintMirror, renderActive, reportHistory]);

  const commitPolygon = useCallback((erase = false) => {
    const poly = polyRef.current;
    polyRef.current = [];
    if (poly.length < 3) {
      renderActive();
      return;
    }
    const dirty = new Map();
    flushRect(fillPolygon(poly, erase, dirty));
    renderActive();
    if (dirty.size === 0) return;
    pushUndo(dirty);
    emit();
  }, [fillPolygon, flushRect, pushUndo, emit, renderActive]);

  // ---------- Pointer handling ----------
  const posOf = (e) => {
    const rect = activeCanvasRef.current.getBoundingClientRect();
    return { cx: e.clientX - rect.left, cy: e.clientY - rect.top };
  };

  const handlePointerDown = (e) => {
    const { cx, cy } = posOf(e);
    // Middle button or space/shift-drag pans; everything else paints.
    if (e.button === 1 || e.shiftKey) {
      panRef.current = { cx, cy, panX: view.panX, panY: view.panY };
      e.preventDefault();
      return;
    }
    if (e.button !== 0) return;
    if (!activeMaskId || !bufRef.current) {
      onMaskSelected?.(null);
      return;
    }
    const { ix, iy } = toImage(cx, cy);

    if (tool === 'polygon') {
      const poly = polyRef.current;
      const r = rectRef.current;
      if (poly.length >= 3) {
        // Snapping is measured on screen, not in image pixels, so the target
        // stays the same physical size however far you are zoomed in.
        const firstCx = r.x + (poly[0].ix / dims.w) * r.w;
        const firstCy = r.y + (poly[0].iy / dims.h) * r.h;
        if (Math.hypot(cx - firstCx, cy - firstCy) <= CLOSE_SNAP_PX) {
          commitPolygon(e.altKey);
          return;
        }
      }
      poly.push({ ix, iy });
      renderActive();
      return;
    }

    activeCanvasRef.current.setPointerCapture?.(e.pointerId);
    const dirty = new Map();
    // Right-drag is a common eraser gesture, but pointer events give us
    // e.buttons instead; alt is the modifier here and matches the tool toggle.
    const erase = tool === 'eraser' || e.altKey;
    strokeRef.current = { dirty, lastIx: ix, lastIy: iy, erase };
    flushRect(stampDisc(ix, iy, brushSize / 2, erase, dirty));
    renderActive();
  };

  const handlePointerMove = (e) => {
    const { cx, cy } = posOf(e);
    cursorRef.current = { cx, cy };

    if (panRef.current) {
      const p = panRef.current;
      setView((v) => ({ ...v, panX: p.panX + (cx - p.cx), panY: p.panY + (cy - p.cy) }));
      return;
    }

    const stroke = strokeRef.current;
    if (!stroke) {
      renderActive(); // cursor or polygon rubber band only
      return;
    }
    const { ix, iy } = toImage(cx, cy);
    flushRect(stampSegment(stroke.lastIx, stroke.lastIy, ix, iy, brushSize / 2, stroke.erase, stroke.dirty));
    stroke.lastIx = ix;
    stroke.lastIy = iy;
    renderActive();
  };

  const endStroke = (e) => {
    if (panRef.current) {
      panRef.current = null;
      return;
    }
    const stroke = strokeRef.current;
    strokeRef.current = null;
    if (!stroke) return;
    activeCanvasRef.current?.releasePointerCapture?.(e.pointerId);
    if (stroke.dirty.size === 0) return;
    pushUndo(stroke.dirty);
    // Encode once per stroke rather than per mousemove: a full column-major
    // walk of a 12-megapixel mask is tens of milliseconds, fine on mouseup and
    // very much not fine at 60 Hz.
    emit();
  };

  const handlePointerLeave = () => {
    cursorRef.current = null;
    renderActive();
  };

  const handleWheel = (e) => {
    e.preventDefault();
    const { cx, cy } = posOf(e);
    setView((v) => {
      const zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, v.zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
      if (zoom === v.zoom) return v;
      // Keep the image point under the cursor fixed while zooming.
      const before = rectOf(v);
      const fx = before.w ? (cx - before.x) / before.w : 0.5;
      const fy = before.h ? (cy - before.y) / before.h : 0.5;
      const after = rectOf({ ...v, zoom, panX: 0, panY: 0 });
      return { zoom, panX: cx - (after.x + fx * after.w), panY: cy - (after.y + fy * after.h) };
    });
  };

  const hasActive = Boolean(activeMaskId);
  const overlayStyle = {
    position: 'absolute', top: 0, left: 0, width: '100%', height: '100%',
  };
  const chip = {
    position: 'absolute', padding: '0.2rem 0.5rem', borderRadius: 4,
    background: 'rgba(20, 24, 33, 0.82)', color: 'var(--text-primary, #e6e9ef)',
    fontSize: '0.72rem', pointerEvents: 'none',
  };

  return (
    <div
      ref={containerRef}
      style={{ width: '100%', height: '100%', position: 'relative', overflow: 'hidden' }}
    >
      <canvas
        ref={imageCanvasRef}
        style={{
          ...overlayStyle,
          opacity: imgLoaded ? 1 : 0,
          filter: `brightness(${brightness}) contrast(${contrast})`,
        }}
      />
      <canvas ref={staticCanvasRef} style={{ ...overlayStyle, pointerEvents: 'none' }} />
      <canvas
        ref={activeCanvasRef}
        style={{
          ...overlayStyle,
          // The brush ring drawn on this canvas IS the cursor; a native one on
          // top of it just misleads about where the stroke lands.
          cursor: !hasActive ? 'default' : tool === 'polygon' ? 'crosshair' : 'none',
          touchAction: 'none',
        }}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={endStroke}
        onPointerCancel={endStroke}
        onPointerLeave={handlePointerLeave}
        onDoubleClick={(e) => { if (tool === 'polygon') { e.preventDefault(); commitPolygon(e.altKey); } }}
        onWheel={handleWheel}
        onContextMenu={(e) => e.preventDefault()}
      />
      {!imgLoaded && (
        <div style={{
          position: 'absolute', inset: 0, display: 'flex',
          alignItems: 'center', justifyContent: 'center',
          color: 'var(--text-muted)', fontSize: '0.85rem',
        }}>
          Loading image...
        </div>
      )}
      {imgLoaded && !hasActive && (
        <div style={{ ...chip, bottom: 12, left: '50%', transform: 'translateX(-50%)' }}>
          Add or select a mask layer to start painting
        </div>
      )}
      {view.zoom > 1 && (
        <div style={{ ...chip, top: 8, right: 8 }}>
          {view.zoom.toFixed(1)}x - shift-drag to pan
        </div>
      )}
      {hasActive && (
        <div style={{ ...chip, top: 8, left: 8, display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
          <span style={{ width: 10, height: 10, borderRadius: 2, background: activeColor }} />
          <span>{classList[activeMask?.classIndex] || activeMask?.label || 'mask'}</span>
          <span style={{ opacity: 0.6 }}>- {tool}</span>
        </div>
      )}
    </div>
  );
});

export default MaskCanvas;
