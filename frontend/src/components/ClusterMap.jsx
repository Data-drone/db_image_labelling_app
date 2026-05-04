/**
 * ClusterMap — Interactive 2D scatter plot of UMAP-projected embeddings.
 *
 * Renders each sample as a colored dot. Supports coloring by status or label.
 * Shows thumbnail + filename on hover, navigates to labeling view on click.
 */

import { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { sampleThumbnailUrl } from '../api/client';

const STATUS_COLORS = {
  labeled: '#22c55e',
  pre_labeled: '#a78bfa',
  unlabeled: '#94a3b8',
  skipped: '#f59e0b',
};

const LABEL_PALETTE = [
  '#3b82f6', '#ef4444', '#22c55e', '#f59e0b', '#8b5cf6',
  '#ec4899', '#14b8a6', '#f97316', '#06b6d4', '#84cc16',
  '#e879f9', '#fb923c', '#2dd4bf', '#a3e635', '#f472b6',
];

function buildLabelColorMap(points) {
  const unique = new Set();
  for (const p of points) {
    for (const lbl of p.labels) unique.add(lbl);
  }
  const map = {};
  let i = 0;
  for (const lbl of unique) {
    map[lbl] = LABEL_PALETTE[i % LABEL_PALETTE.length];
    i++;
  }
  return map;
}

export default function ClusterMap({ projectId, points, onForceRefresh, refreshing }) {
  const navigate = useNavigate();
  const canvasRef = useRef(null);
  const containerRef = useRef(null);
  const [colorBy, setColorBy] = useState('status');
  const [hovered, setHovered] = useState(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });
  const [dimensions, setDimensions] = useState({ width: 800, height: 500 });

  const labelColorMap = useMemo(() => buildLabelColorMap(points), [points]);
  const allLabels = useMemo(() => Object.keys(labelColorMap).sort(), [labelColorMap]);

  const PADDING = 40;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const obs = new ResizeObserver((entries) => {
      for (const e of entries) {
        const w = Math.max(400, e.contentRect.width);
        setDimensions({ width: w, height: Math.max(350, Math.min(550, w * 0.6)) });
      }
    });
    obs.observe(container);
    return () => obs.disconnect();
  }, []);

  const getColor = useCallback((point) => {
    if (colorBy === 'label') {
      if (point.labels.length > 0) return labelColorMap[point.labels[0]] || '#94a3b8';
      return '#404040';
    }
    return STATUS_COLORS[point.status] || '#94a3b8';
  }, [colorBy, labelColorMap]);

  const scaleX = useCallback((v) => PADDING + v * (dimensions.width - 2 * PADDING), [dimensions.width]);
  const scaleY = useCallback((v) => PADDING + (1 - v) * (dimensions.height - 2 * PADDING), [dimensions.height]);

  // Canvas rendering for performance with large datasets
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;

    canvas.width = dimensions.width * dpr;
    canvas.height = dimensions.height * dpr;
    canvas.style.width = dimensions.width + 'px';
    canvas.style.height = dimensions.height + 'px';
    ctx.scale(dpr, dpr);

    ctx.clearRect(0, 0, dimensions.width, dimensions.height);

    const radius = points.length > 2000 ? 2.5 : points.length > 500 ? 3.5 : 5;

    for (const p of points) {
      const cx = scaleX(p.x);
      const cy = scaleY(p.y);
      const isHovered = hovered && hovered.sample_id === p.sample_id;

      ctx.beginPath();
      ctx.arc(cx, cy, isHovered ? radius + 3 : radius, 0, Math.PI * 2);
      ctx.fillStyle = getColor(p);
      ctx.globalAlpha = isHovered ? 1 : 0.75;
      ctx.fill();

      if (isHovered) {
        ctx.strokeStyle = '#fff';
        ctx.lineWidth = 2;
        ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;
  }, [points, dimensions, hovered, getColor, scaleX, scaleY]);

  const handleMouseMove = useCallback((e) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    const radius = points.length > 2000 ? 6 : points.length > 500 ? 8 : 10;

    let closest = null;
    let closestDist = radius * radius;
    for (const p of points) {
      const cx = scaleX(p.x);
      const cy = scaleY(p.y);
      const dx = mx - cx;
      const dy = my - cy;
      const d = dx * dx + dy * dy;
      if (d < closestDist) {
        closestDist = d;
        closest = p;
      }
    }

    setHovered(closest);
    if (closest) {
      setTooltipPos({ x: e.clientX, y: e.clientY });
    }
  }, [points, scaleX, scaleY]);

  const handleClick = useCallback(() => {
    if (hovered) {
      navigate(`/projects/${projectId}/label?sample=${hovered.sample_id}`);
    }
  }, [hovered, projectId, navigate]);

  return (
    <div ref={containerRef} style={{ width: '100%' }}>
      {/* Controls */}
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        marginBottom: '0.5rem', flexWrap: 'wrap', gap: '0.5rem',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>Color by:</span>
          <button
            className={`btn-secondary`}
            onClick={() => setColorBy('status')}
            style={{
              padding: '0.2rem 0.5rem', fontSize: '0.7rem',
              background: colorBy === 'status' ? 'var(--accent-blue)' : undefined,
              color: colorBy === 'status' ? '#fff' : undefined,
              border: colorBy === 'status' ? '1px solid var(--accent-blue)' : undefined,
            }}
          >
            Status
          </button>
          <button
            className={`btn-secondary`}
            onClick={() => setColorBy('label')}
            style={{
              padding: '0.2rem 0.5rem', fontSize: '0.7rem',
              background: colorBy === 'label' ? 'var(--accent-blue)' : undefined,
              color: colorBy === 'label' ? '#fff' : undefined,
              border: colorBy === 'label' ? '1px solid var(--accent-blue)' : undefined,
            }}
          >
            Class Label
          </button>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
            {points.length.toLocaleString()} samples
          </span>
          {onForceRefresh && (
            <button
              className="btn-secondary"
              onClick={onForceRefresh}
              disabled={refreshing}
              style={{ padding: '0.2rem 0.5rem', fontSize: '0.7rem' }}
            >
              {refreshing ? 'Recomputing...' : 'Recompute'}
            </button>
          )}
        </div>
      </div>

      {/* Canvas scatter plot */}
      <div style={{
        position: 'relative',
        background: 'var(--bg-secondary)',
        borderRadius: 8,
        border: '1px solid var(--border-color)',
        overflow: 'hidden',
      }}>
        <canvas
          ref={canvasRef}
          onMouseMove={handleMouseMove}
          onMouseLeave={() => setHovered(null)}
          onClick={handleClick}
          style={{ cursor: hovered ? 'pointer' : 'default', display: 'block' }}
        />

        {/* Tooltip */}
        {hovered && (
          <div style={{
            position: 'fixed',
            left: tooltipPos.x + 12,
            top: tooltipPos.y - 80,
            background: 'var(--bg-primary)',
            border: '1px solid var(--border-color)',
            borderRadius: 8,
            padding: '0.5rem',
            boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
            zIndex: 1000,
            pointerEvents: 'none',
            maxWidth: 220,
          }}>
            <img
              src={sampleThumbnailUrl(projectId, hovered.sample_id, 120)}
              alt={hovered.filename}
              style={{
                width: 100, height: 100, objectFit: 'cover',
                borderRadius: 4, display: 'block', marginBottom: '0.3rem',
              }}
            />
            <div style={{
              fontSize: '0.7rem', fontWeight: 600,
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            }}>
              {hovered.filename}
            </div>
            <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', marginTop: '0.1rem' }}>
              <span style={{
                display: 'inline-block',
                width: 8, height: 8, borderRadius: '50%',
                background: STATUS_COLORS[hovered.status] || '#94a3b8',
                marginRight: 4, verticalAlign: 'middle',
              }} />
              {hovered.status}
              {hovered.labels.length > 0 && (
                <span style={{ marginLeft: 6 }}>{hovered.labels.join(', ')}</span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Legend */}
      <div style={{
        display: 'flex', flexWrap: 'wrap', gap: '0.5rem',
        marginTop: '0.5rem', fontSize: '0.7rem',
      }}>
        {colorBy === 'status' ? (
          Object.entries(STATUS_COLORS).map(([status, color]) => (
            <div key={status} style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
              <span style={{
                display: 'inline-block', width: 10, height: 10,
                borderRadius: '50%', background: color, flexShrink: 0,
              }} />
              <span style={{ color: 'var(--text-secondary)' }}>
                {status === 'pre_labeled' ? 'Pre-labeled' : status}
              </span>
            </div>
          ))
        ) : (
          <>
            {allLabels.map((lbl) => (
              <div key={lbl} style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                <span style={{
                  display: 'inline-block', width: 10, height: 10,
                  borderRadius: '50%', background: labelColorMap[lbl], flexShrink: 0,
                }} />
                <span style={{ color: 'var(--text-secondary)' }}>{lbl}</span>
              </div>
            ))}
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
              <span style={{
                display: 'inline-block', width: 10, height: 10,
                borderRadius: '50%', background: '#404040', flexShrink: 0,
              }} />
              <span style={{ color: 'var(--text-muted)' }}>unlabeled</span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
