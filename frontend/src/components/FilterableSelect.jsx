/**
 * FilterableSelect — click to open, type to filter, pick from list.
 * Replaces plain <select> for catalog/schema/volume pickers.
 */

import { useState, useRef, useEffect, useMemo } from 'react';

export default function FilterableSelect({
  options = [],
  value = '',
  onChange,
  placeholder = 'Select...',
  disabled = false,
}) {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState('');
  const inputRef = useRef(null);
  const containerRef = useRef(null);

  const MAX_DISPLAY = 50;

  const filtered = useMemo(() => {
    if (!filter) return options;
    const lc = filter.toLowerCase();
    return options.filter((o) => o.toLowerCase().includes(lc));
  }, [options, filter]);

  const displayed = filtered.length > MAX_DISPLAY ? filtered.slice(0, MAX_DISPLAY) : filtered;

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e) => {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setOpen(false);
        setFilter('');
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  // Focus input when opened
  useEffect(() => {
    if (open && inputRef.current) inputRef.current.focus();
  }, [open]);

  const handleSelect = (opt) => {
    onChange(opt);
    setOpen(false);
    setFilter('');
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Escape') {
      setOpen(false);
      setFilter('');
    } else if (e.key === 'Enter' && filtered.length === 1) {
      handleSelect(filtered[0]);
    }
  };

  // Highlight the matching portion of text
  const highlight = (text) => {
    if (!filter) return text;
    const idx = text.toLowerCase().indexOf(filter.toLowerCase());
    if (idx === -1) return text;
    return (
      <>
        {text.slice(0, idx)}
        <span style={{ color: 'var(--accent-blue)', fontWeight: 600 }}>{text.slice(idx, idx + filter.length)}</span>
        {text.slice(idx + filter.length)}
      </>
    );
  };

  return (
    <div ref={containerRef} style={{ position: 'relative' }}>
      {/* Display button / input */}
      {open ? (
        <input
          ref={inputRef}
          type="text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={`Type to filter ${options.length} items...`}
          style={{
            width: '100%',
            padding: '0.5rem 0.75rem',
            background: 'var(--bg-input)',
            color: 'var(--text-primary)',
            border: '1px solid var(--accent-blue)',
            borderRadius: 6,
            fontSize: '0.85rem',
            outline: 'none',
          }}
        />
      ) : (
        <button
          type="button"
          onClick={() => { if (!disabled) setOpen(true); }}
          disabled={disabled}
          style={{
            width: '100%',
            padding: '0.5rem 0.75rem',
            background: 'var(--bg-input)',
            color: value ? 'var(--text-primary)' : 'var(--text-muted)',
            border: '1px solid var(--border-color)',
            borderRadius: 6,
            fontSize: '0.85rem',
            textAlign: 'left',
            cursor: disabled ? 'not-allowed' : 'pointer',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            opacity: disabled ? 0.5 : 1,
          }}
        >
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {value || placeholder}
          </span>
          <span style={{ fontSize: '0.6rem', color: 'var(--text-muted)', marginLeft: '0.5rem', flexShrink: 0 }}>
            &#x25BC;
          </span>
        </button>
      )}

      {/* Dropdown */}
      {open && (
        <div
          style={{
            position: 'absolute',
            top: '100%',
            left: 0,
            right: 0,
            marginTop: 2,
            maxHeight: 260,
            overflowY: 'auto',
            background: 'var(--bg-card)',
            border: '1px solid var(--border-color)',
            borderRadius: 6,
            zIndex: 100,
            boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
          }}
        >
          {/* Count header */}
          <div style={{
            padding: '0.35rem 0.75rem',
            fontSize: '0.7rem',
            color: 'var(--text-muted)',
            borderBottom: '1px solid var(--border-color)',
            background: 'rgba(255,255,255,0.02)',
          }}>
            {filter
              ? `${filtered.length} match${filtered.length !== 1 ? 'es' : ''} of ${options.length}`
              : `${options.length} items — type to filter`}
          </div>

          {filtered.length === 0 ? (
            <div style={{ padding: '0.75rem', fontSize: '0.8rem', color: 'var(--text-muted)', textAlign: 'center' }}>
              No matches for "{filter}"
            </div>
          ) : (
            <>
              {displayed.map((opt) => (
                <button
                  key={opt}
                  type="button"
                  onClick={() => handleSelect(opt)}
                  style={{
                    display: 'block',
                    width: '100%',
                    padding: '0.4rem 0.75rem',
                    background: opt === value ? 'rgba(66, 153, 224, 0.15)' : 'transparent',
                    color: opt === value ? 'var(--accent-blue)' : 'var(--text-primary)',
                    border: 'none',
                    fontSize: '0.85rem',
                    textAlign: 'left',
                    cursor: 'pointer',
                    fontWeight: opt === value ? 600 : 400,
                  }}
                  onMouseEnter={(e) => { e.target.style.background = 'rgba(255,255,255,0.05)'; }}
                  onMouseLeave={(e) => { e.target.style.background = opt === value ? 'rgba(66,153,224,0.15)' : 'transparent'; }}
                >
                  {highlight(opt)}
                </button>
              ))}
              {filtered.length > MAX_DISPLAY && (
                <div style={{ padding: '0.4rem 0.75rem', fontSize: '0.7rem', color: 'var(--text-muted)', borderTop: '1px solid var(--border-color)' }}>
                  Showing {MAX_DISPLAY} of {filtered.length} — keep typing to narrow down
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
