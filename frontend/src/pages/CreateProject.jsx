/**
 * Create Project page — form to create a new labeling project.
 */

import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  createProject,
  fetchCatalogs,
  fetchSchemas,
  fetchVolumes,
  browseDirectory,
  fetchInferenceDefaults,
} from '../api/client';
import { humanizeApiError } from '../api/errors';
import FilterableSelect from '../components/FilterableSelect';

/** UC paths must be /Volumes/catalog/schema/volume[...]; local paths any non-empty string. */
function isValidSourceVolumePath(path) {
  const p = (path || '').trim();
  if (!p) return false;
  if (p.startsWith('/Volumes/')) {
    const segs = p.split('/').filter(Boolean);
    return segs.length >= 4;
  }
  return true;
}

export default function CreateProject() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const volumeFromBrowser = searchParams.get('volume') || '';

  // Form fields
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [taskType, setTaskType] = useState('detection');
  const [classList, setClassList] = useState([]);
  const [classInput, setClassInput] = useState('');
  const [servingEndpoint, setServingEndpoint] = useState('');
  const [samPrompt, setSamPrompt] = useState('');

  // Volume browser — if arriving from Browse Volumes, split the path into
  // base volume (/Volumes/cat/sch/vol) and any nested subfolder portion.
  const [initialBase, initialSub] = useMemo(() => {
    if (!volumeFromBrowser) return ['/Volumes/', ''];
    const trimmed = volumeFromBrowser.replace(/\/+$/, '');
    if (trimmed.startsWith('/Volumes/')) {
      const segs = trimmed.split('/').filter(Boolean); // ['Volumes','cat','sch','vol', ...]
      if (segs.length > 4) {
        const base = '/' + segs.slice(0, 4).join('/');
        const sub = segs.slice(4).join('/');
        return [base, sub];
      }
    }
    return [trimmed, ''];
  }, [volumeFromBrowser]);

  const [volumeMode, setVolumeMode] = useState('direct');
  const [directPath, setDirectPath] = useState(initialBase);
  const [catalogs, setCatalogs] = useState([]);
  const [schemas, setSchemas] = useState([]);
  const [volumesList, setVolumesList] = useState([]);
  const [catalog, setCatalog] = useState('');
  const [schema, setSchema] = useState('');
  const [volume, setVolume] = useState('');
  const [nestedSubpath, setNestedSubpath] = useState(initialSub);
  const [volumeFolders, setVolumeFolders] = useState([]);
  const [volumeNavLoading, setVolumeNavLoading] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  const [browseResult, setBrowseResult] = useState(null);

  // Submit
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  // Auto-browse when arriving from Browse Volumes with a pre-filled path
  const hasAutoScanned = useRef(false);
  useEffect(() => {
    if (volumeFromBrowser && !hasAutoScanned.current && isValidSourceVolumePath(volumeFromBrowser)) {
      hasAutoScanned.current = true;
      browseDirectory(volumeFromBrowser)
        .then((data) => {
          const imageCount = (data.files || []).filter(f => {
            const ext = f.name.split('.').pop()?.toLowerCase();
            return ['jpg','jpeg','png','gif','webp','bmp','tiff','tif'].includes(ext);
          }).length;
          setBrowseResult({ imageCount, folders: data.folders?.length || 0 });
        })
        .catch(() => {});
    }
  }, [volumeFromBrowser]);

  // Pre-fill serving endpoint from app env
  useEffect(() => {
    let cancelled = false;
    fetchInferenceDefaults()
      .then((data) => {
        if (cancelled) return;
        const d = (data?.default_serving_endpoint || '').trim();
        if (!d) return;
        setServingEndpoint(d);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  // Load catalogs for picker mode
  useEffect(() => {
    if (volumeMode !== 'picker' || catalogs.length > 0) return;
    fetchCatalogs().then(setCatalogs).catch(() => {});
  }, [volumeMode, catalogs.length]);

  useEffect(() => {
    setSchema('');
    setVolume('');
    setSchemas([]);
    setVolumesList([]);
    if (!catalog) return;
    fetchSchemas(catalog).then(setSchemas).catch(() => {});
  }, [catalog]);

  useEffect(() => {
    setVolume('');
    setVolumesList([]);
    if (!catalog || !schema) return;
    fetchVolumes(catalog, schema).then(setVolumesList).catch(() => {});
  }, [catalog, schema]);

  const volumeBasePath = useMemo(() => {
    if (volumeMode === 'picker') {
      if (!catalog || !schema || !volume) return '';
      return `/Volumes/${catalog}/${schema}/${volume}`;
    }
    return directPath.trim().replace(/\/+$/, '');
  }, [volumeMode, catalog, schema, volume, directPath]);

  const prevVolumeBaseRef = useRef(null);
  useEffect(() => {
    const b = volumeBasePath || '';
    if (prevVolumeBaseRef.current !== null && prevVolumeBaseRef.current !== b) {
      setNestedSubpath('');
    }
    prevVolumeBaseRef.current = b;
  }, [volumeBasePath]);

  const sourceVolume = useMemo(() => {
    if (!volumeBasePath) return '';
    if (!nestedSubpath) return volumeBasePath;
    return `${volumeBasePath.replace(/\/+$/, '')}/${nestedSubpath}`;
  }, [volumeBasePath, nestedSubpath]);

  const sourceVolumeReady = useMemo(
    () => Boolean(sourceVolume) && isValidSourceVolumePath(sourceVolume),
    [sourceVolume],
  );

  useEffect(() => {
    setBrowseResult(null);
  }, [sourceVolume]);

  const showVolumeFolderNav = useMemo(() => {
    if (!volumeBasePath) return false;
    if (volumeMode === 'picker') return true;
    return isValidSourceVolumePath(volumeBasePath);
  }, [volumeMode, volumeBasePath]);

  useEffect(() => {
    if (!showVolumeFolderNav) {
      setVolumeFolders([]);
      return;
    }
    let cancelled = false;
    const listPath = nestedSubpath
      ? `${volumeBasePath.replace(/\/+$/, '')}/${nestedSubpath}`
      : volumeBasePath;
    setVolumeNavLoading(true);
    browseDirectory(listPath)
      .then((data) => {
        if (!cancelled) setVolumeFolders(data.folders || []);
      })
      .catch(() => {
        if (!cancelled) setVolumeFolders([]);
      })
      .finally(() => {
        if (!cancelled) setVolumeNavLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [showVolumeFolderNav, volumeBasePath, nestedSubpath]);

  // Browse the selected path
  const handleBrowse = useCallback(async () => {
    if (!sourceVolume) return;
    setBrowsing(true);
    setBrowseResult(null);
    try {
      const data = await browseDirectory(sourceVolume);
      const imageCount = (data.files || []).filter(f => {
        const ext = f.name.split('.').pop()?.toLowerCase();
        return ['jpg','jpeg','png','gif','webp','bmp','tiff','tif'].includes(ext);
      }).length;
      setBrowseResult({ imageCount, folders: data.folders?.length || 0 });
    } catch (e) {
      setBrowseResult({ error: humanizeApiError(e) });
    } finally {
      setBrowsing(false);
    }
  }, [sourceVolume]);

  // Add class to list
  const addClass = () => {
    const trimmed = classInput.trim();
    if (trimmed && !classList.includes(trimmed)) {
      setClassList([...classList, trimmed]);
    }
    setClassInput('');
  };

  const removeClass = (cls) => {
    setClassList(classList.filter((c) => c !== cls));
  };

  const submitBlockers = useMemo(() => {
    const parts = [];
    if (!name.trim()) parts.push('enter a project name');
    if (!sourceVolume) parts.push('choose a source volume (catalog picker or full direct path)');
    else if (!isValidSourceVolumePath(sourceVolume)) {
      parts.push('use a full Unity Catalog path: /Volumes/catalog/schema/volume');
    }
    if (classList.length === 0) parts.push('add at least one class (type a label and click Add or press Enter)');
    return parts;
  }, [name, sourceVolume, classList.length]);

  const canSubmit = submitBlockers.length === 0;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!canSubmit) {
      setError(`Complete the form first: ${submitBlockers.join('; ')}.`);
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      const payload = {
        name: name.trim(),
        description: description.trim(),
        task_type: taskType,
        class_list: classList,
        source_volume: sourceVolume,
      };
      if (servingEndpoint.trim()) {
        payload.serving_endpoint = servingEndpoint.trim();
      }
      const epConfig = { adapter: 'sam31' };
      if (samPrompt.trim()) {
        epConfig.sam_text_prompt = samPrompt.trim();
      }
      payload.endpoint_config = epConfig;
      const project = await createProject(payload);
      navigate(`/projects/${project.id}`);
    } catch (err) {
      setError(humanizeApiError(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ maxWidth: 640, margin: '0 auto' }}>
      <h1 style={{ fontSize: '1.75rem', fontWeight: 700, marginBottom: '0.25rem' }}>
        New Project
      </h1>
      <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '2rem' }}>
        Set up a labeling project by choosing a source volume and defining classes.
      </p>

      <form onSubmit={handleSubmit}>
        {/* Project name */}
        <div style={{ marginBottom: '1.25rem' }}>
          <label style={labelStyle}>Project Name *</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Traffic Signs v1"
            style={inputStyle}
            required
          />
        </div>

        {/* Description */}
        <div style={{ marginBottom: '1.25rem' }}>
          <label style={labelStyle}>Description</label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Optional project description..."
            rows={2}
            style={{ ...inputStyle, resize: 'vertical' }}
          />
        </div>

        {/* Task type */}
        <div style={{ marginBottom: '1.25rem' }}>
          <label style={labelStyle}>Task Type *</label>
          <div style={{ display: 'flex', gap: '0.75rem' }}>
            {['classification', 'detection'].map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setTaskType(t)}
                style={{
                  flex: 1,
                  padding: '0.6rem 1rem',
                  borderRadius: 8,
                  border: '1px solid var(--border-color)',
                  background: taskType === t ? 'rgba(66, 153, 224, 0.15)' : 'var(--bg-card)',
                  color: taskType === t ? 'var(--accent-blue)' : 'var(--text-secondary)',
                  fontWeight: taskType === t ? 600 : 400,
                  cursor: 'pointer',
                  fontSize: '0.85rem',
                  textTransform: 'capitalize',
                }}
              >
                {t}
              </button>
            ))}
          </div>
        </div>

        {/* Source volume */}
        <div style={{ marginBottom: '1.25rem' }}>
          <label style={labelStyle}>Source Volume *</label>

          {/* Mode toggle */}
          <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.75rem' }}>
            {['direct', 'picker'].map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setVolumeMode(m)}
                style={{
                  padding: '0.35rem 0.75rem',
                  borderRadius: 6,
                  border: '1px solid var(--border-color)',
                  background: volumeMode === m ? 'rgba(66, 153, 224, 0.15)' : 'var(--bg-card)',
                  color: volumeMode === m ? 'var(--accent-blue)' : 'var(--text-secondary)',
                  fontWeight: volumeMode === m ? 600 : 400,
                  cursor: 'pointer',
                  fontSize: '0.8rem',
                }}
              >
                {m === 'direct' ? 'Direct Path' : 'Catalog Picker'}
              </button>
            ))}
          </div>

          {volumeMode === 'direct' ? (
            <div>
              <input
                type="text"
                value={directPath}
                onChange={(e) => setDirectPath(e.target.value)}
                placeholder="/Volumes/catalog/schema/volume"
                style={inputStyle}
              />
              <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: '0.35rem' }}>
                Enter the volume mount path (no trailing subfolder required). When the path is valid, use the folder
                navigator below to nest into a subfolder—same as Catalog Picker.
              </div>
            </div>
          ) : (
            <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
              <div style={{ flex: 1, minWidth: 120 }}>
                <FilterableSelect
                  options={catalogs}
                  value={catalog}
                  onChange={setCatalog}
                  placeholder="Catalog..."
                />
              </div>
              <div style={{ flex: 1, minWidth: 120 }}>
                <FilterableSelect
                  options={schemas}
                  value={schema}
                  onChange={setSchema}
                  placeholder="Schema..."
                  disabled={!catalog}
                />
              </div>
              <div style={{ flex: 1, minWidth: 120 }}>
                <FilterableSelect
                  options={volumesList}
                  value={volume}
                  onChange={setVolume}
                  placeholder="Volume..."
                  disabled={!schema}
                />
              </div>
            </div>
          )}

          {showVolumeFolderNav && (
            <div
              style={{
                marginTop: '0.75rem',
                padding: '0.75rem',
                background: 'var(--bg-card)',
                border: '1px solid var(--border-color)',
                borderRadius: 8,
              }}
            >
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>
                Subfolder (optional) — click a folder to nest; project uses the resolved path shown below.
              </div>
              {volumeNavLoading ? (
                <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>Loading folders…</div>
              ) : (
                <>
                  <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.25rem', marginBottom: volumeFolders.length ? '0.65rem' : 0 }}>
                    {['Volume root', ...(nestedSubpath ? nestedSubpath.split('/') : [])].map((crumb, i) => (
                      <span key={`${crumb}-${i}`} style={{ display: 'flex', alignItems: 'center' }}>
                        {i > 0 && <span style={{ color: 'var(--text-muted)', margin: '0 0.2rem' }}>/</span>}
                        <button
                          type="button"
                          onClick={() => {
                            if (i === 0) setNestedSubpath('');
                            else {
                              const parts = nestedSubpath.split('/');
                              setNestedSubpath(parts.slice(0, i).join('/'));
                            }
                          }}
                          style={{
                            background: i === (nestedSubpath ? nestedSubpath.split('/').length : 0) ? 'rgba(66, 153, 224, 0.12)' : 'var(--bg-input)',
                            border: '1px solid var(--border-color)',
                            borderRadius: 6,
                            padding: '0.25rem 0.5rem',
                            fontSize: '0.75rem',
                            color: 'var(--accent-blue)',
                            cursor: 'pointer',
                          }}
                        >
                          {crumb}
                        </button>
                      </span>
                    ))}
                  </div>
                  {volumeFolders.length > 0 ? (
                    <div
                      style={{
                        display: 'grid',
                        gridTemplateColumns: 'repeat(auto-fill, minmax(100px, 1fr))',
                        gap: '0.5rem',
                      }}
                    >
                      {volumeFolders.map((folder) => (
                        <button
                          key={folder.name}
                          type="button"
                          onClick={() =>
                            setNestedSubpath(nestedSubpath ? `${nestedSubpath}/${folder.name}` : folder.name)
                          }
                          style={{
                            background: 'var(--bg-input)',
                            border: '1px solid var(--border-color)',
                            borderRadius: 8,
                            padding: '0.5rem 0.35rem',
                            cursor: 'pointer',
                            textAlign: 'center',
                            fontSize: '0.78rem',
                            color: 'var(--text-primary)',
                          }}
                        >
                          <span style={{ fontSize: '1.1rem', display: 'block', marginBottom: '0.2rem' }}>&#x1F4C2;</span>
                          {folder.name}
                        </button>
                      ))}
                    </div>
                  ) : (
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                      No subfolders in this location. Images must sit directly in this folder (not in deeper nested paths for scanning).
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {/* Browse button */}
          {sourceVolume && (
            <div style={{ marginTop: '0.5rem' }}>
              <button
                type="button"
                onClick={handleBrowse}
                disabled={browsing}
                className="btn-secondary"
                style={{ fontSize: '0.8rem', padding: '0.35rem 0.75rem' }}
              >
                {browsing ? 'Scanning...' : 'Preview Volume'}
              </button>
              {browseResult && !browseResult.error && (
                <span style={{ marginLeft: '0.75rem', fontSize: '0.8rem', color: 'var(--status-success)' }}>
                  {browseResult.imageCount} images found
                  {browseResult.folders > 0 && `, ${browseResult.folders} subfolders`}
                </span>
              )}
              {browseResult?.error && (
                <span style={{ marginLeft: '0.75rem', fontSize: '0.8rem', color: 'var(--status-error)' }}>
                  {browseResult.error}
                </span>
              )}
            </div>
          )}

          {sourceVolume && (
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.35rem' }}>
              {sourceVolume}
            </div>
          )}
        </div>

        {/* Pre-Label with SAM 3.1 */}
        <div
          style={{
            marginBottom: '1.5rem',
            padding: '1.25rem',
            background: 'var(--bg-card)',
            border: '1px solid var(--border-color)',
            borderRadius: 12,
          }}
        >
          <h2 style={{ fontSize: '1.1rem', fontWeight: 700, marginBottom: '0.15rem' }}>
            Pre-Label with SAM 3.1
          </h2>
          <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginBottom: '1.25rem' }}>
            Define classes and an optional text prompt for automatic bounding-box pre-annotation using SAM 3.1.
          </p>

          {/* Classes */}
          <div style={{ marginBottom: '1rem' }}>
            <label style={labelStyle}>Classes * ({classList.length})</label>
            <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem' }}>
              <input
                type="text"
                value={classInput}
                onChange={(e) => setClassInput(e.target.value)}
                placeholder="Type a class name and press Enter"
                style={{ ...inputStyle, flex: 1 }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    addClass();
                  }
                }}
              />
              <button
                type="button"
                onClick={addClass}
                className="btn-secondary"
                style={{ padding: '0.4rem 0.75rem', whiteSpace: 'nowrap' }}
              >
                Add
              </button>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem' }}>
              {classList.map((cls, i) => (
                <span
                  key={cls}
                  className="badge badge-blue"
                  style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem', padding: '0.25rem 0.6rem' }}
                >
                  <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontWeight: 700 }}>{i + 1}</span>
                  {cls}
                  <button
                    type="button"
                    onClick={() => removeClass(cls)}
                    style={{ background: 'none', border: 'none', color: 'inherit', cursor: 'pointer', padding: 0, fontSize: '0.85rem', lineHeight: 1 }}
                  >
                    &#x2715;
                  </button>
                </span>
              ))}
            </div>
          </div>

          {/* SAM Text Prompt */}
          <div style={{ marginBottom: '1rem' }}>
            <label style={labelStyle}>SAM Text Prompt</label>
            <textarea
              value={samPrompt}
              onChange={(e) => setSamPrompt(e.target.value)}
              placeholder={classList.length > 0 ? `Default: "${classList.join('. ')}"` : 'e.g. "car. person. traffic sign"'}
              rows={2}
              style={{ ...inputStyle, resize: 'vertical' }}
            />
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
              Text prompt sent to SAM 3.1 for detection. Leave blank to auto-generate from the class list above (classes joined with ". ").
            </div>
          </div>

          {/* Serving endpoint */}
          <div>
            <label style={labelStyle}>Model Serving Endpoint</label>
            <input
              type="text"
              value={servingEndpoint}
              onChange={(e) => setServingEndpoint(e.target.value)}
              placeholder="e.g. sam31-endpoint"
              style={inputStyle}
            />
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
              Databricks Model Serving endpoint running SAM 3.1. Must be added as an App resource with "Can query" permission.
              {servingEndpoint && <span style={{ color: 'var(--status-success)', marginLeft: '0.5rem' }}>Auto-detected from environment</span>}
            </div>
          </div>
        </div>

        {/* Error */}
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

        {/* Submit */}
        <div style={{ marginTop: '1.5rem' }}>
          <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', alignItems: 'center' }}>
            <button
              type="submit"
              disabled={submitting || !canSubmit}
              className="btn-primary"
              style={{ padding: '0.6rem 2rem' }}
            >
              {submitting ? 'Creating...' : 'Create Project'}
            </button>
            <button
              type="button"
              onClick={() => navigate('/projects')}
              className="btn-secondary"
              style={{ padding: '0.6rem 1.5rem' }}
            >
              Cancel
            </button>
          </div>
          {!canSubmit && !submitting && (
            <p
              style={{
                marginTop: '0.65rem',
                fontSize: '0.8rem',
                color: 'var(--text-muted)',
                lineHeight: 1.45,
              }}
            >
              <strong style={{ color: 'var(--text-secondary)' }}>Create is disabled until:</strong>{' '}
              {submitBlockers.join(' · ')}.
            </p>
          )}
        </div>
      </form>
    </div>
  );
}

const labelStyle = {
  display: 'block',
  fontSize: '0.8rem',
  fontWeight: 500,
  color: 'var(--text-secondary)',
  marginBottom: '0.35rem',
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
