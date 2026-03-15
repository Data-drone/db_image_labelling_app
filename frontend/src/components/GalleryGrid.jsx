/**
 * Image gallery grid with thumbnails, captions, and click-to-expand.
 * Supports configurable column count (3-6).
 */

import { thumbnailUrl } from '../api/client';

export default function GalleryGrid({
  samples = [],
  columns = 4,
  onSelect,
  selectedId,
}) {
  if (samples.length === 0) {
    return (
      <div
        style={{
          textAlign: 'center',
          padding: '3rem',
          color: 'var(--text-muted)',
        }}
      >
        No samples to display.
      </div>
    );
  }

  return (
    <div className={`gallery-grid cols-${columns}`}>
      {samples.map((sample) => (
        <div
          key={sample.id}
          className="gallery-item"
          onClick={() => onSelect && onSelect(sample)}
          style={{
            borderColor:
              selectedId === sample.id
                ? 'var(--accent-blue)'
                : undefined,
          }}
        >
          <img
            src={thumbnailUrl(sample.id, 400)}
            alt={sample.filename}
            loading="lazy"
          />
          <div className="gallery-caption">
            {sample.filename}
            {sample.tags && sample.tags.length > 0 && (
              <div style={{ marginTop: '0.25rem' }}>
                {sample.tags.map((t) => (
                  <span
                    key={t.id}
                    className="badge badge-blue"
                    style={{ marginRight: '0.25rem', fontSize: '0.65rem' }}
                  >
                    {t.tag}
                  </span>
                ))}
              </div>
            )}
          </div>
          {/* Annotation count badge */}
          {sample.annotations && sample.annotations.length > 0 && (
            <div
              style={{
                position: 'absolute',
                top: 6,
                right: 6,
                background: 'rgba(66, 153, 224, 0.85)',
                color: '#fff',
                fontSize: '0.7rem',
                fontWeight: 700,
                padding: '0.15rem 0.4rem',
                borderRadius: 6,
              }}
            >
              {sample.annotations.length}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
