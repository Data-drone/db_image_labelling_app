/**
 * AnnotationCanvas: react-konva based canvas for drawing bounding boxes
 * and polygons on images. Supports undo/redo.
 *
 * Props:
 *   imageUrl      - URL of the image to annotate
 *   mode          - "bbox" | "polygon" | "view"
 *   annotations   - existing annotations to render
 *   classes       - list of class label strings
 *   onAnnotation  - callback when a new annotation is drawn: { type, data }
 *   width/height  - canvas dimensions
 */

import { useState, useRef, useEffect, useCallback } from 'react';
import { Stage, Layer, Rect, Line, Image as KImage, Circle, Text, Group } from 'react-konva';

// Color palette matching the Streamlit app
const PALETTE = [
  '#eb1600', '#00c800', '#0064ff', '#ffa500', '#9400d3',
  '#00ced1', '#ff1493', '#808000', '#008080', '#dc143c',
];

function classColor(label, classes) {
  const idx = classes.indexOf(label);
  return PALETTE[(idx >= 0 ? idx : Math.abs(hashCode(label))) % PALETTE.length];
}

function hashCode(str) {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = ((hash << 5) - hash + str.charCodeAt(i)) | 0;
  }
  return hash;
}

export default function AnnotationCanvas({
  imageUrl,
  mode = 'view',
  annotations = [],
  classes = [],
  currentLabel = 'unknown',
  onAnnotation,
  width = 800,
  height = 600,
}) {
  const [image, setImage] = useState(null);
  const [scale, setScale] = useState(1);
  const [imgW, setImgW] = useState(width);
  const [imgH, setImgH] = useState(height);

  // Bbox drawing state
  const [drawing, setDrawing] = useState(false);
  const [startPos, setStartPos] = useState(null);
  const [currentRect, setCurrentRect] = useState(null);

  // Polygon drawing state
  const [polyPoints, setPolyPoints] = useState([]);

  // Drawn items (for undo)
  const [drawnItems, setDrawnItems] = useState([]);
  const [undoneItems, setUndoneItems] = useState([]);

  const stageRef = useRef(null);

  // Load image
  useEffect(() => {
    if (!imageUrl) return;
    const img = new window.Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => {
      const scaleX = width / img.width;
      const scaleY = height / img.height;
      const s = Math.min(scaleX, scaleY, 1);
      setScale(s);
      setImgW(img.width * s);
      setImgH(img.height * s);
      setImage(img);
    };
    img.src = imageUrl;
  }, [imageUrl, width, height]);

  // Reset drawing state when mode or image changes
  useEffect(() => {
    setDrawing(false);
    setStartPos(null);
    setCurrentRect(null);
    setPolyPoints([]);
    setDrawnItems([]);
    setUndoneItems([]);
  }, [imageUrl, mode]);

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e) => {
      if (e.ctrlKey && e.key === 'z') {
        e.preventDefault();
        undo();
      } else if (e.ctrlKey && (e.key === 'y' || (e.shiftKey && e.key === 'z'))) {
        e.preventDefault();
        redo();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [drawnItems, undoneItems]);

  const undo = useCallback(() => {
    setDrawnItems((prev) => {
      if (prev.length === 0) return prev;
      const last = prev[prev.length - 1];
      setUndoneItems((u) => [...u, last]);
      return prev.slice(0, -1);
    });
  }, []);

  const redo = useCallback(() => {
    setUndoneItems((prev) => {
      if (prev.length === 0) return prev;
      const last = prev[prev.length - 1];
      setDrawnItems((d) => [...d, last]);
      return prev.slice(0, -1);
    });
  }, []);

  const handleMouseDown = (e) => {
    if (mode === 'view') return;
    const pos = e.target.getStage().getPointerPosition();

    if (mode === 'bbox') {
      setDrawing(true);
      setStartPos(pos);
      setCurrentRect(null);
    }
  };

  const handleMouseMove = (e) => {
    if (mode !== 'bbox' || !drawing || !startPos) return;
    const pos = e.target.getStage().getPointerPosition();
    setCurrentRect({
      x: Math.min(startPos.x, pos.x),
      y: Math.min(startPos.y, pos.y),
      width: Math.abs(pos.x - startPos.x),
      height: Math.abs(pos.y - startPos.y),
    });
  };

  const handleMouseUp = () => {
    if (mode !== 'bbox' || !drawing || !currentRect) return;
    setDrawing(false);

    // Minimum size check
    if (currentRect.width < 5 || currentRect.height < 5) {
      setCurrentRect(null);
      return;
    }

    // Normalize to [0, 1] relative to original image
    const norm = {
      x: currentRect.x / imgW / scale,
      y: currentRect.y / imgH / scale,
      w: currentRect.width / imgW / scale,
      h: currentRect.height / imgH / scale,
    };

    const item = {
      type: 'detection',
      label: currentLabel,
      bbox: norm,
      displayRect: { ...currentRect },
    };

    setDrawnItems((prev) => [...prev, item]);
    setUndoneItems([]);
    setCurrentRect(null);

    if (onAnnotation) {
      onAnnotation(item);
    }
  };

  const handleClick = (e) => {
    if (mode !== 'polygon') return;
    const pos = e.target.getStage().getPointerPosition();

    // Check if closing the polygon (click near first point)
    if (polyPoints.length >= 3) {
      const first = polyPoints[0];
      const dist = Math.sqrt((pos.x - first.x) ** 2 + (pos.y - first.y) ** 2);
      if (dist < 15) {
        // Close polygon
        const normPoints = polyPoints.map((p) => [
          p.x / imgW / scale,
          p.y / imgH / scale,
        ]);

        const item = {
          type: 'segmentation',
          label: currentLabel,
          polygon: normPoints,
          displayPoints: [...polyPoints],
        };

        setDrawnItems((prev) => [...prev, item]);
        setUndoneItems([]);
        setPolyPoints([]);

        if (onAnnotation) {
          onAnnotation(item);
        }
        return;
      }
    }

    setPolyPoints((prev) => [...prev, pos]);
  };

  const handleDblClick = () => {
    if (mode !== 'polygon' || polyPoints.length < 3) return;

    const normPoints = polyPoints.map((p) => [
      p.x / imgW / scale,
      p.y / imgH / scale,
    ]);

    const item = {
      type: 'segmentation',
      label: currentLabel,
      polygon: normPoints,
      displayPoints: [...polyPoints],
    };

    setDrawnItems((prev) => [...prev, item]);
    setUndoneItems([]);
    setPolyPoints([]);

    if (onAnnotation) {
      onAnnotation(item);
    }
  };

  // Render existing annotations (from database)
  const renderExistingAnnotations = () => {
    return annotations.map((ann, i) => {
      const color = classColor(ann.label, classes);

      if (ann.ann_type === 'detection' && ann.bbox_json) {
        const bbox = JSON.parse(ann.bbox_json);
        const x = bbox.x * imgW;
        const y = bbox.y * imgH;
        const w = bbox.w * imgW;
        const h = bbox.h * imgH;

        return (
          <Group key={`existing-${i}`}>
            <Rect
              x={x} y={y} width={w} height={h}
              stroke={color} strokeWidth={2}
              fill="transparent"
            />
            <Text
              x={x} y={y - 16}
              text={ann.label}
              fontSize={12} fontStyle="bold"
              fill="#fff"
              padding={2}
            />
          </Group>
        );
      }

      if (ann.ann_type === 'segmentation' && ann.polygon_json) {
        const points = JSON.parse(ann.polygon_json);
        const flatPoints = points.flatMap((p) => [p[0] * imgW, p[1] * imgH]);

        return (
          <Group key={`existing-${i}`}>
            <Line
              points={flatPoints}
              stroke={color} strokeWidth={2}
              fill={color + '33'}
              closed
            />
          </Group>
        );
      }

      return null;
    });
  };

  // Render newly drawn items
  const renderDrawnItems = () => {
    return drawnItems.map((item, i) => {
      const color = classColor(item.label, classes);

      if (item.type === 'detection') {
        const r = item.displayRect;
        return (
          <Group key={`drawn-${i}`}>
            <Rect
              x={r.x} y={r.y} width={r.width} height={r.height}
              stroke={color} strokeWidth={2}
              fill={color + '1a'}
              dash={[4, 2]}
            />
            <Text
              x={r.x} y={r.y - 16}
              text={item.label}
              fontSize={12} fontStyle="bold"
              fill="#fff"
              padding={2}
            />
          </Group>
        );
      }

      if (item.type === 'segmentation') {
        const flatPoints = item.displayPoints.flatMap((p) => [p.x, p.y]);
        return (
          <Group key={`drawn-${i}`}>
            <Line
              points={flatPoints}
              stroke={color} strokeWidth={2}
              fill={color + '22'}
              closed
              dash={[4, 2]}
            />
          </Group>
        );
      }

      return null;
    });
  };

  return (
    <div>
      {/* Toolbar */}
      <div
        style={{
          display: 'flex',
          gap: '0.5rem',
          marginBottom: '0.5rem',
          alignItems: 'center',
        }}
      >
        <button
          className="btn-secondary"
          onClick={undo}
          disabled={drawnItems.length === 0}
          style={{ fontSize: '0.8rem', padding: '0.3rem 0.75rem' }}
          title="Undo (Ctrl+Z)"
        >
          Undo
        </button>
        <button
          className="btn-secondary"
          onClick={redo}
          disabled={undoneItems.length === 0}
          style={{ fontSize: '0.8rem', padding: '0.3rem 0.75rem' }}
          title="Redo (Ctrl+Y)"
        >
          Redo
        </button>
        <button
          className="btn-secondary"
          onClick={() => {
            setDrawnItems([]);
            setUndoneItems([]);
            setPolyPoints([]);
          }}
          style={{ fontSize: '0.8rem', padding: '0.3rem 0.75rem' }}
        >
          Clear New
        </button>
        <span
          style={{
            marginLeft: 'auto',
            fontSize: '0.75rem',
            color: 'var(--text-muted)',
          }}
        >
          {mode === 'bbox' && 'Click and drag to draw boxes'}
          {mode === 'polygon' && 'Click to place vertices, double-click to close'}
          {mode === 'view' && 'View only'}
        </span>
      </div>

      {/* Canvas */}
      <div
        style={{
          background: 'var(--bg-secondary)',
          borderRadius: 8,
          border: '1px solid var(--border-color)',
          overflow: 'hidden',
          display: 'inline-block',
        }}
      >
        <Stage
          ref={stageRef}
          width={imgW}
          height={imgH}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onClick={handleClick}
          onDblClick={handleDblClick}
          style={{
            cursor:
              mode === 'bbox'
                ? 'crosshair'
                : mode === 'polygon'
                ? 'crosshair'
                : 'default',
          }}
        >
          <Layer>
            {/* Background image */}
            {image && <KImage image={image} width={imgW} height={imgH} />}

            {/* Existing annotations */}
            {renderExistingAnnotations()}

            {/* Newly drawn annotations */}
            {renderDrawnItems()}

            {/* Current bbox being drawn */}
            {currentRect && (
              <Rect
                x={currentRect.x}
                y={currentRect.y}
                width={currentRect.width}
                height={currentRect.height}
                stroke="var(--accent-red, #eb1600)"
                strokeWidth={2}
                fill="rgba(235, 22, 0, 0.12)"
                dash={[6, 3]}
              />
            )}

            {/* Current polygon vertices */}
            {polyPoints.map((pt, i) => (
              <Circle
                key={`polyvert-${i}`}
                x={pt.x}
                y={pt.y}
                radius={4}
                fill="#eb1600"
                stroke="#fff"
                strokeWidth={1}
              />
            ))}
            {polyPoints.length >= 2 && (
              <Line
                points={polyPoints.flatMap((p) => [p.x, p.y])}
                stroke="#eb1600"
                strokeWidth={2}
                dash={[4, 2]}
              />
            )}
          </Layer>
        </Stage>
      </div>
    </div>
  );
}
