/**
 * Create Project page — form to create a new labeling project.
 */

import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  createProject,
  fetchCatalogs,
  fetchSchemas,
  fetchVolumes,
  browseDirectory,
} from '../api/client';
import Spinner from '../components/Spinner';
import FilterableSelect from '../components/FilterableSelect';

export default function CreateProject() {
  const navigate = useNavigate();

  // Form fields
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [taskType, setTaskType] = useState('classification');
  const [classList, setClassList] = useState([]);
  const [classInput, setClassInput] = useState('');
  const [sourceVolume, setSourceVolume] = useState('');

  // Volume browser
  const [volumeMode, setVolumeMode] = useState('direct');
  const [directPath, setDirectPath] = useState('/Volumes/');
  const [catalogs, setCatalogs] = useState([]);
  const [schemas, setSchemas] = useState([]);
  const [volumesList, setVolumesList] = useState([]);
  const [catalog, setCatalog] = useState('');
  const [schema, setSchema] = useState('');
  const [volume, setVolume] = useState('');
  const [browsing, setBrowsing] = useState(false);
  const [browseResult, setBrowseResult] = useState(null);

  // Submit
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

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

  // Build volume path from picker
  useEffect(() => {
    if (volumeMode === 'picker' && catalog && schema && volume) {
      const path = `/Volumes/${catalog}/${schema}/${volume}`;
      setSourceVolume(path);
    }
  }, [volumeMode, catalog, schema, volume]);

  useEffect(() => {
    if (volumeMode === 'direct') {
      setSourceVolume(directPath.trim());
    }
  }, [volumeMode, directPath]);

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
      setBrowseResult({ error: e.response?.data?.detail || e.message });
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

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!name.trim() || !sourceVolume || classList.length === 0) return;
    setSubmitting(true);
    setError('');
    try {
      const project = await createProject({
        name: name.trim(),
        description: description.trim(),
        task_type: taskType,
        class_list: classList,
        source_volume: sourceVolume,
      });
      navigate(`/projects/${project.id}`);
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
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

        {/* Class list */}
        <div style={{ marginBottom: '1.25rem' }}>
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
            <input
              type="text"
              value={directPath}
              onChange={(e) => setDirectPath(e.target.value)}
              placeholder="/Volumes/catalog/schema/volume"
              style={inputStyle}
            />
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
        <div style={{ display: 'flex', gap: '0.75rem', marginTop: '1.5rem' }}>
          <button
            type="submit"
            disabled={submitting || !name.trim() || !sourceVolume || classList.length === 0}
            className="btn-primary"
            style={{ padding: '0.6rem 2rem' }}
          >
            {submitting ? 'Creating...' : 'Create Project'}
          </button>
          <button
            type="button"
            onClick={() => navigate('/')}
            className="btn-secondary"
            style={{ padding: '0.6rem 1.5rem' }}
          >
            Cancel
          </button>
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
