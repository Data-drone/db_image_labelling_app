/**
 * MaskToolPanel — right-hand panel for segmentation projects.
 *
 * The detection panel is written inline in LabelingView; this one is extracted
 * because segmentation needs materially more controls (tool, brush, undo,
 * per-layer class) and inlining a third branch of that size would make the
 * page unreadable. Styling deliberately mirrors the detection branch.
 */

import { getClassColor } from './BBoxCanvas';
import ImageAdjust from './ImageAdjust';
import { maskArea, countsOf } from '../utils/rle';

const MIN_BRUSH = 2;
const MAX_BRUSH = 200;
const TOOL_KEYS = { brush: 'B', eraser: 'E', polygon: 'P' };

/** Painted share of the image, for a rough "did I actually cover it" check. */
function coverage(layer, width, height) {
  const counts = countsOf(layer.mask_json);
  if (!counts || !width || !height) return null;
  return (maskArea(counts) / (width * height)) * 100;
}

export default function MaskToolPanel({
  classList,
  activeClassIndex,
  onActiveClassChange,
  flashIndex,
  layers,
  activeMaskId,
  onSelectLayer,
  onAddLayer,
  onDeleteLayer,
  onRelabelLayer,
  imageWidth,
  imageHeight,
  tool,
  onToolChange,
  brushSize,
  onBrushSizeChange,
  adjust,
  onAdjustChange,
  canUndo,
  canRedo,
  onUndo,
  onRedo,
  onClearMask,
  newClassName,
  onNewClassNameChange,
  onAddClass,
  addingClass,
  saving,
  onSave,
  onSkip,
  predictions,
  endpointReady,
  predicting,
  onPredict,
  hasDraftAnnotations,
  onAcceptDrafts,
  onClearDrafts,
  children,
}) {
  const painted = layers.filter((l) => countsOf(l.mask_json));
  const sectionTitle = {
    fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.5rem', color: 'var(--text-secondary)',
  };
  const divider = { borderTop: '1px solid var(--border-color)', margin: '0 0 0.75rem' };

  return (
    <>
      {/* Tool + brush */}
      <div style={{ marginBottom: '0.75rem' }}>
        <h4 style={sectionTitle}>Tool</h4>
        <div style={{ display: 'flex', gap: '0.3rem', marginBottom: '0.5rem' }}>
          {['brush', 'eraser', 'polygon'].map((t) => (
            <button
              key={t}
              className="btn-secondary"
              onClick={() => onToolChange(t)}
              style={{
                flex: 1, fontSize: '0.78rem', padding: '0.35rem',
                textTransform: 'capitalize',
                borderColor: tool === t ? 'var(--accent, #4299e0)' : undefined,
                background: tool === t ? 'rgba(66, 153, 224, 0.16)' : undefined,
                fontWeight: tool === t ? 600 : 400,
              }}
            >
              {t} ({TOOL_KEYS[t]})
            </button>
          ))}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', width: 52 }}>Brush</span>
          <input
            type="range"
            min={MIN_BRUSH}
            max={MAX_BRUSH}
            step={1}
            value={brushSize}
            disabled={tool === 'polygon'}
            onChange={(e) => onBrushSizeChange(Number(e.target.value))}
            style={{ flex: 1, accentColor: 'var(--accent, #4299e0)', opacity: tool === 'polygon' ? 0.4 : 1 }}
          />
          {/* Image pixels, not screen pixels: the stroke stays the same physical
              size on the photo whatever the zoom. */}
          <span style={{
            fontSize: '0.68rem', color: 'var(--text-muted)', width: 34,
            textAlign: 'right', fontVariantNumeric: 'tabular-nums',
          }}>
            {brushSize}px
          </span>
        </div>
        {tool === 'polygon' && (
          <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: '0.35rem' }}>
            Click to place vertices; click the first one again or double-click to fill.
            Hold Alt to cut instead. Esc cancels.
          </div>
        )}
        <div style={{ display: 'flex', gap: '0.3rem', marginTop: '0.5rem' }}>
          <button
            className="btn-secondary"
            onClick={onUndo}
            disabled={!canUndo}
            style={{ flex: 1, fontSize: '0.72rem', padding: '0.3rem' }}
          >
            Undo
          </button>
          <button
            className="btn-secondary"
            onClick={onRedo}
            disabled={!canRedo}
            style={{ flex: 1, fontSize: '0.72rem', padding: '0.3rem' }}
          >
            Redo
          </button>
          <button
            className="btn-secondary"
            onClick={onClearMask}
            disabled={!activeMaskId}
            style={{ flex: 1, fontSize: '0.72rem', padding: '0.3rem', color: '#f97316' }}
          >
            Clear
          </button>
        </div>
      </div>

      <div style={{ marginBottom: '0.75rem' }}>
        <ImageAdjust
          brightness={adjust.brightness}
          contrast={adjust.contrast}
          onChange={onAdjustChange}
        />
      </div>

      <div style={divider} />

      {/* Class selector — sets the class for the next new layer */}
      <div style={{ marginBottom: '0.75rem' }}>
        <h4 style={sectionTitle}>Class for new layer</h4>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
          {classList.map((cls, i) => (
            <button
              key={cls}
              className="btn-secondary"
              onClick={() => onActiveClassChange(i)}
              style={{
                textAlign: 'left', fontSize: '0.85rem', padding: '0.45rem 0.7rem',
                display: 'flex', alignItems: 'center', gap: '0.5rem',
                borderLeft: `4px solid ${i === activeClassIndex ? getClassColor(i) : 'transparent'}`,
                background: i === flashIndex ? getClassColor(i) + '60'
                  : i === activeClassIndex ? getClassColor(i) + '20' : undefined,
                transition: 'background 0.15s ease-out',
                fontWeight: i === activeClassIndex ? 600 : 400,
              }}
            >
              <span style={{
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                width: 22, height: 22, borderRadius: 4,
                background: getClassColor(i) + '33', color: getClassColor(i),
                fontSize: '0.75rem', fontWeight: 700, flexShrink: 0,
              }}>
                {i + 1}
              </span>
              {cls}
            </button>
          ))}
        </div>
      </div>

      <div style={{ display: 'flex', gap: '0.3rem', marginBottom: '0.75rem' }}>
        <input
          type="text"
          value={newClassName}
          onChange={(e) => onNewClassNameChange(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); onAddClass(); } }}
          placeholder="New class..."
          style={{
            flex: 1, padding: '0.35rem 0.5rem', background: 'var(--bg-input)',
            color: 'var(--text-primary)', border: '1px solid var(--border-color)',
            borderRadius: 4, fontSize: '0.75rem',
          }}
        />
        <button
          type="button"
          onClick={onAddClass}
          disabled={addingClass || !newClassName.trim()}
          className="btn-secondary"
          style={{ padding: '0.35rem 0.5rem', fontSize: '0.75rem', whiteSpace: 'nowrap' }}
        >
          + Add
        </button>
      </div>

      <div style={divider} />

      {/* Mask layers */}
      <div style={{ flex: 1, overflowY: 'auto', marginBottom: '0.75rem' }}>
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          marginBottom: '0.5rem',
        }}>
          <h4 style={{ ...sectionTitle, marginBottom: 0 }}>Mask layers ({layers.length})</h4>
          <button
            className="btn-secondary"
            onClick={onAddLayer}
            disabled={classList.length === 0}
            style={{ fontSize: '0.7rem', padding: '0.2rem 0.45rem' }}
          >
            + Layer
          </button>
        </div>
        {layers.length === 0 ? (
          <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>
            Add a layer, then paint on the image
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
            {layers.map((layer) => {
              const isActive = String(layer.id) === String(activeMaskId);
              const pct = coverage(layer, imageWidth, imageHeight);
              return (
                <div
                  key={layer.id}
                  onClick={() => onSelectLayer(layer.id)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: '0.4rem',
                    padding: '0.3rem 0.5rem', borderRadius: 4, fontSize: '0.8rem',
                    cursor: 'pointer',
                    background: isActive ? 'var(--bg-hover)' : 'transparent',
                    border: isActive ? '1px solid var(--border-hover)' : '1px solid transparent',
                  }}
                >
                  <span style={{
                    width: 10, height: 10, borderRadius: 2, flexShrink: 0,
                    background: getClassColor(layer.classIndex),
                    outline: layer.isDraft ? '1px dashed var(--text-muted)' : undefined,
                    outlineOffset: 1,
                  }} />
                  <select
                    value={layer.classIndex}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => onRelabelLayer(layer.id, Number(e.target.value))}
                    style={{
                      flex: 1, background: 'transparent', color: 'var(--text-primary)',
                      border: 'none', fontSize: '0.8rem', cursor: 'pointer',
                    }}
                  >
                    {classList.map((cls, i) => (
                      <option key={cls} value={i} style={{ background: 'var(--bg-secondary)' }}>{cls}</option>
                    ))}
                  </select>
                  <span style={{ fontSize: '0.68rem', color: 'var(--text-muted)' }}>
                    {pct == null ? 'empty' : `${pct < 0.1 ? '<0.1' : pct.toFixed(1)}%`}
                  </span>
                  <button
                    onClick={(e) => { e.stopPropagation(); onDeleteLayer(layer.id); }}
                    style={{
                      background: 'none', border: 'none', color: 'var(--text-muted)',
                      cursor: 'pointer', padding: '0 0.2rem', fontSize: '0.75rem',
                    }}
                  >
                    &#x2715;
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div style={divider} />

      {predictions && predictions.length > 0 && (
        <div style={{
          padding: '0.4rem 0.5rem', borderRadius: 4,
          background: 'rgba(167, 139, 250, 0.12)',
          border: '1px solid rgba(167, 139, 250, 0.3)',
          marginBottom: '0.5rem', fontSize: '0.7rem', color: '#a78bfa', fontWeight: 600,
        }}>
          {predictions.length} model prediction{predictions.length !== 1 ? 's' : ''} loaded
        </div>
      )}

      {endpointReady && !predictions && layers.length === 0 && (
        <button
          className="btn-secondary"
          onClick={onPredict}
          disabled={predicting || saving}
          style={{
            width: '100%', fontSize: '0.85rem', marginBottom: '0.5rem',
            borderColor: '#a78bfa', color: '#a78bfa',
          }}
        >
          {predicting ? 'Predicting...' : 'Get Prediction'}
        </button>
      )}

      {hasDraftAnnotations && (
        <div style={{ display: 'flex', gap: '0.35rem', marginBottom: '0.5rem' }}>
          <button
            type="button"
            className="btn-secondary"
            onClick={onAcceptDrafts}
            disabled={saving}
            style={{ flex: 1, fontSize: '0.75rem', padding: '0.4rem' }}
          >
            Accept drafts
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={onClearDrafts}
            disabled={saving}
            style={{ flex: 1, fontSize: '0.75rem', padding: '0.4rem', color: '#f97316' }}
          >
            Clear drafts
          </button>
        </div>
      )}

      <button
        className="btn-primary"
        onClick={onSave}
        disabled={saving || painted.length === 0}
        style={{ width: '100%', fontSize: '0.85rem', marginBottom: '0.5rem' }}
      >
        {saving ? 'Saving...' : `Save & Next (${painted.length})`}
      </button>

      <button
        className="btn-secondary"
        onClick={onSkip}
        disabled={saving}
        style={{ width: '100%', fontSize: '0.85rem', marginBottom: '0.75rem' }}
      >
        Skip (S)
      </button>

      {children}
    </>
  );
}
