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
 * Two invariants worth stating, because both were violated by the first cut and
 * both lose the user's data when they are:
 *
 *   1. A mask this build cannot read is never edited. `bufRef` stays null, the
 *      paint paths become no-ops and the annotation reaches the server exactly
 *      as it arrived. Decoding it leniently and re-encoding on the first stroke
 *      overwrites the original with a guess.
 *   2. A stroke belongs to the buffer and mask id it started on. Navigating or
 *      saving mid-stroke commits it against that identity, never against
 *      whatever happens to be active by the time the pointer comes up.
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
 *   onNeedLayer: () => void — create a layer when the user paints with none active
 *   onHistoryChange: ({canUndo, canRedo}) => void
 *
 * Imperative handle: undo(), redo(), clear(), resetView(), zoomBy(factor),
 * cancelPolygon(), flushStroke()
 */

import {
  useRef, useState, useEffect, useCallback, useImperativeHandle, forwardRef,
} from 'react';
import { getClassColor } from './BBoxCanvas';
import { encodeMask, decodeMask, maskIsEmpty, readMask } from '../utils/rle';

const UNDO_LIMIT = 20;
/**
 * Byte ceiling for the undo stack. A count limit is not a memory limit: one
 * full-image fill is worth several hundred brush strokes, so both apply.
 */
const UNDO_BYTE_BUDGET = 96 * 1024 * 1024;
/** Undo journal granularity, in mask pixels. 128x128 = 16 KB per tile. */
const TILE = 128;
/** Cap the backing-store multiplier so a 3x-DPR phone does not allocate 9x. */
const MAX_DPR = 3;
/** Click within this many *screen* px of the first vertex to close a polygon. */
const CLOSE_SNAP_PX = 10;
const MIN_ZOOM = 1;
const MAX_ZOOM = 16;

/**
 * '#rgb' or '#rrggbb' -> {r,g,b}.
 *
 * Falls back to magenta instead of returning NaN channels: NaN written into an
 * ImageData paints as transparent black, so a palette that ever returns
 * `hsl(...)` would make masks silently invisible rather than obviously wrong.
 */
