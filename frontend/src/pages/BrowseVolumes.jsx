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
  fetchTables,
  fetchTablePreview,
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
  const [tablesList, setTablesList] = useState([]);
  const [catalog, setCatalog] = useState('');
  const [schema, setSchema] = useState('');
  const [volume, setVolume] = useState('');
  const [tableName, setTableName] = useState('');
  const [pathColumn, setPathColumn] = useState('image_path');
  const [sourceFilter, setSourceFilter] = useState('');
  const [tablePreview, setTablePreview] = useState(null);
  const [tableLoading, setTableLoading] = useState(false);
  const [warehouseConfigured, setWarehouseConfigured] = useState(true);
  const [catalogsLoading, setCatalogsLoading] = useState(false);

  // Direct path input — default populated from DEMO_VOLUME_PATH env var
  const [directPath, setDirectPath] = useState('');

  useEffect(() => {
    fetchAppConfig()
      .then((cfg) => {
        if (cfg.demo_volume_path) setDirectPath(cfg.demo_volume_path);
        setWarehouseConfigured(Boolean(cfg.sql_warehouse_configured));
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
  const [hasBrowsed, setHasBrowsed] = useState(false);

  // Load catalogs when picker or table mode is activated
  useEffect(() => {
    if (mode !== 'picker' && mode !== 'table') return;
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
    setTableName('');
    setSchemas([]);
    setVolumesList([]);
    setTablesList([]);
    if (!catalog) return;
    fetchSchemas(catalog)
      .then(setSchemas)
      .catch((e) => setError('Could not load schemas: ' + humanizeApiError(e)));
  }, [catalog]);

  // Load volumes or tables when schema changes
  useEffect(() => {
    setVolume('');
    setTableName('');
    setVolumesList([]);
    setTablesList([]);
    setTablePreview(null);
    if (!catalog || !schema) return;
    if (mode === 'table') {
      fetchTables(catalog, schema)
        .then(setTablesList)
        .catch((e) => setError('Could not load tables: ' + humanizeApiError(e)));
    } else if (mode === 'picker') {
      fetchVolumes(catalog, schema)
        .then(setVolumesList)
        .catch((e) => setError('Could not load volumes: ' + humanizeApiError(e)));
    }
  }, [catalog, schema, mode]);

  // Reset subpath when volume or mode changes
  useEffect(() => {
    setSubpath('');
    setFolders([]);
    setFiles([]);
    setTotalFiles(0);
    setFilePage(0);
    setHasBrowsed(false);
  }, [catalog, schema, volume, mode, tableName]);

  const tableFqn = (catalog && schema && tableName)
    ? `${catalog}.${schema}.${tableName}`
    : '';

  useEffect(() => {
    if (mode !== 'table' || !tableFqn) {
      setTablePreview(null);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      setTableLoading(true);
      setError('');
      fetchTablePreview(tableFqn, {
        path_column: pathColumn || undefined,
        source_filter: sourceFilter.trim() || undefined,
        limit: 12,
      })
        .then((data) => {
          if (cancelled) return;
          setTablePreview(data);
          const names = (data.columns || []).map((c) => c.name).filter(Boolean);
          if (data.path_column && names.length && !names.includes(pathColumn)) {
            setPathColumn(data.path_column);
          }
        })
        .catch((e) => {
          if (!cancelled) {
            setTablePreview(null);
            setError('Could not preview table: ' + humanizeApiError(e));
          }
        })
        .finally(() => {
          if (!cancelled) setTableLoading(false);
        });
    }, 350);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [mode, tableFqn, pathColumn, sourceFilter]);

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

  useEffect(() => {
    if (mode === 'table') return;
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
        Navigate Unity Catalog Volumes or pick a Delta table of image paths, then create a labeling project.
      </p>

      {/* Mode toggle */}
      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1.5rem', flexWrap: 'wrap' }}>
        {[
          { id: 'direct', label: 'Direct Path' },
          { id: 'picker', label: 'Catalog Picker' },
          { id: 'table', label: 'Delta table' },
        ].map((opt) => (
          <button
            key={opt.id}
            onClick={() => setMode(opt.id)}
            style={{
              padding: '0.5rem 1rem',
              borderRadius: 8,
              border: '1px solid var(--border-color)',
              background: mode === opt.id ? 'rgba(66, 153, 224, 0.15)' : 'var(--bg-card)',
              color: mode === opt.id ? 'var(--accent-blue-light)' : 'var(--text-secondary)',
              fontWeight: mode === opt.id ? 600 : 400,
              cursor: 'pointer',
              fontSize: '0.85rem',
            }}
          >
            {opt.label}
          </button>
        ))}
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

      {/* Delta table picker */}
      {mode === 'table' && (
        <div style={{ marginBottom: '1.5rem' }}>
          {!warehouseConfigured && (
            <div style={{
              fontSize: '0.85rem',
              color: '#e2a03f',
              marginBottom: '0.75rem',
              background: 'var(--bg-card)',
              border: '1px solid var(--border-color)',
              borderRadius: 8,
              padding: '0.75rem 1rem',
            }}>
              Table preview needs a SQL warehouse on the app (<code>SQL_WAREHOUSE_ID</code>).
            </div>
          )}
          <div style={{ display: 'flex', gap: '1rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
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
              <label style={labelStyle}>Table</label>
              <FilterableSelect
                options={tablesList}
                value={tableName}
                onChange={setTableName}
                placeholder="Select table..."
                disabled={!catalog || !schema}
              />
            </div>
          </div>
          <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
            <div style={{ flex: 1, minWidth: 180 }}>
              <label style={labelStyle}>Path column</label>
              <select
                value={pathColumn}
                onChange={(e) => setPathColumn(e.target.value)}
                style={inputStyle}
              >
                {(tablePreview?.columns || []).length > 0
                  ? tablePreview.columns.map((col) => (
                    <option key={col.name} value={col.name}>{col.name}</option>
                  ))
                  : <option value={pathColumn}>{pathColumn}</option>}
              </select>
            </div>
            <div style={{ flex: 2, minWidth: 220 }}>
              <label style={labelStyle}>Optional SQL filter</label>
              <input
                type="text"
                value={sourceFilter}
                onChange={(e) => setSourceFilter(e.target.value)}
                placeholder="e.g. split = 'train'"
                style={inputStyle}
              />
            </div>
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

      {mode !== 'table' && loading && !paging && <Spinner label="Browsing volume..." />}

      {mode === 'table' && tableFqn && (
        <div style={{ marginBottom: '2rem' }}>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '0.75rem',
            marginBottom: '0.75rem',
            flexWrap: 'wrap',
          }}>
            <div>
              <h3 style={{ fontSize: '0.95rem', fontWeight: 600, margin: 0 }}>{tableFqn}</h3>
              <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
                {tableLoading
                  ? 'Loading preview…'
                  : tablePreview?.row_count != null
                    ? `${tablePreview.row_count} rows · path column ${tablePreview.path_column || pathColumn}`
                    : 'Preview columns and sample paths, then create a project.'}
              </div>
            </div>
            <button
              className="btn-primary"
              disabled={!tableFqn || tableLoading}
              onClick={() => {
                const params = new URLSearchParams({
                  source: 'table',
                  table: tableFqn,
                  path_column: pathColumn || tablePreview?.path_column || 'image_path',
                });
                if (sourceFilter.trim()) params.set('filter', sourceFilter.trim());
                navigate(`/projects/new?${params.toString()}`);
              }}
              style={{ padding: '0.5rem 1.25rem', fontSize: '0.85rem' }}
            >
              Create Project
              {tablePreview?.row_count != null ? ` (${tablePreview.row_count} rows)` : ''}
            </button>
          </div>

          {tableLoading && <Spinner label="Previewing table..." />}

          {!tableLoading && tablePreview && (
            <>
              {(tablePreview.columns || []).length > 0 && (
                <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginBottom: '1rem' }}>
                  Columns: {tablePreview.columns.map((c) => c.name).join(', ')}
                </p>
              )}
              {(tablePreview.sample_paths || []).length > 0 ? (
                <div style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))',
                  gap: '0.75rem',
                }}>
                  {tablePreview.sample_paths.map((filePath) => {
                    const name = filePath.split('/').filter(Boolean).pop() || filePath;
                    return (
                      <div
                        key={filePath}
                        style={{
                          background: 'var(--bg-card)',
                          border: '1px solid var(--border-color)',
                          borderRadius: 8,
                          padding: '0.4rem',
                          textAlign: 'center',
                        }}
                      >
                        <img
                          src={browseThumbnailUrl(filePath, 120)}
                          alt={name}
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
                        <div style={{
                          fontSize: '0.65rem',
                          color: 'var(--text-secondary)',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                          width: '100%',
                        }}>
                          {name}
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div style={{
                  textAlign: 'center',
                  padding: '2rem',
                  color: 'var(--text-muted)',
                  background: 'var(--bg-card)',
                  borderRadius: 12,
                  border: '1px solid var(--border-color)',
                }}>
                  No sample paths yet. Check the path column and SQL warehouse, then create a project to sync rows.
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* Browsing results */}
      {mode !== 'table' && currentPath && (folders.length > 0 || files.length > 0) && (
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
      {mode !== 'table' && !loading && currentPath && folders.length === 0 && totalFiles === 0 && !error && (
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

      {mode === 'table' && !tableFqn && !catalogsLoading && (
        <div style={{
          textAlign: 'center',
          padding: '3rem',
          color: 'var(--text-muted)',
          background: 'var(--bg-card)',
          borderRadius: 12,
          border: '1px solid var(--border-color)',
        }}>
          Select a catalog, schema, and Delta table of image paths.
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
