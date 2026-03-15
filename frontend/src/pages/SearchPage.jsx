/**
 * Search page - Find images by filename, label, or tag.
 * Mirrors Streamlit page 4 (Search).
 */

import { useState, useEffect } from 'react';
import { useDataset } from '../contexts/DatasetContext';
import DatasetSelector from '../components/DatasetSelector';
import GalleryGrid from '../components/GalleryGrid';
import { fetchSamples, fetchDatasetStats } from '../api/client';

export default function SearchPage() {
  const { dataset } = useDataset();
  const [stats, setStats] = useState(null);
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);

  // Search state
  const [activeTab, setActiveTab] = useState('filename');
  const [filenameQuery, setFilenameQuery] = useState('');
  const [selectedLabel, setSelectedLabel] = useState('');
  const [selectedTag, setSelectedTag] = useState('');
  const [maxResults, setMaxResults] = useState(24);
  const [columns, setColumns] = useState(4);

  useEffect(() => {
    if (!dataset) return;
    fetchDatasetStats(dataset.id).then(setStats).catch(console.error);
  }, [dataset]);

  const doSearch = async () => {
    if (!dataset) return;
    setLoading(true);
    try {
      const params = { page: 0, page_size: maxResults };
      if (activeTab === 'filename' && filenameQuery) {
        params.search = filenameQuery;
      } else if (activeTab === 'label' && selectedLabel) {
        params.label = selectedLabel;
      } else if (activeTab === 'tag' && selectedTag) {
        params.tag = selectedTag;
      } else {
        setResults([]);
        setLoading(false);
        return;
      }
      const data = await fetchSamples(dataset.id, params);
      setResults(data.items);
    } catch (err) {
      console.error('Search failed:', err);
      setResults([]);
    } finally {
      setLoading(false);
    }
  };

  const tabs = [
    { id: 'filename', label: 'Filename Search' },
    { id: 'label', label: 'Label Search' },
    { id: 'tag', label: 'Tag Search' },
  ];

  return (
    <div>
      <h1 style={{ fontSize: '1.75rem', fontWeight: 700, marginBottom: '0.5rem' }}>
        Search
      </h1>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem', fontSize: '0.9rem' }}>
        Find images by filename, label, or tag.
      </p>

      <div style={{ display: 'flex', gap: '1rem', marginBottom: '1.5rem', alignItems: 'center' }}>
        <DatasetSelector />

        <div style={{ marginLeft: 'auto', display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
          <label style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
            Max results:
          </label>
          <select
            value={maxResults}
            onChange={(e) => setMaxResults(Number(e.target.value))}
            style={{
              background: 'var(--bg-input)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border-color)',
              borderRadius: 6,
              padding: '0.3rem 0.5rem',
              fontSize: '0.8rem',
            }}
          >
            {[12, 24, 48, 96].map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>

          <label style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>Columns:</label>
          <select
            value={columns}
            onChange={(e) => setColumns(Number(e.target.value))}
            style={{
              background: 'var(--bg-input)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border-color)',
              borderRadius: 6,
              padding: '0.3rem 0.5rem',
              fontSize: '0.8rem',
            }}
          >
            {[3, 4, 5, 6].map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </div>
      </div>

      {dataset && (
        <>
          {/* Tabs */}
          <div
            style={{
              display: 'flex',
              gap: '0.25rem',
              borderBottom: '2px solid var(--border-color)',
              marginBottom: '1.5rem',
            }}
          >
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => {
                  setActiveTab(tab.id);
                  setResults(null);
                }}
                style={{
                  padding: '0.5rem 1rem',
                  fontSize: '0.875rem',
                  fontWeight: activeTab === tab.id ? 600 : 400,
                  color: activeTab === tab.id ? 'var(--accent-blue)' : 'var(--text-secondary)',
                  background: 'transparent',
                  border: 'none',
                  borderBottom: activeTab === tab.id ? '2px solid var(--accent-blue)' : '2px solid transparent',
                  cursor: 'pointer',
                  marginBottom: -2,
                }}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {/* Search inputs */}
          <div style={{ marginBottom: '1.5rem' }}>
            {activeTab === 'filename' && (
              <div style={{ display: 'flex', gap: '0.5rem' }}>
                <input
                  type="text"
                  value={filenameQuery}
                  onChange={(e) => setFilenameQuery(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && doSearch()}
                  placeholder="e.g. IMG_001 or .jpg"
                  style={{
                    flex: 1,
                    maxWidth: 400,
                    padding: '0.5rem 0.75rem',
                    background: 'var(--bg-input)',
                    color: 'var(--text-primary)',
                    border: '1px solid var(--border-color)',
                    borderRadius: 8,
                    fontSize: '0.875rem',
                  }}
                />
                <button className="btn-primary" onClick={doSearch}>Search</button>
              </div>
            )}

            {activeTab === 'label' && (
              <div style={{ display: 'flex', gap: '0.5rem' }}>
                <select
                  value={selectedLabel}
                  onChange={(e) => setSelectedLabel(e.target.value)}
                  style={{
                    padding: '0.5rem 0.75rem',
                    background: 'var(--bg-input)',
                    color: 'var(--text-primary)',
                    border: '1px solid var(--border-color)',
                    borderRadius: 8,
                    fontSize: '0.875rem',
                    minWidth: 200,
                  }}
                >
                  <option value="">Select a label...</option>
                  {(stats?.classes || []).map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
                <button className="btn-primary" onClick={doSearch}>Find Samples</button>
              </div>
            )}

            {activeTab === 'tag' && (
              <div style={{ display: 'flex', gap: '0.5rem' }}>
                <select
                  value={selectedTag}
                  onChange={(e) => setSelectedTag(e.target.value)}
                  style={{
                    padding: '0.5rem 0.75rem',
                    background: 'var(--bg-input)',
                    color: 'var(--text-primary)',
                    border: '1px solid var(--border-color)',
                    borderRadius: 8,
                    fontSize: '0.875rem',
                    minWidth: 200,
                  }}
                >
                  <option value="">Select a tag...</option>
                  {(stats?.tags || []).map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
                <button className="btn-primary" onClick={doSearch}>Find Samples</button>
              </div>
            )}
          </div>

          {/* Results */}
          {loading && (
            <div style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-muted)' }}>
              Searching...
            </div>
          )}

          {results !== null && !loading && (
            <div>
              <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '1rem' }}>
                Results ({results.length} matches)
              </h3>
              {results.length === 0 ? (
                <div
                  style={{
                    textAlign: 'center',
                    padding: '2rem',
                    color: 'var(--text-muted)',
                    background: 'var(--bg-card)',
                    borderRadius: 8,
                  }}
                >
                  No matches found.
                </div>
              ) : (
                <GalleryGrid samples={results} columns={columns} />
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
