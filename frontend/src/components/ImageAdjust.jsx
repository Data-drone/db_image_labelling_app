/**
 * ImageAdjust — brightness/contrast sliders for the labeling canvases.
 *
 * Display-only: nothing here touches the stored image or the annotations. It
 * exists because dark or low-contrast frames are common in the datasets this
 * app gets pointed at, and squinting at them is how you get sloppy masks.
 *
 * Props:
 *   brightness, contrast: number — 1 = unchanged
 *   onChange: ({brightness, contrast}) => void
 */

const MIN = 0.4;
const MAX = 2.0;

export default function ImageAdjust({ brightness, contrast, onChange }) {
  const isDefault = brightness === 1 && contrast === 1;

  const row = (label, value, key) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
      <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', width: 52 }}>{label}</span>
      <input
        type="range"
        min={MIN}
        max={MAX}
        step={0.05}
        value={value}
        onChange={(e) => onChange({ brightness, contrast, [key]: Number(e.target.value) })}
        style={{ flex: 1, accentColor: 'var(--accent, #4299e0)' }}
      />
      <span style={{
        fontSize: '0.68rem', color: 'var(--text-muted)', width: 30,
        textAlign: 'right', fontVariantNumeric: 'tabular-nums',
      }}>
        {Math.round(value * 100)}
      </span>
    </div>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
        <span style={{ fontSize: '0.75rem', fontWeight: 600 }}>Image</span>
        <button
          className="btn btn-sm"
          onClick={() => onChange({ brightness: 1, contrast: 1 })}
          disabled={isDefault}
          style={{ fontSize: '0.68rem', padding: '0.1rem 0.4rem' }}
        >
          Reset
        </button>
      </div>
      {row('Brightness', brightness, 'brightness')}
      {row('Contrast', contrast, 'contrast')}
    </div>
  );
}
