/**
 * Browse Volumes page — navigate Unity Catalog Volumes to find image folders
 * and create datasets from them. Mirrors Streamlit page 1 (Browse Volumes).
 */

import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import Spinner from '../components/Spinner';
import FilterableSelect from '../components/FilterableSelect';
import { humanizeApiError } from '../api/errors';
import {
  fetchAppConfig,
  fetchCatalogs,
  fetchSchemas,
  fetchVolumes,
  browseDirectory,
  browseThumbnailUrl,
} from '../api/client';

export default function BrowseVolumes() {
  const navigate = useNavigate();
  // Mode: 'picker' or 'direct'
  const [mode, setMode] = useState('direct');

  // UC pickers
  const [catalogs, setCatalogs] = useState([]);
  const [schemas, setSchemas] = useState([]);
  const [volumes, setVolumesList] = useState([]);
  const [catalog, setCatalog] = useState('');
  const [schema, setSchema] = useState('');
  const [volume, setVolume] = useState('');
  const [catalogsLoading, setCatalogsLoading] = useState(false);

  // Direct path input — default populated from DEMO_VOLUME_PATH env var
  const [directPath, setDirectPath] = useState('');

  useEffect(() => {
    fetchAppConfig()
      .then((cfg) => {
        if (cfg.demo_volume_path) setDirectPath(cfg.demo_volume_path);
      })
      .catch(() => {});
  }, []);

  // Browsing state
  const [subpath, setSubpath] = useState('');
  const [folders, setFolders] = useState([]);
  const [files, setFiles] = useState([]);
  const [totalFiles, setTotalFiles] = useState(0);
  const [filePage, setFilePage] = useState(0);
  const filePageSize = 50;
  const [loading, setLoading] = useState(false);
  const [paging, setPaging] = useState(false);
  const [error, setError] = useState('');

  // Load catalogs when picker mode is activated
  useEffect(() => {
    if (mode !== 'picker') return;
    if (catalogs.length > 0) return;
    setCatalogsLoading(true);
    setError('');
    fetchCatalogs()
      .then((data) => {
        setCatalogs(data);
        setCatalogsLoading(false);
      })
      .catch((e) => {
        setError('Could not load catalogs: ' + humanizeApiError(e));
        setCatalogsLoading(false);
      });
  }, [mode, catalogs.length]);

  // Load schemas when catalog changes
  useEffect(() => {
    setSchema('');
    setVolume('');
    setSchemas([]);
    setVolumesList([]);
    if (!catalog) return;
    fetchSchemas(catalog)
      .then(setSchemas)
      .catch((e) => setError('Could not load schemas: ' + humanizeApiError(e)));
  }, [catalog]);

  // Load volumes when schema changes
  useEffect(() => {
    setVolume('');
    setVolumesList([]);
    if (!catalog || !schema) return;
    fetchVolumes(catalog, schema)
      .then(setVolumesList)
      .catch((e) => setError('Could not load volumes: ' + humanizeApiError(e)));
  }, [catalog, schema]);

  // Reset subpath when volume or mode changes
  useEffect(() => {
    setSubpath('');
    setFolders([]);
    setFiles([]);
    setTotalFiles(0);
    setFilePage(0);
    setHasBrowsed(false);
  }, [catalog, schema, volume, mode]);

  // Compute current path based on mode
  let basePath = '';
  if (mode === 'picker' && catalog && schema && volume) {
    basePath = `/Volumes/${catalog}/${schema}/${volume}`;
  } else if (mode === 'direct' && directPath.trim()) {
    basePath = directPath.trim();
  }

  const currentPath = basePath
    ? (subpath ? `${basePath.replace(/\/+$/, '')}/${subpath}` : basePath)
    : '';

  // Browse directory
  const loadDirectory = useCallback(async (pageOverride, { isPageChange = false } = {}) => {
    if (!currentPath) return;
    if (isPageChange) {
      setPaging(true);
    } else {
      setLoading(true);
    }
    setError('');
    const page = pageOverride ?? filePage;
    try {
      const data = await browseDirectory(currentPath, { page, page_size: filePageSize });
      setFolders(data.folders || []);
      setFiles(data.files || []);
      setTotalFiles(data.total_files ?? (data.files || []).length);
    } catch (e) {
      setError('Could not browse: ' + humanizeApiError(e));
      setFolders([]);
      setFiles([]);
      setTotalFiles(0);
    } finally {
      setLoading(false);
      setPaging(false);
    }
  }, [currentPath]);

  const [hasBrowsed, setHasBrowsed] = useState(false);

  useEffect(() => {
    if (mode === 'direct' && !hasBrowsed) return;
    loadDirectory(0);
  }, [currentPath, mode, hasBrowsed]);

  // Breadcrumb navigation
  const breadcrumbs = ['Root', ...(subpath ? subpath.split('/') : [])];

  const navigateToFolder = (folderName) => {
    setFilePage(0);
    setSubpath(subpath ? `${subpath}/${folderName}` : folderName);
  };

  const navigateToCrumb = (index) => {
    setFilePage(0);
    if (index === 0) {
      setSubpath('');
    } else {
      const parts = subpath.split('/');
      setSubpath(parts.slice(0, index).join('/'));
    }
  };

  const handleBrowse = () => {
    setSubpath('');
    setFolders([]);
    setFiles([]);
    setTotalFiles(0);
    setFilePage(0);
    setHasBrowsed(true);
    loadDirectory(0);
  };

  return (
    <div>
      <h1 style={{ fontSize: '1.75rem', fontWeight: 700, marginBottom: '0.5rem' }}>
        Browse Volumes
      </h1>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem', fontSize: '0.9rem' }}>
        Navigate Unity Catalog Volumes to find image folders and create datasets.
      </p>

      {/* Mode toggle */}
      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1.5rem' }}>
        <button
          onClick={() => setMode('direct')}
          style={{
            padding: '0.5rem 1rem',
            borderRadius: 8,
            border: '1px solid var(--border-color)',
            background: mode === 'direct' ? 'rgba(66, 153, 224, 0.15)' : 'var(--bg-card)',
            color: mode === 'direct' ? 'var(--accent-blue-light)' : 'var(--text-secondary)',
            fontWeight: mode === 'direct' ? 600 : 400,
            cursor: 'pointer',
            fontSize: '0.85rem',
          }}
        >
          Direct Path
        </button>
        <button
          onClick={() => setMode('picker')}
          style={{
            padding: '0.5rem 1rem',
            borderRadius: 8,
            border: '1px solid var(--border-color)',
            background: mode === 'picker' ? 'rgba(66, 153, 224, 0.15)' : 'var(--bg-card)',
            color: mode === 'picker' ? 'var(--accent-blue-light)' : 'var(--text-secondary)',
            fontWeight: mode === 'picker' ? 600 : 400,
            cursor: 'pointer',
            fontSize: '0.85rem',
          }}
        >
          Catalog Picker
        </button>
      </div>

      {/* Direct path mode */}
      {mode === 'direct' && (
        <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '1.5rem', alignItems: 'flex-end' }}>
          <div style={{ flex: 1 }}>
            <label style={labelStyle}>Volume Path</label>
            <input
              type="text"
              value={directPath}
              onChange={(e) => setDirectPath(e.target.value)}
              placeholder="/Volumes/catalog/schema/volume"
              style={inputStyle}
              onKeyDown={(e) => { if (e.key === 'Enter') handleBrowse(); }}
            />
          </div>
          <button
            onClick={handleBrowse}
            disabled={!directPath.trim() || loading}
            className="btn-primary"
            style={{ padding: '0.5rem 1.5rem', whiteSpace: 'nowrap' }}
          >
            {loading ? 'Loading...' : 'Browse'}
          </button>
        </div>
      )}

      {/* Catalog picker mode */}
      {mode === 'picker' && (
        <div style={{ display: 'flex', gap: '1rem', marginBottom: '1.5rem', flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 180 }}>
            <label style={labelStyle}>Catalog</label>
            {catalogsLoading ? (
              <div style={{ ...inputStyle, color: 'var(--text-muted)', display: 'flex', alignItems: 'center' }}>
                Loading catalogs...
              </div>
            ) : (
              <FilterableSelect
                options={catalogs}
                value={catalog}
                onChange={setCatalog}
                placeholder="Select catalog..."
              />
            )}
          </div>

          <div style={{ flex: 1, minWidth: 180 }}>
            <label style={labelStyle}>Schema</label>
            <FilterableSelect
              options={schemas}
              value={schema}
              onChange={setSchema}
              placeholder="Select schema..."
              disabled={!catalog}
            />
          </div>

          <div style={{ flex: 1, minWidth: 180 }}>
            <label style={labelStyle}>Volume</label>
            <FilterableSelect
              options={volumes}
              value={volume}
              onChange={setVolume}
              placeholder="Select volume..."
              disabled={!catalog || !schema}
            />
          </div>
        </div>
      )}

      {error && (
        <div style={{
          background: 'rgba(255, 50, 50, 0.1)',
          border: '1px solid rgba(255, 50, 50, 0.3)',
          borderRadius: 8,
          padding: '0.75rem 1rem',
          marginBottom: '1rem',
          color: '#ff6b6b',
          fontSize: '0.85rem',
        }}>
          {error}
        </div>
      )}

      {loading && !paging && <Spinner label="Browsing volume..." />}

      {/* Browsing results */}
      {currentPath && (folders.length > 0 || files.length > 0) && (
        <>
          <div style={{
            fontSize: '0.8rem',
            color: 'var(--text-muted)',
            marginBottom: '0.5rem',
          }}>
            {currentPath}
          </div>

          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '0.25rem',
            marginBottom: '1rem',
            flexWrap: 'wrap',
          }}>
            {breadcrumbs.map((crumb, i) => (
              <span key={i} style={{ display: 'flex', alignItems: 'center' }}>
                {i > 0 && <span style={{ color: 'var(--text-muted)', margin: '0 0.25rem' }}>/</span>}
                <button
                  onClick={() => { navigateToCrumb(i); setTimeout(loadDirectory, 100); }}
                  style={{
                    background: 'var(--bg-card)',
                    border: '1px solid var(--border-color)',
                    borderRadius: 6,
                    padding: '0.3rem 0.6rem',
                    fontSize: '0.8rem',
                    color: 'var(--accent-blue-light)',
                    cursor: 'pointer',
                  }}
                >
                  {crumb}
                </button>
              </span>
            ))}
          </div>

          <div style={{ borderTop: '1px solid var(--border-color)', marginBottom: '1.5rem' }} />

          {/* Folders */}
          {folders.length > 0 && (
            <div style={{ marginBottom: '2rem' }}>
              <h3 style={{ fontSize: '0.95rem', fontWeight: 600, marginBottom: '0.75rem' }}>
                Folders
              </h3>
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))',
                gap: '0.75rem',
              }}>
                {folders.map((folder) => (
                  <button
                    key={folder.name}
                    onClick={() => navigateToFolder(folder.name)}
                    style={{
                      background: 'var(--bg-card)',
                      border: '1px solid var(--border-color)',
                      borderRadius: 10,
                      padding: '1rem',
                      cursor: 'pointer',
                      textAlign: 'center',
                      transition: 'all 0.2s',
                      color: 'var(--text-primary)',
                    }}
                  >
                    <div style={{ fontSize: '1.5rem', marginBottom: '0.5rem' }}>
                      &#x1F4C2;
                    </div>
                    <div style={{ fontSize: '0.85rem', fontWeight: 500 }}>
                      {folder.name}
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Files */}
          {(files.length > 0 || totalFiles > 0) && (
            <div style={{ marginBottom: '2rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.75rem' }}>
                <h3 style={{ fontSize: '0.95rem', fontWeight: 600, margin: 0 }}>
                  Files {totalFiles > filePageSize
                    ? `${filePage * filePageSize + 1}–${Math.min((filePage + 1) * filePageSize, totalFiles)} of ${totalFiles}`
                    : `(${totalFiles})`}
                </h3>
                {totalFiles > 0 && (
                  <button
                    className="btn-primary"
                    onClick={() => navigate(`/projects/new?volume=${encodeURIComponent(currentPath)}`)}
                    style={{ padding: '0.5rem 1.25rem', fontSize: '0.85rem' }}
                  >
                    Create Project ({totalFiles} files)
                  </button>
                )}
              </div>
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))',
                gap: '0.75rem',
                opacity: paging ? 0.5 : 1,
                transition: 'opacity 0.15s',
              }}>
                {files.map((file) => {
                  const ext = file.name.split('.').pop()?.toLowerCase();
                  const isImage = ['jpg','jpeg','png','gif','webp','bmp','tiff'].includes(ext);
                  const isJson = ext === 'json';
                  return (
                    <div
                      key={file.name}
                      style={{
                        background: 'var(--bg-card)',
                        border: '1px solid var(--border-color)',
                        borderRadius: 8,
                        padding: '0.4rem',
                        textAlign: 'center',
                        display: 'flex',
                        flexDirection: 'column',
                        alignItems: 'center',
                      }}
                    >
                      {isImage ? (
                        <img
                          src={browseThumbnailUrl(file.path, 120)}
                          alt={file.name}
                          loading="lazy"
                          style={{
                            width: '100%',
                            height: 80,
                            objectFit: 'cover',
                            borderRadius: 4,
                            marginBottom: '0.3rem',
                            background: 'var(--bg-hover)',
                          }}
                        />
                      ) : (
                        <svg
                          width="32"
                          height="32"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke={isJson ? 'var(--status-warning)' : 'var(--text-muted)'}
                          strokeWidth="1.5"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          style={{ marginBottom: '0.3rem', marginTop: '1rem' }}
                        >
                          {isJson
                            ? <path d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                            : <path d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
                          }
                        </svg>
                      )}
                      <div style={{
                        fontSize: '0.65rem',
                        color: 'var(--text-secondary)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                        width: '100%',
                      }}>
                        {file.name}
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Pagination */}
              {totalFiles > filePageSize && (() => {
                const totalPages = Math.ceil(totalFiles / filePageSize);
                return (
                  <div style={{
                    display: 'flex',
                    justifyContent: 'center',
                    alignItems: 'center',
                    gap: '0.5rem',
                    marginTop: '1rem',
                    fontSize: '0.8rem',
                  }}>
                    <button
                      className="btn-secondary"
                      onClick={() => {
                        const next = Math.max(0, filePage - 1);
                        setFilePage(next);
                        loadDirectory(next, { isPageChange: true });
                      }}
                      disabled={filePage === 0 || paging}
                      style={{ padding: '0.25rem 0.5rem', fontSize: '0.75rem' }}
                    >
                      Prev
                    </button>
                    <span style={{ color: 'var(--text-secondary)' }}>
                      {paging ? '...' : `Page ${filePage + 1} / ${totalPages}`}
                    </span>
                    <button
                      className="btn-secondary"
                      onClick={() => {
                        const next = Math.min(totalPages - 1, filePage + 1);
                        setFilePage(next);
                        loadDirectory(next, { isPageChange: true });
                      }}
                      disabled={filePage >= totalPages - 1 || paging}
                      style={{ padding: '0.25rem 0.5rem', fontSize: '0.75rem' }}
                    >
                      Next
                    </button>
                  </div>
                );
              })()}
            </div>
          )}
        </>
      )}

      {/* Empty state */}
      {!loading && currentPath && folders.length === 0 && totalFiles === 0 && !error && (
        <div style={{
          textAlign: 'center',
          padding: '3rem',
          color: 'var(--text-muted)',
          background: 'var(--bg-card)',
          borderRadius: 12,
          border: '1px solid var(--border-color)',
        }}>
          {mode === 'direct' ? 'Click "Browse" to explore this path.' : 'This folder is empty.'}
        </div>
      )}

      {!currentPath && mode === 'picker' && !catalogsLoading && (
        <div style={{
          textAlign: 'center',
          padding: '3rem',
          color: 'var(--text-muted)',
          background: 'var(--bg-card)',
          borderRadius: 12,
          border: '1px solid var(--border-color)',
        }}>
          Select a catalog, schema, and volume above to start browsing.
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
};

const inputStyle = {
  width: '100%',
  padding: '0.5rem 0.75rem',
  background: 'var(--bg-input)',
  color: 'var(--text-primary)',
  border: '1px solid var(--border-color)',
  borderRadius: 6,
  fontSize: '0.85rem',
};