function hexToRgb(hex) {
  let h = typeof hex === 'string' ? hex.trim() : '';
  if (/^#[0-9a-f]{3}$/i.test(h)) h = `#${h[1]}${h[1]}${h[2]}${h[2]}${h[3]}${h[3]}`;
  if (!/^#[0-9a-f]{6}$/i.test(h)) return { r: 255, g: 0, b: 255 };
  return {
    r: parseInt(h.slice(1, 3), 16),
    g: parseInt(h.slice(3, 5), 16),
    b: parseInt(h.slice(5, 7), 16),
  };
}

/** Full-resolution RGBA canvas for one mask, used as a drawImage source. */
function overlayCanvasFor(buf, width, height, hex, alpha) {
  const cv = document.createElement('canvas');
  cv.width = width;
  cv.height = height;
  const ctx = cv.getContext('2d');
  const data = ctx.createImageData(width, height);
  const { r, g, b } = hexToRgb(hex);
  const px = data.data;
  for (let i = 0; i < buf.length; i++) {
    if (!buf[i]) continue;
    const o = i * 4;
    px[o] = r; px[o + 1] = g; px[o + 2] = b; px[o + 3] = alpha;
  }
  ctx.putImageData(data, 0, 0);
  return cv;
}

/**
 * Size a canvas for the device pixel ratio and return a context whose units
 * are CSS pixels. Without this the nearest-neighbour rationale in renderImage
 * is undone on any HiDPI screen: the browser upscales a low-res backing store
 * and smooths the mask edges we went to trouble to keep crisp.
 */
function prepare(canvas, w, h) {
  const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
  const pw = Math.max(1, Math.round(w * dpr));
  const ph = Math.max(1, Math.round(h * dpr));
  if (canvas.width !== pw || canvas.height !== ph) {
    canvas.width = pw;
    canvas.height = ph;
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return ctx;
}

// ---------- Undo journal ----------
//
// The obvious undo record is a Map of changed pixel index -> previous value,
// and it is the wrong shape at these sizes: a V8 Map entry costs tens of bytes,
// so clearing a 3-megapixel foreground builds a ~150 MB Map and a polygon over
// a 12 MP image takes the tab with it. Whole-buffer snapshots are the other
// extreme -- 20 levels of a 12 MP mask is 240 MB whether you dabbed one pixel
// or filled the frame.
//
// Copy-on-write tiles get both ends. A caller declares the rectangle it is
// about to touch, the overlapping tiles are copied once *before* the paint
// loop, and the inner loop then does no bookkeeping at all: no hash per pixel.
// A brush stroke saves the two or three 16 KB tiles it crosses; a full-image
// fill degrades to exactly one copy of the buffer, minus the tiles it left
// unchanged.

function tileBox(width, height, c, r) {
  const x = c * TILE;
  const y = r * TILE;
  return { x, y, w: Math.min(TILE, width - x), h: Math.min(TILE, height - y) };
}

function readTile(buf, width, height, c, r) {
  const { x, y, w, h } = tileBox(width, height, c, r);
  const out = new Uint8Array(w * h);
  for (let k = 0; k < h; k++) {
    const from = (y + k) * width + x;
    out.set(buf.subarray(from, from + w), k * w);
  }
  return out;
}

function writeTile(buf, width, height, c, r, data) {
  const { x, y, w, h } = tileBox(width, height, c, r);
  for (let k = 0; k < h; k++) {
    buf.set(data.subarray(k * w, k * w + w), (y + k) * width + x);
  }
}

function tilesEqual(a, b) {
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

/** Start journalling edits to `buf`. Holds the buffer, not a ref to it. */
function makeJournal(buf, width, height) {
  const cols = Math.ceil(width / TILE);
  const rows = Math.ceil(height / TILE);
  return {
    buf, width, height, cols, rows,
    saved: new Array(cols * rows).fill(null),
    touched: [],
    /** Snapshot every tile overlapping the inclusive pixel rect. */
    touchRect(x0, y0, x1, y1) {
      const c0 = Math.max(0, Math.floor(x0 / TILE));
      const c1 = Math.min(cols - 1, Math.floor(x1 / TILE));
      const r0 = Math.max(0, Math.floor(y0 / TILE));
      const r1 = Math.min(rows - 1, Math.floor(y1 / TILE));
      for (let r = r0; r <= r1; r++) {
        for (let c = c0; c <= c1; c++) {
          const t = r * cols + c;
          if (this.saved[t]) continue;
          this.saved[t] = readTile(buf, width, height, c, r);
          this.touched.push(t);
        }
      }
    },
  };
}

/**
 * Turn a journal into an undo entry, dropping tiles the edit did not actually
 * change (a disc's bounding box overlaps tiles its circle never reaches).
 * Returns null when nothing changed. `rect` is the union of changed tiles.
 */
function sealJournal(journal) {
  const { buf, width, height, cols } = journal;
  const tiles = [];
  let bytes = 0;
  let minX = width, minY = height, maxX = -1, maxY = -1;
  for (const t of journal.touched) {
    const c = t % cols;
    const r = Math.floor(t / cols);
    const before = journal.saved[t];
    const after = readTile(buf, width, height, c, r);
    if (tilesEqual(before, after)) continue;
    tiles.push({ c, r, before, after });
    bytes += before.length * 2;
    const box = tileBox(width, height, c, r);
    if (box.x < minX) minX = box.x;
    if (box.y < minY) minY = box.y;
    if (box.x + box.w - 1 > maxX) maxX = box.x + box.w - 1;
    if (box.y + box.h - 1 > maxY) maxY = box.y + box.h - 1;
  }
  if (tiles.length === 0) return null;
  return {
    tiles, bytes,
    rect: { x: minX, y: minY, w: maxX - minX + 1, h: maxY - minY + 1 },
  };
}

/** setPointerCapture/release throw when the pointer is already gone. */
function safeCapture(el, pointerId) {
  try { el?.setPointerCapture?.(pointerId); } catch { /* pointer already up */ }
}
function safeRelease(el, pointerId) {
  try { el?.releasePointerCapture?.(pointerId); } catch { /* never captured */ }
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
  onNeedLayer,
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
  // Why the active layer is not editable, when it is not. Null = fine.
  const [maskError, setMaskError] = useState(null);
  const [unreadableOthers, setUnreadableOthers] = useState(0);

  // Active mask working state. Refs, not state: mutated on every mousemove.
  const bufRef = useRef(null);           // Uint8Array(w*h), or null if unreadable
  const maskCanvasRef = useRef(null);    // full-res RGBA mirror of bufRef
  const maskDataRef = useRef(null);      // its ImageData, kept in sync
  const loadedKeyRef = useRef(null);     // which mask bufRef currently holds
  const strokeRef = useRef(null);        // in-flight stroke, bound to one pointer
  const undoRef = useRef([]);
  const redoRef = useRef([]);
  const cursorRef = useRef(null);        // {cx, cy} in canvas px, or null
  const polyRef = useRef([]);            // in-progress polygon, image-space points
  const staticOverlayRef = useRef(null); // composited non-active masks, full-res
  const overlayCacheRef = useRef(new Map()); // layer id -> rasterised overlay
  const staticKeyRef = useRef(null);     // fingerprint of the current composite
  const panRef = useRef(null);

  const activeMask = masks.find((m) => String(m.id) === String(activeMaskId)) || null;
  const activeColor = getClassColor(activeMask ? activeMask.classIndex : activeClassIndex);

  // Mirrored so the commit path can run from an effect without re-creating
  // itself (and the stroke handlers) every time the parent re-renders.
  const onMaskUpdatedRef = useRef(onMaskUpdated);
  onMaskUpdatedRef.current = onMaskUpdated;

  const reportHistory = useCallback(() => {
    onHistoryChange?.({ canUndo: undoRef.current.length > 0, canRedo: redoRef.current.length > 0 });
  }, [onHistoryChange]);

  // ---------- Image loading ----------
  useEffect(() => {
    setImgLoaded(false);
    // Clear the dimensions too, or every dims-keyed effect runs once against
    // the *previous* image's size: a 12 MP buffer allocated for nothing and a
    // spurious size-mismatch warning, immediately thrown away on load.
    setDims({ w: 0, h: 0 });
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
    if (!imgLoaded || !imageWidth || !imageHeight || !dims.w) return;
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
    if (!container) return undefined;
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

  // ---------- Rendering ----------
  const renderImage = useCallback(() => {
    const canvas = imageCanvasRef.current;
    if (!canvas || !imgLoaded) return;
    const ctx = prepare(canvas, canvasSize.w, canvasSize.h);
    ctx.clearRect(0, 0, canvasSize.w, canvasSize.h);
    // Nearest-neighbour when magnified so mask edges line up with the pixels
    // they actually cover instead of a smoothed approximation of them.
    ctx.imageSmoothingEnabled = view.zoom < 2;
    const r = rectRef.current;
    ctx.drawImage(imgRef.current, r.x, r.y, r.w, r.h);
  }, [imgLoaded, canvasSize, view.zoom]);

  const renderStatic = useCallback(() => {
    const canvas = staticCanvasRef.current;
    if (!canvas) return;
    const ctx = prepare(canvas, canvasSize.w, canvasSize.h);
    ctx.clearRect(0, 0, canvasSize.w, canvasSize.h);
    const src = staticOverlayRef.current;
    if (!src) return;
    ctx.imageSmoothingEnabled = view.zoom < 2;
    const r = rectRef.current;
    ctx.drawImage(src, r.x, r.y, r.w, r.h);
  }, [canvasSize, view.zoom]);

  const renderActive = useCallback(() => {
    const canvas = activeCanvasRef.current;
    if (!canvas) return;
    const ctx = prepare(canvas, canvasSize.w, canvasSize.h);
    ctx.clearRect(0, 0, canvasSize.w, canvasSize.h);
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

  /** Re-derive the mirror from bufRef over one rect and upload just that box. */
  const repaintRect = useCallback((rect) => {
    const cv = maskCanvasRef.current;
    const data = maskDataRef.current;
    const buf = bufRef.current;
    if (!cv || !data || !buf || !rect) return;
    const w = dims.w;
    const { r: cr, g: cg, b: cb } = hexToRgb(activeColor);
    const px = data.data;
    for (let y = rect.y; y < rect.y + rect.h; y++) {
      for (let x = rect.x; x < rect.x + rect.w; x++) {
        const i = y * w + x;
        const o = i * 4;
        if (buf[i]) {
          px[o] = cr; px[o + 1] = cg; px[o + 2] = cb; px[o + 3] = 150;
        } else {
          px[o + 3] = 0;
        }
      }
    }
    cv.getContext('2d').putImageData(data, 0, 0, rect.x, rect.y, rect.w, rect.h);
  }, [dims, activeColor]);

  /** Upload an already-written region of the mirror ImageData. */
  const flushRect = useCallback((rect) => {
    if (!rect || !maskCanvasRef.current || !maskDataRef.current) return;
    maskCanvasRef.current
      .getContext('2d')
      .putImageData(maskDataRef.current, 0, 0, rect.x, rect.y, rect.w, rect.h);
  }, []);

  // ---------- Undo / redo ----------
  const pushUndo = useCallback((entry) => {
    if (!entry) return;
    undoRef.current.push(entry);
    let bytes = 0;
    for (const e of undoRef.current) bytes += e.bytes;
    while (undoRef.current.length > UNDO_LIMIT
           || (undoRef.current.length > 1 && bytes > UNDO_BYTE_BUDGET)) {
      bytes -= undoRef.current.shift().bytes;
    }
    redoRef.current = [];
    reportHistory();
  }, [reportHistory]);

  const applyEntry = useCallback((entry, which) => {
    const buf = bufRef.current;
    if (!buf) return;
    for (const t of entry.tiles) writeTile(buf, dims.w, dims.h, t.c, t.r, t[which]);
    repaintRect(entry.rect);
    renderActive();
  }, [dims, repaintRect, renderActive]);

  const emit = useCallback(() => {
    const buf = bufRef.current;
    if (!buf || !activeMaskId) return;
    if (maskIsEmpty(buf)) {
      onMaskUpdatedRef.current?.(activeMaskId, null);
      return;
    }
    onMaskUpdatedRef.current?.(activeMaskId, {
      size: [dims.h, dims.w],
      counts: encodeMask(buf, dims.w, dims.h),
    });
  }, [activeMaskId, dims]);

  /**
   * Commit the in-flight stroke, if any, and return `{id, mask}` for it.
   *
   * Everything comes off the stroke itself -- its buffer, its dimensions, its
   * mask id -- so this is safe to call after the active layer has already
   * changed underneath. The return value exists for the save path: the parent's
   * `onMaskUpdated` state change is not visible inside the handler that
   * triggered the save, so it needs the finalized mask in hand.
   */
  const finishStroke = useCallback(() => {
    const stroke = strokeRef.current;
    strokeRef.current = null;
    if (!stroke) return null;
    const entry = sealJournal(stroke.journal);
    // History belongs to the layer that was active when the stroke began; if
    // that changed under us the entry goes away with the buffer it described.
    if (entry && stroke.key === loadedKeyRef.current) pushUndo(entry);
    if (!entry) return null;
    const mask = maskIsEmpty(stroke.buf)
      ? null
      : { size: [stroke.h, stroke.w], counts: encodeMask(stroke.buf, stroke.w, stroke.h) };
    onMaskUpdatedRef.current?.(stroke.maskId, mask);
    return { id: stroke.maskId, mask };
  }, [pushUndo]);

  // ---------- Load the active mask into the working buffer ----------
  // Keyed on mask identity, not contents: re-syncing on every `masks` change
  // would fight the parent's state update after our own onMaskUpdated.
  useEffect(() => {
    if (!dims.w) return;
    const key = `${imageSrc}|${activeMaskId}|${dims.w}x${dims.h}`;
    if (loadedKeyRef.current === key) return;
    loadedKeyRef.current = key;
    // A stroke still in flight belongs to the buffer we are about to drop.
    // Commit it against its own identity before swapping; leaving it in place
    // is how arrow-key navigation mid-stroke used to paint the old stroke's
    // coordinates into the newly loaded mask.
    finishStroke();

    const stored = activeMask?.mask_json;
    let buf = null;
    let error = null;
    if (stored) {
      const read = readMask(stored, dims.h, dims.w);
      if (read.error) error = read.error;
      else buf = decodeMask(read.counts, dims.w, dims.h);
    } else {
      buf = new Uint8Array(dims.w * dims.h);
    }
    setMaskError(error);
    bufRef.current = buf;
    undoRef.current = [];
    redoRef.current = [];
    polyRef.current = [];

    if (!buf) {
      // No buffer means no edit path: stampDisc, fillPolygon and clear() all
      // bail, emit() bails, and the annotation reaches the server byte for byte
      // as it arrived. Decoding it anyway and re-encoding on the first stroke
      // is how a mask this build cannot read gets overwritten with a guess.
      maskCanvasRef.current = null;
      maskDataRef.current = null;
      console.warn(`MaskCanvas: active mask left untouched — ${error}`);
      reportHistory();
      renderActive();
      return;
    }

    const cv = document.createElement('canvas');
    cv.width = dims.w;
    cv.height = dims.h;
    maskCanvasRef.current = cv;
    maskDataRef.current = cv.getContext('2d').createImageData(dims.w, dims.h);
    repaintMirror();
    reportHistory();
    renderActive();
    // repaintMirror/renderActive are stable-by-construction closures over refs
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [imageSrc, activeMaskId, dims, activeMask]);

  // Recolour when the active mask's class changes. Gated on there being an
  // active layer: otherwise every number-key press repaints a blank 12 MP
  // mirror and uploads 48 MB for nothing.
  useEffect(() => {
    if (!bufRef.current || !activeMask) return;
    repaintMirror();
    renderActive();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeColor]);

  // ---------- Non-active mask compositing ----------
  // mask_json objects are compared by identity to decide whether a cached
  // overlay is still good; a WeakMap serial turns that identity into something
  // that fits in a plain string fingerprint.
  const serialRef = useRef({ map: new WeakMap(), next: 1 });
  const serialOf = useCallback((obj) => {
    if (!obj || typeof obj !== 'object') return 0;
    const state = serialRef.current;
    let s = state.map.get(obj);
    if (!s) {
      s = state.next++;
      state.map.set(obj, s);
    }
    return s;
  }, []);

  useEffect(() => {
    if (!dims.w) {
      staticOverlayRef.current = null;
      overlayCacheRef.current.clear();
      staticKeyRef.current = null;
      setUnreadableOthers(0);
      return;
    }
    // Cache is keyed on layer id, not on "is it the active one", so selecting a
    // different layer does not throw away a rasterisation we still need.
    const live = new Set(masks.map((m) => String(m.id)));
    for (const id of Array.from(overlayCacheRef.current.keys())) {
      if (!live.has(id)) overlayCacheRef.current.delete(id);
    }

    const others = masks.filter((m) => String(m.id) !== String(activeMaskId));
    const key = `${dims.w}x${dims.h}|${others
      .map((m) => `${m.id}:${m.classIndex}:${m.isDraft ? 1 : 0}:${serialOf(m.mask_json)}`)
      .join('|')}`;
    // Every stroke produces a new `masks` array. Without this fingerprint the
    // composite was rebuilt on every pointerup -- decode, createImageData,
    // putImageData and a throwaway canvas per inactive layer, all at full
    // resolution, which is ~250 MB of churn with five layers at 12 MP.
    if (staticKeyRef.current === key) return;
    staticKeyRef.current = key;

    let unreadable = 0;
    const layers = [];
    for (const m of others) {
      const id = String(m.id);
      const cached = overlayCacheRef.current.get(id);
      if (cached && cached.maskJson === m.mask_json && cached.classIndex === m.classIndex
          && cached.isDraft === Boolean(m.isDraft) && cached.w === dims.w && cached.h === dims.h) {
        if (cached.canvas) layers.push(cached.canvas);
        if (cached.unreadable) unreadable++;
        continue;
      }
      let canvas = null;
      let bad = false;
      if (m.mask_json) {
        const read = readMask(m.mask_json, dims.h, dims.w);
        if (read.error) {
          bad = true;
        } else {
          // Drafts read as a fainter wash so a model suggestion is visibly
          // provisional, matching the dashed borders BBoxCanvas uses.
          canvas = overlayCanvasFor(
            decodeMask(read.counts, dims.w, dims.h), dims.w, dims.h,
            getClassColor(m.classIndex), m.isDraft ? 70 : 110,
          );
        }
      }
      overlayCacheRef.current.set(id, {
        maskJson: m.mask_json, classIndex: m.classIndex, isDraft: Boolean(m.isDraft),
        w: dims.w, h: dims.h, canvas, unreadable: bad,
      });
      if (canvas) layers.push(canvas);
      if (bad) unreadable++;
    }
    setUnreadableOthers(unreadable);

    if (layers.length === 0) {
      staticOverlayRef.current = null;
    } else {
      const cv = document.createElement('canvas');
      cv.width = dims.w;
      cv.height = dims.h;
      const ctx = cv.getContext('2d');
      for (const layer of layers) ctx.drawImage(layer, 0, 0);
      staticOverlayRef.current = cv;
    }
    renderStatic();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [masks, activeMaskId, dims]);

  // ---------- Painting ----------
  /** Stamp a filled disc into bufRef and its RGBA mirror. Returns a dirty rect. */
  const stampDisc = useCallback((ix, iy, radius, erase, journal) => {
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
    // Journal the whole box up front: one snapshot per 16 KB tile, and then not
    // a single bookkeeping operation inside the loop below.
    if (journal) journal.touchRect(x0, y0, x1, y1);
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
  const fillPolygon = useCallback((points, erase, journal) => {
    const w = dims.w, h = dims.h;
    const buf = bufRef.current;
    const data = maskDataRef.current;
    if (!buf || !data || points.length < 3) return null;
    const val = erase ? 0 : 1;
    const { r: cr, g: cg, b: cb } = hexToRgb(activeColor);
    const px = data.data;
    const yMin = Math.max(0, Math.floor(Math.min(...points.map((pt) => pt.iy))));
    const yMax = Math.min(h - 1, Math.ceil(Math.max(...points.map((pt) => pt.iy))));
    const xMin = Math.max(0, Math.floor(Math.min(...points.map((pt) => pt.ix))));
    const xMax = Math.min(w - 1, Math.ceil(Math.max(...points.map((pt) => pt.ix))));
    if (journal && xMax >= xMin && yMax >= yMin) journal.touchRect(xMin, yMin, xMax, yMax);
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

  const union = (a, b) => {
    if (!a) return b;
    if (!b) return a;
    const x = Math.min(a.x, b.x), y = Math.min(a.y, b.y);
    return { x, y, w: Math.max(a.x + a.w, b.x + b.w) - x, h: Math.max(a.y + a.h, b.y + b.h) - y };
  };

  /** Stamp along a segment so fast drags leave a line, not dots. */
  const stampSegment = useCallback((x0, y0, x1, y1, radius, erase, journal) => {
    const dist = Math.hypot(x1 - x0, y1 - y0);
    const step = Math.max(1, radius / 2);
    const n = Math.max(1, Math.ceil(dist / step));
    let rect = null;
    for (let k = 1; k <= n; k++) {
      const t = k / n;
      rect = union(rect, stampDisc(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, radius, erase, journal));
    }
    return rect;
  }, [stampDisc]);

  const commitInFlightPolygon = useCallback((erase = false) => {
    const poly = polyRef.current;
    if (poly.length === 0) return null;
    polyRef.current = [];
    const buf = bufRef.current;
    if (poly.length < 3 || !buf || !activeMaskId) {
      renderActive();
      return null;
    }
    const journal = makeJournal(buf, dims.w, dims.h);
    flushRect(fillPolygon(poly, erase, journal));
    renderActive();
    const entry = sealJournal(journal);
    if (!entry) return null;
    pushUndo(entry);
    const mask = maskIsEmpty(buf)
      ? null
      : { size: [dims.h, dims.w], counts: encodeMask(buf, dims.w, dims.h) };
    onMaskUpdatedRef.current?.(activeMaskId, mask);
    return { id: activeMaskId, mask };
  }, [dims, fillPolygon, flushRect, pushUndo, renderActive, activeMaskId]);

  const commitPolygon = useCallback((erase = false) => {
    commitInFlightPolygon(erase);
  }, [commitInFlightPolygon]);

  useImperativeHandle(ref, () => ({
    undo() {
      const entry = undoRef.current.pop();
      if (!entry) return;
      applyEntry(entry, 'before');
      redoRef.current.push(entry);
      reportHistory();
      emit();
    },
    redo() {
      const entry = redoRef.current.pop();
      if (!entry) return;
      applyEntry(entry, 'after');
      undoRef.current.push(entry);
      reportHistory();
      emit();
    },
    clear() {
      const buf = bufRef.current;
      if (!buf) return;
      const journal = makeJournal(buf, dims.w, dims.h);
      journal.touchRect(0, 0, dims.w - 1, dims.h - 1);
      buf.fill(0);
      const entry = sealJournal(journal);
      if (!entry) return;
      pushUndo(entry);
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
    flushStroke() {
      return finishStroke();
    },
    flushPending() {
      const stroke = finishStroke();
      return commitInFlightPolygon(false) || stroke;
    },
    resetView() {
      setView({ zoom: 1, panX: 0, panY: 0 });
    },
    zoomBy(factor) {
      setView((v) => ({ ...v, zoom: Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, v.zoom * factor)) }));
    },
  }), [applyEntry, commitInFlightPolygon, dims, emit, finishStroke, pushUndo, repaintMirror, renderActive, reportHistory]);

  // ---------- Pointer handling ----------
  const posOf = (e) => {
    const rect = activeCanvasRef.current.getBoundingClientRect();
    return { cx: e.clientX - rect.left, cy: e.clientY - rect.top };
  };

  const handlePointerDown = (e) => {
    // One pointer drives the canvas at a time. `touchAction: 'none'` means we
    // see every finger, and a second one used to overwrite strokeRef and end
    // the first stroke early.
    if (strokeRef.current || panRef.current) return;
    const { cx, cy } = posOf(e);
    // Middle button or space/shift-drag pans; everything else paints.
    if (e.button === 1 || e.shiftKey) {
      panRef.current = { pointerId: e.pointerId, cx, cy, panX: view.panX, panY: view.panY };
      // Capture, or a drag released outside the canvas never reaches endStroke
      // and the pan stays glued to the pointer when it comes back.
      safeCapture(activeCanvasRef.current, e.pointerId);
      e.preventDefault();
      return;
    }
    if (e.button !== 0) return;
    if (!activeMaskId || !bufRef.current) {
      if (onNeedLayer) onNeedLayer();
      else onMaskSelected?.(null);
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

    safeCapture(activeCanvasRef.current, e.pointerId);
    const buf = bufRef.current;
    // Right-drag is a common eraser gesture, but pointer events give us
    // e.buttons instead; alt is the modifier here and matches the tool toggle.
    const erase = tool === 'eraser' || e.altKey;
    strokeRef.current = {
      pointerId: e.pointerId,
      journal: makeJournal(buf, dims.w, dims.h),
      // The stroke carries its own buffer, size, target and identity so it can
      // still be committed correctly if the active layer changes mid-drag.
      buf, w: dims.w, h: dims.h,
      maskId: activeMaskId,
      key: loadedKeyRef.current,
      lastIx: ix, lastIy: iy, erase,
    };
    flushRect(stampDisc(ix, iy, brushSize / 2, erase, strokeRef.current.journal));
    renderActive();
  };

  const handlePointerMove = (e) => {
    const { cx, cy } = posOf(e);
    cursorRef.current = { cx, cy };

    const pan = panRef.current;
    if (pan) {
      if (pan.pointerId !== e.pointerId) return;
      setView((v) => ({ ...v, panX: pan.panX + (cx - pan.cx), panY: pan.panY + (cy - pan.cy) }));
      return;
    }

    const stroke = strokeRef.current;
    if (!stroke) {
      renderActive(); // cursor or polygon rubber band only
      return;
    }
    if (stroke.pointerId !== e.pointerId) return;
    const { ix, iy } = toImage(cx, cy);
    flushRect(stampSegment(stroke.lastIx, stroke.lastIy, ix, iy, brushSize / 2, stroke.erase, stroke.journal));
    stroke.lastIx = ix;
    stroke.lastIy = iy;
    renderActive();
  };

  const endStroke = (e) => {
    const pan = panRef.current;
    if (pan) {
      if (pan.pointerId !== e.pointerId) return;
      panRef.current = null;
      safeRelease(activeCanvasRef.current, e.pointerId);
      return;
    }
    const stroke = strokeRef.current;
    if (!stroke || stroke.pointerId !== e.pointerId) return;
    safeRelease(activeCanvasRef.current, e.pointerId);
    // Encode once per stroke rather than per mousemove: a full column-major
    // walk of a 12-megapixel mask is tens of milliseconds, fine on mouseup and
    // very much not fine at 60 Hz.
    finishStroke();
  };

  const handlePointerLeave = () => {
    cursorRef.current = null;
    renderActive();
  };

  // React registers wheel/touchstart/touchmove on the root as *passive*, so
  // preventDefault() inside an onWheel prop is ignored and the page scrolls
  // behind the zoom. A native non-passive listener is the only way to stop it.
  useEffect(() => {
    const canvas = activeCanvasRef.current;
    if (!canvas) return undefined;
    const onWheel = (e) => {
      e.preventDefault();
      const box = canvas.getBoundingClientRect();
      const cx = e.clientX - box.left;
      const cy = e.clientY - box.top;
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
    canvas.addEventListener('wheel', onWheel, { passive: false });
    return () => canvas.removeEventListener('wheel', onWheel);
  }, [rectOf]);

  const hasActive = Boolean(activeMaskId);
  const editable = hasActive && !maskError;
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
          cursor: !editable ? 'default' : tool === 'polygon' ? 'crosshair' : 'none',
          touchAction: 'none',
        }}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={endStroke}
        onPointerCancel={endStroke}
        onPointerLeave={handlePointerLeave}
        onDoubleClick={(e) => {
          if (tool !== 'polygon') return;
          e.preventDefault();
          // The two pointerdowns behind a double click already pushed two
          // vertices in the same spot; drop the duplicate before closing.
          const poly = polyRef.current;
          if (poly.length >= 2) {
            const a = poly[poly.length - 1];
            const b = poly[poly.length - 2];
            if (Math.hypot(a.ix - b.ix, a.iy - b.iy) < 1) poly.pop();
          }
          commitPolygon(e.altKey);
        }}
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
          Click the image or + Layer to start painting
        </div>
      )}
      {imgLoaded && maskError && (
        <div style={{
          ...chip, bottom: 12, left: 12, right: 12, textAlign: 'center',
          background: 'rgba(122, 34, 34, 0.92)',
        }}>
          This layer cannot be shown or edited ({maskError}). It is left exactly
          as stored — delete the layer if you want to redraw it.
        </div>
      )}
      {imgLoaded && !maskError && unreadableOthers > 0 && (
        <div style={{
          ...chip, bottom: 12, left: 12, right: 12, textAlign: 'center',
          background: 'rgba(122, 34, 34, 0.92)',
        }}>
          {unreadableOthers} other mask {unreadableOthers === 1 ? 'layer' : 'layers'} cannot be
          shown and {unreadableOthers === 1 ? 'is' : 'are'} left exactly as stored.
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
