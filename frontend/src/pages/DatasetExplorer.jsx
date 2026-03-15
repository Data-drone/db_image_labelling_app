/**
 * Dataset Explorer page - Gallery view with filters and pagination.
 * Mirrors Streamlit page 2 (Dataset Explorer).
 */

import { useState, useEffect, useCallback } from 'react';
import { useDataset } from '../contexts/DatasetContext';
import DatasetSelector from '../components/DatasetSelector';
import GalleryGrid from '../components/GalleryGrid';
import Pagination from '../components/Pagination';
import { fetchSamples, fetchDatasetStats, imageUrl } from '../api/client';

export default function DatasetExplorer() {
  const { dataset } = useDataset();
  const [samples, setSamples] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(24);
  const [columns, setColumns] = useState(4);
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState(null);

  // Filters
  const [labelFilter, setLabelFilter] = useState('');
  const [tagFilter, setTagFilter] = useState('');
  const [minConfidence, setMinConfidence] = useState(0);
  const [searchTerm, setSearchTerm] = useState('');

  // Detail view
  const [selectedSample, setSelectedSample] = useState(null);

  const loadSamples = useCallback(async () => {
    if (!dataset) return;
    setLoading(true);
    try {
      const params = { page, page_size: pageSize };
      if (labelFilter) params.label = labelFilter;
      if (tagFilter) params.tag = tagFilter;
      if (minConfidence > 0) params.min_confidence = minConfidence;
      if (searchTerm) params.search = searchTerm;

      const data = await fetchSamples(dataset.id, params);
      setSamples(data.items);
      setTotal(data.total);
    } catch (err) {
      console.error('Failed to load samples:', err);
    } finally {
      setLoading(false);
    }
  }, [dataset, page, pageSize, labelFilter, tagFilter, minConfidence, searchTerm]);

  useEffect(() => {
    loadSamples();
  }, [loadSamples]);

  useEffect(() => {
    if (!dataset) return;
    fetchDatasetStats(dataset.id)
      .then(setStats)
      .catch(console.error);
  }, [dataset]);

  // Reset page when filters change
  useEffect(() => {
    setPage(0);
  }, [labelFilter, tagFilter, minConfidence, searchTerm, pageSize]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div>
      <h1 style={{ fontSize: '1.75rem', fontWeight: 700, marginBottom: '0.5rem' }}>
        Dataset Explorer
      </h1>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem', fontSize: '0.9rem' }}>
        Browse, filter, and inspect images with bounding-box overlays.
      </p>

      {/* Dataset selector + controls bar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '1rem',
          marginBottom: '1.5rem',
          flexWrap: 'wrap',
        }}
      >
        <DatasetSelector />

        {dataset && (
          <>
            <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>|</span>
            <span style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
              <strong style={{ color: 'var(--accent-blue)' }}>{total}</strong> samples
              {(labelFilter || tagFilter || minConfidence > 0 || searchTerm) && ' (filtered)'}
            </span>
          </>
        )}
      </div>

      {dataset && (
        <div style={{ display: 'flex', gap: '1.5rem' }}>
          {/* Sidebar filters */}
          <div
            style={{
              width: 220,
              minWidth: 220,
              background: 'var(--bg-card)',
              borderRadius: 12,
              border: '1px solid var(--border-color)',
              padding: '1rem',
              alignSelf: 'flex-start',
            }}
          >
            <h3 style={{ fontSize: '0.9rem', fontWeight: 600, marginBottom: '1rem' }}>
              Filters
            </h3>

            {/* Search */}
            <label style={labelStyle}>Filename search</label>
            <input
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="e.g. IMG_001"
              style={inputStyle}
            />

            {/* Class filter */}
            {stats?.classes?.length > 0 && (
              <>
                <label style={labelStyle}>Class label</label>
                <select
                  value={labelFilter}
                  onChange={(e) => setLabelFilter(e.target.value)}
                  style={inputStyle}
                >
                  <option value="">All classes</option>
                  {stats.classes.map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
              </>
            )}

            {/* Tag filter */}
            {stats?.tags?.length > 0 && (
              <>
                <label style={labelStyle}>Tag</label>
                <select
                  value={tagFilter}
                  onChange={(e) => setTagFilter(e.target.value)}
                  style={inputStyle}
                >
                  <option value="">All tags</option>
                  {stats.tags.map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              </>
            )}

            {/* Confidence slider */}
            <label style={labelStyle}>
              Min confidence: {(minConfidence * 100).toFixed(0)}%
            </label>
            <input
              type="range"
              min="0" max="1" step="0.05"
              value={minConfidence}
              onChange={(e) => setMinConfidence(Number(e.target.value))}
              style={{ width: '100%', accentColor: 'var(--accent-blue)' }}
            />

            <div
              style={{
                borderTop: '1px solid var(--border-color)',
                margin: '1rem 0',
              }}
            />

            <h3 style={{ fontSize: '0.9rem', fontWeight: 600, marginBottom: '0.75rem' }}>
              Gallery Settings
            </h3>

            <label style={labelStyle}>Columns</label>
            <select
              value={columns}
              onChange={(e) => setColumns(Number(e.target.value))}
              style={inputStyle}
            >
              {[3, 4, 5, 6].map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>

            <label style={labelStyle}>Page size</label>
            <select
              value={pageSize}
              onChange={(e) => setPageSize(Number(e.target.value))}
              style={inputStyle}
            >
              {[12, 24, 48, 96].map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </div>

          {/* Main gallery area */}
          <div style={{ flex: 1, minWidth: 0 }}>
            {loading ? (
              <div className={`gallery-grid cols-${columns}`}>
                {Array.from({ length: pageSize }).map((_, i) => (
                  <div key={i} className="skeleton" style={{ aspectRatio: '1', borderRadius: 8 }} />
                ))}
              </div>
            ) : (
              <>
                <GalleryGrid
                  samples={samples}
                  columns={columns}
                  onSelect={setSelectedSample}
                  selectedId={selectedSample?.id}
                />
                <Pagination
                  page={page}
                  totalPages={totalPages}
                  onPageChange={setPage}
                />
              </>
            )}

            {/* Detail panel */}
            {selectedSample && (
              <div
                className="card"
                style={{
                  marginTop: '1.5rem',
                  display: 'flex',
                  gap: '1.5rem',
                  flexWrap: 'wrap',
                }}
              >
                <img
                  src={imageUrl(selectedSample.id)}
                  alt={selectedSample.filename}
                  style={{
                    maxWidth: 500,
                    maxHeight: 400,
                    objectFit: 'contain',
                    borderRadius: 8,
                    background: 'var(--bg-secondary)',
                  }}
                />
                <div style={{ flex: 1, minWidth: 200 }}>
                  <h3 style={{ fontWeight: 600, marginBottom: '0.5rem' }}>
                    {selectedSample.filename}
                  </h3>
                  <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '1rem' }}>
                    {selectedSample.filepath}
                  </p>

                  {selectedSample.tags?.length > 0 && (
                    <div style={{ marginBottom: '0.75rem' }}>
                      <strong style={{ fontSize: '0.8rem' }}>Tags: </strong>
                      {selectedSample.tags.map((t) => (
                        <span key={t.id} className="badge badge-blue" style={{ marginRight: '0.25rem' }}>
                          {t.tag}
                        </span>
                      ))}
                    </div>
                  )}

                  {selectedSample.annotations?.length > 0 && (
                    <div>
                      <strong style={{ fontSize: '0.8rem' }}>
                        Annotations ({selectedSample.annotations.length}):
                      </strong>
                      <ul style={{ listStyle: 'none', paddingLeft: 0, marginTop: '0.5rem' }}>
                        {selectedSample.annotations.map((ann) => (
                          <li
                            key={ann.id}
                            style={{
                              fontSize: '0.8rem',
                              color: 'var(--text-secondary)',
                              padding: '0.25rem 0',
                            }}
                          >
                            [{ann.ann_type}] <strong>{ann.label}</strong>
                            {ann.confidence != null && ` (${(ann.confidence * 100).toFixed(0)}%)`}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}

                  <button
                    className="btn-secondary"
                    onClick={() => setSelectedSample(null)}
                    style={{ marginTop: '1rem' }}
                  >
                    Close
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

const labelStyle = {
  display: 'block',
  fontSize: '0.75rem',
  fontWeight: 500,
  color: 'var(--text-secondary)',
  marginBottom: '0.25rem',
  marginTop: '0.75rem',
};

const inputStyle = {
  width: '100%',
  padding: '0.4rem 0.6rem',
  background: 'var(--bg-input)',
  color: 'var(--text-primary)',
  border: '1px solid var(--border-color)',
  borderRadius: 6,
  fontSize: '0.8rem',
};
