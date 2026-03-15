/**
 * Browse Volumes page — navigate Unity Catalog Volumes to find image folders
 * and create datasets from them. Mirrors Streamlit page 1 (Browse Volumes).
 */

import { useState, useEffect, useCallback } from 'react';
import Spinner from '../components/Spinner';
import {
  fetchCatalogs,
  fetchSchemas,
  fetchVolumes,
  browseDirectory,
  createDataset,
} from '../api/client';

export default function BrowseVolumes() {
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

  // Direct path input
  const [directPath, setDirectPath] = useState('/Volumes/brian_gen_ai/cv_explorer/demo_images');

  // Browsing state
  const [subpath, setSubpath] = useState('');
  const [folders, setFolders] = useState([]);
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // Dataset creation
  const [datasetName, setDatasetName] = useState('');
  const [creating, setCreating] = useState(false);
  const [createResult, setCreateResult] = useState(null);

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
        setError('Could not load catalogs: ' + (e.response?.data?.detail || e.message));
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
      .catch((e) => setError('Could not load schemas: ' + (e.response?.data?.detail || e.message)));
  }, [catalog]);

  // Load volumes when schema changes
  useEffect(() => {
    setVolume('');
    setVolumesList([]);
    if (!catalog || !schema) return;
    fetchVolumes(catalog, schema)
      .then(setVolumesList)
      .catch((e) => setError('Could not load volumes: ' + (e.response?.data?.detail || e.message)));
  }, [catalog, schema]);

  // Reset subpath when volume or mode changes
  useEffect(() => {
    setSubpath('');
    setFolders([]);
    setFiles([]);
    setCreateResult(null);
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
  const loadDirectory = useCallback(async () => {
    if (!currentPath) return;
    setLoading(true);
    setError('');
    setCreateResult(null);
    try {
      const data = await browseDirectory(currentPath);
      setFolders(data.folders || []);
      setFiles(data.files || []);
      const pathParts = currentPath.split('/').filter(Boolean);
      setDatasetName(pathParts[pathParts.length - 1] || '');
    } catch (e) {
      setError('Could not browse: ' + (e.response?.data?.detail || e.message));
      setFolders([]);
      setFiles([]);
    } finally {
      setLoading(false);
    }
  }, [currentPath]);

  useEffect(() => {
    if (mode === 'direct') return; // Only auto-browse in picker mode
    loadDirectory();
  }, [loadDirectory, mode]);

  // Breadcrumb navigation
  const breadcrumbs = ['Root', ...(subpath ? subpath.split('/') : [])];

  const navigateToFolder = (folderName) => {
    setSubpath(subpath ? `${subpath}/${folderName}` : folderName);
  };

  const navigateToCrumb = (index) => {
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
    loadDirectory();
  };

  const handleCreateDataset = async () => {
    if (!datasetName.trim()) return;
    setCreating(true);
    setCreateResult(null);
    try {
      const result = await createDataset({
        name: datasetName.trim(),
        description: `Created from ${currentPath}`,
        image_dir: currentPath,
      });
      setCreateResult({
        success: true,
        message: `Dataset "${result.name}" created with ${result.sample_count} images.`,
      });
    } catch (e) {
      const detail = e.response?.data?.detail || e.message;
      setCreateResult({ success: false, message: detail });
    } finally {
      setCreating(false);
    }
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
              <select
                value={catalog}
                onChange={(e) => setCatalog(e.target.value)}
                style={inputStyle}
              >
                <option value="">Select catalog...</option>
                {catalogs.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            )}
          </div>

          <div style={{ flex: 1, minWidth: 180 }}>
            <label style={labelStyle}>Schema</label>
            <select
              value={schema}
              onChange={(e) => setSchema(e.target.value)}
              style={inputStyle}
              disabled={!catalog}
            >
              <option value="">Select schema...</option>
              {schemas.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>

          <div style={{ flex: 1, minWidth: 180 }}>
            <label style={labelStyle}>Volume</label>
            <select
              value={volume}
              onChange={(e) => setVolume(e.target.value)}
              style={inputStyle}
              disabled={!catalog || !schema}
            >
              <option value="">Select volume...</option>
              {volumes.map((v) => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
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

      {loading && <Spinner label="Browsing volume..." />}

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
          {files.length > 0 && (
            <div style={{ marginBottom: '2rem' }}>
              <h3 style={{ fontSize: '0.95rem', fontWeight: 600, marginBottom: '0.75rem' }}>
                Files ({files.length})
              </h3>
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))',
                gap: '0.75rem',
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
                        padding: '0.6rem 0.4rem',
                        textAlign: 'center',
                      }}
                    >
                      <svg
                        width="32"
                        height="32"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke={isImage ? 'var(--accent-blue)' : isJson ? 'var(--status-warning)' : 'var(--text-muted)'}
                        strokeWidth="1.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        style={{ marginBottom: '0.3rem' }}
                      >
                        {isImage
                          ? <path d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
                          : isJson
                            ? <path d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                            : <path d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
                        }
                      </svg>
                      <div style={{
                        fontSize: '0.65rem',
                        color: 'var(--text-secondary)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}>
                        {file.name}
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Create dataset */}
              <div style={{
                borderTop: '1px solid var(--border-color)',
                marginTop: '2rem',
                paddingTop: '1.5rem',
              }}>
                <h3 style={{ fontSize: '0.95rem', fontWeight: 600, marginBottom: '0.75rem' }}>
                  Create Dataset from This Folder
                </h3>
                <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-end', flexWrap: 'wrap' }}>
                  <div style={{ flex: 1, minWidth: 200 }}>
                    <label style={labelStyle}>Dataset name</label>
                    <input
                      type="text"
                      value={datasetName}
                      onChange={(e) => setDatasetName(e.target.value)}
                      placeholder="Enter dataset name"
                      style={inputStyle}
                    />
                  </div>
                  <button
                    onClick={handleCreateDataset}
                    disabled={creating || !datasetName.trim()}
                    className="btn-primary"
                    style={{ padding: '0.5rem 1.5rem', whiteSpace: 'nowrap' }}
                  >
                    {creating ? 'Creating...' : 'Create Dataset'}
                  </button>
                </div>

                {createResult && (
                  <div style={{
                    marginTop: '0.75rem',
                    padding: '0.75rem 1rem',
                    borderRadius: 8,
                    fontSize: '0.85rem',
                    background: createResult.success
                      ? 'rgba(0, 200, 0, 0.1)'
                      : 'rgba(255, 50, 50, 0.1)',
                    border: `1px solid ${createResult.success
                      ? 'rgba(0, 200, 0, 0.3)'
                      : 'rgba(255, 50, 50, 0.3)'}`,
                    color: createResult.success ? '#4caf50' : '#ff6b6b',
                  }}>
                    {createResult.message}
                  </div>
                )}
              </div>
            </div>
          )}
        </>
      )}

      {/* Empty state */}
      {!loading && currentPath && folders.length === 0 && files.length === 0 && !error && (
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
