/**
 * Project Dashboard — per-project stats, progress, and per-user breakdown.
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  fetchProject, fetchProjectStats, fetchDetailedProjectStats, cloneProject, updateProject,
  fetchSamples, sampleThumbnailUrl, exportProject, fetchEndpointStatus, preAnnotateProject,
  acceptAllDrafts, clearAllModelDrafts,
  fetchInferenceSettings, enqueuePreannotateJob, fetchPreannotateRun,
} from '../api/client';
import Spinner from '../components/Spinner';

export default function ProjectDashboard() {
  const { id: projectId } = useParams();
  const navigate = useNavigate();

  const [project, setProject] = useState(null);
  const [stats, setStats] = useState(null);
  const [detailedStats, setDetailedStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [cloning, setCloning] = useState(false);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editForm, setEditForm] = useState({});
  const [newClass, setNewClass] = useState('');

  // Export state
  const [showExport, setShowExport] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportVolume, setExportVolume] = useState('');
  const [exportResult, setExportResult] = useState(null);
  const [exportError, setExportError] = useState('');

  // Endpoint / pre-annotation state
  const [endpointStatus, setEndpointStatus] = useState(null);
  const [preAnnotating, setPreAnnotating] = useState(false);
  const [preAnnotateResult, setPreAnnotateResult] = useState(null);
  const [preAnnotateError, setPreAnnotateError] = useState('');
  const [includePreLabeledInPreAnnotate, setIncludePreLabeledInPreAnnotate] = useState(false);
  const [draftActionBusy, setDraftActionBusy] = useState(false);
  const [draftActionMessage, setDraftActionMessage] = useState('');
  const [inferenceSettings, setInferenceSettings] = useState(null);
  const [asyncPreAnnotating, setAsyncPreAnnotating] = useState(false);
  const [activeAsyncRunId, setActiveAsyncRunId] = useState(null);
  const [asyncRunMessage, setAsyncRunMessage] = useState('');

  // Gallery state
  const [gallerySamples, setGallerySamples] = useState([]);
  const [galleryTotal, setGalleryTotal] = useState(0);
  const [galleryPage, setGalleryPage] = useState(0);
  const [galleryFilter, setGalleryFilter] = useState('');
  const [filterLabel, setFilterLabel] = useState('');
  const [filterLabeler, setFilterLabeler] = useState('');
  const [filterFilename, setFilterFilename] = useState('');
  const [filenameInput, setFilenameInput] = useState('');
  const galleryPageSize = 24;
  const debounceRef = useRef(null);

  const onFilenameInputChange = useCallback((value) => {
    setFilenameInput(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setFilterFilename(value);
      setGalleryPage(0);
    }, 300);
  }, []);

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  useEffect(() => {
    Promise.all([
      fetchProject(projectId),
      fetchProjectStats(projectId),
      fetchDetailedProjectStats(projectId),
      fetchInferenceSettings(projectId).catch(() => null),
    ])
      .then(([proj, st, detailed, inf]) => {
        setProject(proj);
        setStats(st);
        setDetailedStats(detailed);
        setInferenceSettings(inf);
      })
      .catch(() => navigate('/'))
      .finally(() => setLoading(false));
  }, [projectId, navigate]);

  useEffect(() => {
    if (!project) return;
    fetchEndpointStatus(projectId)
      .then(setEndpointStatus)
      .catch(() => setEndpointStatus({ status: 'error', error: 'Could not check endpoint' }));
  }, [project, projectId]);

  useEffect(() => {
    if (!activeAsyncRunId) return;
    const tick = async () => {
      try {
        const r = await fetchPreannotateRun(projectId, activeAsyncRunId);
        if (['succeeded', 'failed', 'cancelled'].includes(r.status)) {
          setActiveAsyncRunId(null);
          setAsyncRunMessage(
            r.status === 'succeeded'
              ? `Background pre-label finished (${r.completed} samples pre-labeled, ${r.skipped} skipped, ${r.failed} failed).`
              : `Background pre-label ended: ${r.status}${r.error_message ? ` — ${r.error_message}` : ''}`,
          );
          const [st, detailed] = await Promise.all([
            fetchProjectStats(projectId),
            fetchDetailedProjectStats(projectId),
          ]);
          setStats(st);
          setDetailedStats(detailed);
          setGalleryPage(0);
        }
      } catch (_) { /* still running */ }
    };
    tick();
    const id = setInterval(tick, 4000);
    return () => clearInterval(id);
  }, [activeAsyncRunId, projectId]);

  const handleClone = async () => {
    if (cloning) return;
    setCloning(true);
    try {
      const newProj = await cloneProject(projectId);
      navigate(`/projects/${newProj.id}`);
    } catch (err) {
      console.error('Clone failed:', err);
      alert('Failed to create new version: ' + (err.response?.data?.detail || err.message));
    } finally {
      setCloning(false);
    }
  };

  const handleAsyncPreAnnotate = async () => {
    if (asyncPreAnnotating) return;
    const scope = includePreLabeledInPreAnnotate
      ? 'unlabeled and pre-labeled rows (model drafts on pre-labeled will be replaced)'
      : 'unlabeled rows only';
    if (!confirm(`Start a Databricks Job to pre-label ${scope}? You can close this page; progress updates every few seconds.\n\nContinue?`)) return;
    setAsyncPreAnnotating(true);
    setAsyncRunMessage('');
    try {
      const r = await enqueuePreannotateJob(projectId, {
        include_pre_labeled: includePreLabeledInPreAnnotate,
        max_samples: 0,
      });
      setActiveAsyncRunId(r.id);
      setAsyncRunMessage(`Job queued (run record #${r.id}, Databricks run ${r.databricks_run_id ?? '—'}).`);
    } catch (err) {
      setAsyncRunMessage(err.response?.data?.detail || err.message || 'Failed to start job');
    } finally {
      setAsyncPreAnnotating(false);
    }
  };

  const handlePreAnnotate = async () => {
    if (preAnnotating) return;
    const scope = includePreLabeledInPreAnnotate
      ? 'unlabeled and pre-labeled images (model drafts on pre-labeled rows will be replaced)'
      : 'unlabeled images only';
    if (!confirm(
      `This will send ${scope} to the model endpoint for pre-labeling. This may take a while for large projects.\n\nContinue?`
    )) return;
    setPreAnnotating(true);
    setPreAnnotateError('');
    setPreAnnotateResult(null);
    try {
      const result = await preAnnotateProject(projectId, {
        include_pre_labeled: includePreLabeledInPreAnnotate,
      });
      setPreAnnotateResult(result);
      const st = await fetchProjectStats(projectId);
      setStats(st);
      setGalleryPage(0);
    } catch (err) {
      setPreAnnotateError(err.response?.data?.detail || err.message);
    } finally {
      setPreAnnotating(false);
    }
  };

  const handleAcceptAllDrafts = async () => {
    if (draftActionBusy) return;
    if (!confirm('Confirm all draft (model suggestion) annotations in this project? They will count as human-approved labels.')) return;
    setDraftActionBusy(true);
    setDraftActionMessage('');
    try {
      const r = await acceptAllDrafts(projectId);
      setDraftActionMessage(`Accepted ${r.annotations_affected} draft(s) across ${r.samples_touched} sample(s).`);
      const [st, detailed] = await Promise.all([
        fetchProjectStats(projectId),
        fetchDetailedProjectStats(projectId),
      ]);
      setStats(st);
      setDetailedStats(detailed);
      setGalleryPage(0);
    } catch (err) {
      setDraftActionMessage(err.response?.data?.detail || err.message || 'Failed');
    } finally {
      setDraftActionBusy(false);
    }
  };

  const handleClearAllDrafts = async () => {
    if (draftActionBusy) return;
    if (!confirm('Delete ALL model draft annotations project-wide? Human labels are kept. This cannot be undone.')) return;
    setDraftActionBusy(true);
    setDraftActionMessage('');
    try {
      const r = await clearAllModelDrafts(projectId);
      setDraftActionMessage(`Removed ${r.annotations_affected} draft(s) from ${r.samples_touched} sample(s).`);
      const [st, detailed] = await Promise.all([
        fetchProjectStats(projectId),
        fetchDetailedProjectStats(projectId),
      ]);
      setStats(st);
      setDetailedStats(detailed);
      setGalleryPage(0);
    } catch (err) {
      setDraftActionMessage(err.response?.data?.detail || err.message || 'Failed');
    } finally {
      setDraftActionBusy(false);
    }
  };

  const openExportModal = () => {
    // Default export path: same source volume with /exports subdirectory
    // e.g. /Volumes/catalog/schema/volume -> /Volumes/catalog/schema/volume/exports
    if (project?.source_volume) {
      setExportVolume(project.source_volume.replace(/\/+$/, '') + '/exports');
    }
    setExportResult(null);
    setExportError('');
    setShowExport(true);
  };

  const handleExport = async () => {
    if (exporting || !exportVolume.trim()) return;
    setExporting(true);
    setExportError('');
    setExportResult(null);
    try {
      const result = await exportProject(projectId, exportVolume.trim());
      setExportResult(result);
    } catch (err) {
      setExportError(err.response?.data?.detail || err.message);
    } finally {
      setExporting(false);
    }
  };

  // Load gallery
  useEffect(() => {
    if (!project) return;
    const params = { page: galleryPage, page_size: galleryPageSize };
    if (galleryFilter) params.status = galleryFilter;
    if (filterLabel) params.label = filterLabel;
    if (filterLabeler) params.labeler = filterLabeler;
    if (filterFilename) params.filename = filterFilename;
    fetchSamples(projectId, params)
      .then((page) => {
        setGallerySamples(page.items);
        setGalleryTotal(page.total);
      })
      .catch(console.error);
  }, [project, projectId, galleryPage, galleryFilter, filterLabel, filterLabeler, filterFilename]);

  const galleryTotalPages = Math.ceil(galleryTotal / galleryPageSize);

  const startEditing = () => {
    setEditForm({
      name: project.name,
      description: project.description || '',
      source_volume: project.source_volume,
      class_list: [...project.class_list],
      serving_endpoint: project.serving_endpoint || '',
    });
    setNewClass('');
    setEditing(true);
  };

  const cancelEditing = () => {
    setEditing(false);
    setEditForm({});
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const patch = {};
      if (editForm.name !== project.name) patch.name = editForm.name;
      if (editForm.description !== (project.description || '')) patch.description = editForm.description;
      if (JSON.stringify(editForm.class_list) !== JSON.stringify(project.class_list)) patch.class_list = editForm.class_list;
      if ((editForm.serving_endpoint || '') !== (project.serving_endpoint || '')) patch.serving_endpoint = editForm.serving_endpoint;

      const sourceChanged = editForm.source_volume !== project.source_volume;
      if (sourceChanged) {
        if (!confirm(
          'Changing the source volume will DELETE all existing samples and annotations for this project. This cannot be undone.\n\nAre you sure?'
        )) {
          setSaving(false);
          return;
        }
        patch.source_volume = editForm.source_volume;
        patch.confirm_source_change = true;
      }

      if (Object.keys(patch).length === 0) {
        setEditing(false);
        setSaving(false);
        return;
      }

      const updated = await updateProject(projectId, patch);
      setProject(updated);
      if (sourceChanged) {
        const [st, detailed] = await Promise.all([
          fetchProjectStats(projectId),
          fetchDetailedProjectStats(projectId),
        ]);
        setStats(st);
        setDetailedStats(detailed);
      }
      if (patch.serving_endpoint !== undefined) {
        fetchEndpointStatus(projectId).then(setEndpointStatus).catch(() => {});
      }
      setEditing(false);
    } catch (err) {
      alert(err.response?.data?.detail || err.message);
    } finally {
      setSaving(false);
    }
  };

  const addClassToList = () => {
    const cls = newClass.trim();
    if (!cls) return;
    if (editForm.class_list.includes(cls)) return;
    setEditForm({ ...editForm, class_list: [...editForm.class_list, cls] });
    setNewClass('');
  };

  const removeClassFromList = (cls) => {
    setEditForm({ ...editForm, class_list: editForm.class_list.filter(c => c !== cls) });
  };

  if (loading) return <Spinner label="Loading project..." />;
  if (!project) return null;

  const pct = stats && stats.total > 0
    ? Math.round((stats.labeled / stats.total) * 100)
    : 0;

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '1.5rem' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.25rem' }}>
            <button
              onClick={() => navigate('/')}
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--text-secondary)',
                cursor: 'pointer',
                fontSize: '1.2rem',
                padding: '0.25rem',
              }}
            >
              &#x2190;
            </button>
            <h1 style={{ fontSize: '1.75rem', fontWeight: 700, margin: 0 }}>
              {project.name}
            </h1>
            <span className={`badge ${project.task_type === 'detection' ? 'badge-yellow' : 'badge-blue'}`}>
              {project.task_type}
            </span>
            {project.version > 1 && (
              <span className="badge" style={{ background: 'rgba(255,255,255,0.08)', color: 'var(--text-secondary)' }}>
                v{project.version}
              </span>
            )}
          </div>
          {project.description && (
            <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginLeft: '2.5rem' }}>
              {project.description}
            </p>
          )}
          <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginLeft: '2.5rem', marginTop: '0.25rem' }}>
            Created by {project.created_by || 'unknown'} on {new Date(project.created_at).toLocaleDateString()}
          </div>
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', alignItems: 'center' }}>
          {endpointStatus && endpointStatus.status === 'ready' && stats && (stats.unlabeled > 0 || (includePreLabeledInPreAnnotate && stats.pre_labeled > 0)) && (
            <>
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem', fontSize: '0.75rem', color: 'var(--text-muted)', userSelect: 'none' }}>
                <input
                  type="checkbox"
                  checked={includePreLabeledInPreAnnotate}
                  onChange={(e) => setIncludePreLabeledInPreAnnotate(e.target.checked)}
                />
                Include pre-labeled
              </label>
              <button
                className="btn-secondary"
                onClick={handlePreAnnotate}
                disabled={preAnnotating}
                style={{ padding: '0.6rem 1rem', fontSize: '0.85rem' }}
              >
                {preAnnotating ? 'Pre-labeling...' : 'Pre-label'}
              </button>
              {inferenceSettings?.async_preannotate_job_configured && (
                <button
                  className="btn-secondary"
                  onClick={handleAsyncPreAnnotate}
                  disabled={asyncPreAnnotating || Boolean(activeAsyncRunId)}
                  style={{ padding: '0.6rem 1rem', fontSize: '0.85rem' }}
                  title="Runs the bundle-deployed Databricks Job (non-blocking)"
                >
                  {asyncPreAnnotating ? 'Queueing…' : activeAsyncRunId ? 'Job running…' : 'Pre-label (job)'}
                </button>
              )}
            </>
          )}
          {stats && stats.pre_labeled > 0 && (
            <>
              <button
                className="btn-secondary"
                onClick={handleAcceptAllDrafts}
                disabled={draftActionBusy}
                style={{ padding: '0.6rem 1rem', fontSize: '0.85rem' }}
                title="Confirm every draft annotation in this project"
              >
                {draftActionBusy ? '…' : 'Accept all drafts'}
              </button>
              <button
                className="btn-secondary"
                onClick={handleClearAllDrafts}
                disabled={draftActionBusy}
                style={{ padding: '0.6rem 1rem', fontSize: '0.85rem', color: '#f97316' }}
                title="Remove all model-generated draft rows"
              >
                Clear model drafts
              </button>
            </>
          )}
          <button
            className="btn-secondary"
            onClick={openExportModal}
            disabled={!stats || stats.labeled === 0}
            style={{ padding: '0.6rem 1rem', fontSize: '0.85rem' }}
          >
            Export Dataset
          </button>
          <button
            className="btn-secondary"
            onClick={handleClone}
            disabled={cloning}
            style={{ padding: '0.6rem 1rem', fontSize: '0.85rem' }}
          >
            {cloning ? 'Creating...' : 'New Version'}
          </button>
          <button
            className="btn-primary"
            onClick={() => navigate(`/projects/${projectId}/label`)}
            style={{ padding: '0.6rem 1.5rem' }}
          >
            Start Labeling
          </button>
        </div>
      </div>

      {/* Export Modal */}
      {showExport && (
        <div className="card" style={{ marginBottom: '1.5rem', border: '1px solid var(--accent-blue)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
            <h3 style={{ fontWeight: 600, fontSize: '1rem', margin: 0 }}>
              Export Dataset
            </h3>
            <button
              onClick={() => setShowExport(false)}
              style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', fontSize: '1.1rem' }}
            >
              &#x2715;
            </button>
          </div>

          <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '0.75rem' }}>
            Export {stats?.labeled || 0} labeled samples as {project.task_type === 'detection' ? 'COCO JSON' : 'CSV + images'} to a UC Volume.
          </div>

          <div style={{ marginBottom: '0.75rem' }}>
            <label style={{ display: 'block', fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '0.3rem' }}>
              Export Volume Path
            </label>
            <input
              type="text"
              value={exportVolume}
              onChange={(e) => setExportVolume(e.target.value)}
              placeholder="/Volumes/catalog/schema/volume"
              disabled={exporting}
              style={{
                width: '100%',
                padding: '0.5rem 0.75rem',
                background: 'var(--bg-input)',
                color: 'var(--text-primary)',
                border: '1px solid var(--border-color)',
                borderRadius: 4,
                fontSize: '0.85rem',
              }}
            />
          </div>

          {exportError && (
            <div style={{
              padding: '0.5rem 0.75rem',
              borderRadius: 4,
              background: 'rgba(239, 68, 68, 0.1)',
              border: '1px solid rgba(239, 68, 68, 0.3)',
              color: '#ef4444',
              fontSize: '0.8rem',
              marginBottom: '0.75rem',
            }}>
              {exportError}
            </div>
          )}

          {exportResult && (
            <div style={{
              padding: '0.75rem',
              borderRadius: 4,
              background: 'rgba(34, 197, 94, 0.1)',
              border: '1px solid rgba(34, 197, 94, 0.3)',
              fontSize: '0.8rem',
              marginBottom: '0.75rem',
            }}>
              <div style={{ color: 'var(--status-success)', fontWeight: 600, marginBottom: '0.3rem' }}>
                Export complete!
              </div>
              <div style={{ color: 'var(--text-secondary)' }}>
                <strong>{exportResult.images}</strong> images, <strong>{exportResult.annotations}</strong> annotations
              </div>
              <div style={{
                marginTop: '0.4rem',
                padding: '0.3rem 0.5rem',
                background: 'var(--bg-secondary)',
                borderRadius: 3,
                fontFamily: 'monospace',
                fontSize: '0.75rem',
                color: 'var(--text-primary)',
                wordBreak: 'break-all',
              }}>
                {exportResult.export_path}
              </div>
              <div style={{ color: 'var(--text-muted)', marginTop: '0.3rem', fontSize: '0.75rem' }}>
                Format: {exportResult.format === 'coco' ? 'COCO JSON (annotations.json + images/)' : 'CSV (labels.csv + images/)'}
              </div>
            </div>
          )}

          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button
              className="btn-primary"
              onClick={handleExport}
              disabled={exporting || !exportVolume.trim() || !!exportResult}
              style={{ padding: '0.5rem 1.25rem', fontSize: '0.85rem' }}
            >
              {exporting ? 'Exporting...' : 'Export'}
            </button>
            <button
              className="btn-secondary"
              onClick={() => setShowExport(false)}
              style={{ padding: '0.5rem 1rem', fontSize: '0.85rem' }}
            >
              {exportResult ? 'Done' : 'Cancel'}
            </button>
          </div>
        </div>
      )}

      {/* Endpoint status badge */}
      {endpointStatus && (
        <div className="card" style={{ marginBottom: '1rem', padding: '0.6rem 1rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span style={{
            width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
            background:
              endpointStatus.status === 'ready' ? 'var(--status-success)'
              : endpointStatus.status === 'not_ready' ? 'var(--status-warning)'
              : endpointStatus.status === 'not_configured' ? 'var(--text-muted)'
              : '#ef4444',
          }} />
          <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
            Pre-annotation:{' '}
            {endpointStatus.status === 'ready' && (
              <span style={{ color: 'var(--status-success)' }}>Ready ({endpointStatus.endpoint})</span>
            )}
            {endpointStatus.status === 'not_ready' && (
              <span style={{ color: 'var(--status-warning)' }}>Endpoint updating ({endpointStatus.endpoint})</span>
            )}
            {endpointStatus.status === 'not_configured' && (
              <span style={{ color: 'var(--text-muted)' }}>Not configured</span>
            )}
            {(endpointStatus.status === 'not_found' || endpointStatus.status === 'error') && (
              <span style={{ color: '#ef4444' }}>{endpointStatus.error || 'Endpoint unreachable'}</span>
            )}
          </span>
        </div>
      )}

      {/* Pre-annotate result/error */}
      {preAnnotateError && (
        <div style={{
          padding: '0.5rem 0.75rem',
          borderRadius: 4,
          background: 'rgba(239, 68, 68, 0.1)',
          border: '1px solid rgba(239, 68, 68, 0.3)',
          color: '#ef4444',
          fontSize: '0.8rem',
          marginBottom: '1rem',
        }}>
          Pre-annotation failed: {preAnnotateError}
        </div>
      )}
      {preAnnotateResult && (
        <div style={{
          padding: '0.5rem 0.75rem',
          borderRadius: 4,
          background: 'rgba(34, 197, 94, 0.1)',
          border: '1px solid rgba(34, 197, 94, 0.3)',
          fontSize: '0.8rem',
          marginBottom: '1rem',
        }}>
          <span style={{ color: 'var(--status-success)', fontWeight: 600 }}>Pre-annotation complete: </span>
          <span style={{ color: 'var(--text-secondary)' }}>
            {preAnnotateResult.completed} pre-labeled, {preAnnotateResult.skipped} below threshold / empty, {preAnnotateResult.failed} failed
          </span>
        </div>
      )}
      {draftActionMessage && (
        <div style={{
          padding: '0.5rem 0.75rem',
          borderRadius: 4,
          background: 'rgba(59, 130, 246, 0.08)',
          border: '1px solid rgba(59, 130, 246, 0.25)',
          fontSize: '0.8rem',
          marginBottom: '1rem',
          color: 'var(--text-secondary)',
        }}>
          {draftActionMessage}
        </div>
      )}
      {asyncRunMessage && (
        <div style={{
          padding: '0.5rem 0.75rem',
          borderRadius: 4,
          background: 'rgba(59, 130, 246, 0.08)',
          border: '1px solid rgba(59, 130, 246, 0.25)',
          fontSize: '0.8rem',
          marginBottom: '1rem',
          color: 'var(--text-secondary)',
        }}>
          {asyncRunMessage}
        </div>
      )}

      {/* Stats cards */}
      {stats && (
        <>
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
            gap: '1rem',
            marginBottom: '1.5rem',
          }}>
            {[
              { label: 'Total', value: stats.total, color: 'var(--text-primary)' },
              { label: 'Labeled', value: stats.labeled, color: 'var(--status-success)' },
              ...(stats.pre_labeled > 0 ? [{ label: 'Pre-labeled', value: stats.pre_labeled, color: '#a78bfa' }] : []),
              { label: 'Skipped', value: stats.skipped, color: 'var(--status-warning)' },
              { label: 'Remaining', value: stats.unlabeled, color: 'var(--accent-blue)' },
            ].map((card) => (
              <div key={card.label} className="card" style={{ textAlign: 'center' }}>
                <div style={{ fontSize: '2rem', fontWeight: 700, color: card.color, lineHeight: 1.2 }}>
                  {card.value}
                </div>
                <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.25rem' }}>
                  {card.label}
                </div>
              </div>
            ))}
          </div>

          {/* Progress bar */}
          <div className="card" style={{ marginBottom: '1.5rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem', marginBottom: '0.5rem' }}>
              <span style={{ fontWeight: 600 }}>Overall Progress</span>
              <span style={{ color: 'var(--accent-blue)', fontWeight: 600 }}>{pct}%</span>
            </div>
            <div className="progress-bar" style={{ height: 8 }}>
              <div className="progress-fill" style={{ width: `${pct}%` }} />
            </div>
            <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.35rem' }}>
              {stats.labeled} labeled{stats.pre_labeled > 0 ? `, ${stats.pre_labeled} pre-labeled` : ''}, {stats.skipped} skipped, {stats.unlabeled} remaining
            </div>
          </div>

          {/* Analytics Section */}
          {detailedStats && (
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))',
              gap: '1rem',
              marginBottom: '1.5rem',
            }}>
              {/* Class Distribution */}
              <div className="card">
                <h3 style={{ fontWeight: 600, fontSize: '1rem', marginBottom: '0.75rem' }}>
                  Class Distribution
                </h3>
                {detailedStats.per_class.length === 0 ? (
                  <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>No classes defined</div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                    {(() => {
                      const maxCount = Math.max(...detailedStats.per_class.map(c => c.count), 1);
                      const colors = [
                        '#3b82f6', '#8b5cf6', '#ec4899', '#f59e0b', '#10b981',
                        '#06b6d4', '#f97316', '#6366f1', '#14b8a6', '#e11d48',
                      ];
                      return detailedStats.per_class.map((cls, i) => (
                        <div key={cls.label} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                          <div style={{
                            width: 90,
                            fontSize: '0.8rem',
                            color: 'var(--text-secondary)',
                            textAlign: 'right',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                            flexShrink: 0,
                          }} title={cls.label}>
                            {cls.label}
                          </div>
                          <div style={{ flex: 1, background: 'var(--bg-secondary)', borderRadius: 4, height: 22, position: 'relative' }}>
                            <div style={{
                              width: `${(cls.count / maxCount) * 100}%`,
                              minWidth: cls.count > 0 ? 4 : 0,
                              height: '100%',
                              background: colors[i % colors.length],
                              borderRadius: 4,
                              transition: 'width 0.4s ease',
                            }} />
                          </div>
                          <div style={{
                            width: 36,
                            fontSize: '0.8rem',
                            fontWeight: 600,
                            color: 'var(--text-primary)',
                            textAlign: 'right',
                            flexShrink: 0,
                          }}>
                            {cls.count}
                          </div>
                        </div>
                      ));
                    })()}
                  </div>
                )}
              </div>

              {/* Velocity + Completion */}
              <div className="card">
                <h3 style={{ fontWeight: 600, fontSize: '1rem', marginBottom: '0.75rem' }}>
                  Labeling Velocity
                </h3>
                <VelocityChart data={detailedStats.daily_velocity} />
                <div style={{
                  marginTop: '1rem',
                  padding: '0.75rem',
                  background: 'var(--bg-secondary)',
                  borderRadius: 6,
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                }}>
                  <div>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.15rem' }}>
                      Avg. Daily Rate (7d)
                    </div>
                    <div style={{ fontSize: '1.1rem', fontWeight: 700, color: 'var(--accent-blue)' }}>
                      ~{detailedStats.avg_daily_rate} samples/day
                    </div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.15rem' }}>
                      Est. Completion
                    </div>
                    <div style={{ fontSize: '1.1rem', fontWeight: 700, color: detailedStats.estimated_completion_date ? 'var(--status-success)' : 'var(--text-muted)' }}>
                      {detailedStats.estimated_completion_date
                        ? new Date(detailedStats.estimated_completion_date + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
                        : detailedStats.avg_daily_rate === 0 ? 'No recent activity' : 'Complete'}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Sample Gallery */}
          <div className="card" style={{ marginBottom: '1.5rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
              <h3 style={{ fontWeight: 600, fontSize: '1rem', margin: 0 }}>
                Samples
              </h3>
              <div style={{ display: 'flex', gap: '0.3rem' }}>
                {['', 'unlabeled', 'pre_labeled', 'labeled', 'skipped'].map((f) => (
                  <button
                    key={f}
                    className="btn-secondary"
                    onClick={() => { setGalleryFilter(f); setGalleryPage(0); }}
                    style={{
                      padding: '0.25rem 0.6rem',
                      fontSize: '0.75rem',
                      background: galleryFilter === f ? 'var(--accent-blue)' : undefined,
                      color: galleryFilter === f ? '#fff' : undefined,
                      border: galleryFilter === f ? '1px solid var(--accent-blue)' : undefined,
                    }}
                  >
                    {f === '' ? 'All' : f === 'pre_labeled' ? 'Pre-labeled' : f}
                  </button>
                ))}
              </div>
            </div>

            {/* Search & filter bar */}
            <div style={{
              display: 'flex', flexWrap: 'wrap', gap: '0.5rem',
              alignItems: 'center', marginBottom: '0.75rem',
            }}>
              <input
                type="text"
                value={filenameInput}
                onChange={(e) => onFilenameInputChange(e.target.value)}
                placeholder="Search filename..."
                style={{
                  flex: '1 1 160px', minWidth: 120,
                  padding: '0.35rem 0.6rem',
                  background: 'var(--bg-input)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border-color)',
                  borderRadius: 4,
                  fontSize: '0.8rem',
                }}
              />
              <select
                value={filterLabel}
                onChange={(e) => { setFilterLabel(e.target.value); setGalleryPage(0); }}
                style={{
                  padding: '0.35rem 0.6rem',
                  background: 'var(--bg-input)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border-color)',
                  borderRadius: 4,
                  fontSize: '0.8rem',
                }}
              >
                <option value="">All labels</option>
                {(project.class_list || []).map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
              <select
                value={filterLabeler}
                onChange={(e) => { setFilterLabeler(e.target.value); setGalleryPage(0); }}
                style={{
                  padding: '0.35rem 0.6rem',
                  background: 'var(--bg-input)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border-color)',
                  borderRadius: 4,
                  fontSize: '0.8rem',
                }}
              >
                <option value="">All labelers</option>
                {(stats?.per_user || []).map((u) => (
                  <option key={u.user} value={u.user}>{u.user}</option>
                ))}
              </select>
              {(filterLabel || filterLabeler || filterFilename || galleryFilter) && (
                <button
                  className="btn-secondary"
                  onClick={() => {
                    setGalleryFilter('');
                    setFilterLabel('');
                    setFilterLabeler('');
                    setFilterFilename('');
                    setFilenameInput('');
                    setGalleryPage(0);
                  }}
                  style={{ padding: '0.35rem 0.6rem', fontSize: '0.75rem' }}
                >
                  Clear filters
                </button>
              )}
            </div>

            {gallerySamples.length === 0 ? (
              <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem', padding: '1rem 0', textAlign: 'center' }}>
                No samples found
              </div>
            ) : (
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))',
                gap: '0.5rem',
              }}>
                {gallerySamples.map((s) => (
                  <div
                    key={s.id}
                    onClick={() => navigate(`/projects/${projectId}/label?sample=${s.id}`)}
                    style={{
                      cursor: 'pointer',
                      borderRadius: 6,
                      border: '1px solid var(--border-color)',
                      overflow: 'hidden',
                      background: 'var(--bg-secondary)',
                      transition: 'border-color 0.15s',
                    }}
                    onMouseEnter={(e) => e.currentTarget.style.borderColor = 'var(--accent-blue)'}
                    onMouseLeave={(e) => e.currentTarget.style.borderColor = 'var(--border-color)'}
                  >
                    <div style={{ position: 'relative', paddingTop: '100%' }}>
                      <img
                        src={sampleThumbnailUrl(projectId, s.id, 200)}
                        alt={s.filename}
                        loading="lazy"
                        style={{
                          position: 'absolute',
                          top: 0, left: 0,
                          width: '100%', height: '100%',
                          objectFit: 'cover',
                        }}
                      />
                      <span style={{
                        position: 'absolute',
                        top: 4, right: 4,
                        padding: '0.1rem 0.35rem',
                        borderRadius: 3,
                        fontSize: '0.6rem',
                        fontWeight: 600,
                        background: s.status === 'labeled' ? 'var(--status-success)'
                          : s.status === 'pre_labeled' ? '#a78bfa'
                          : s.status === 'skipped' ? 'var(--status-warning)'
                          : 'rgba(255,255,255,0.15)',
                        color: s.status === 'unlabeled' ? 'var(--text-muted)' : '#fff',
                      }}>
                        {s.status}
                      </span>
                    </div>
                    <div style={{ padding: '0.3rem 0.4rem' }}>
                      <div style={{
                        fontSize: '0.65rem',
                        color: 'var(--text-secondary)',
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}>
                        {s.filename}
                      </div>
                      {s.labels && s.labels.length > 0 && (
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.15rem', marginTop: '0.2rem' }}>
                          {s.labels.map((lbl) => (
                            <span
                              key={lbl}
                              style={{
                                padding: '0.05rem 0.3rem',
                                borderRadius: 3,
                                fontSize: '0.55rem',
                                fontWeight: 600,
                                background: 'rgba(59,130,246,0.15)',
                                color: 'var(--accent-blue)',
                              }}
                            >
                              {lbl}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Pagination */}
            {galleryTotalPages > 1 && (
              <div style={{
                display: 'flex',
                justifyContent: 'center',
                alignItems: 'center',
                gap: '0.5rem',
                marginTop: '0.75rem',
                fontSize: '0.8rem',
              }}>
                <button
                  className="btn-secondary"
                  onClick={() => setGalleryPage(p => Math.max(0, p - 1))}
                  disabled={galleryPage === 0}
                  style={{ padding: '0.25rem 0.5rem', fontSize: '0.75rem' }}
                >
                  Prev
                </button>
                <span style={{ color: 'var(--text-secondary)' }}>
                  Page {galleryPage + 1} / {galleryTotalPages}
                </span>
                <button
                  className="btn-secondary"
                  onClick={() => setGalleryPage(p => Math.min(galleryTotalPages - 1, p + 1))}
                  disabled={galleryPage >= galleryTotalPages - 1}
                  style={{ padding: '0.25rem 0.5rem', fontSize: '0.75rem' }}
                >
                  Next
                </button>
              </div>
            )}
          </div>

          {/* Per-user breakdown */}
          {stats.per_user && stats.per_user.length > 0 && (
            <div className="card">
              <h3 style={{ fontWeight: 600, fontSize: '1rem', marginBottom: '0.75rem' }}>
                Contributor Activity
              </h3>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border-color)' }}>
                    <th style={thStyle}>User</th>
                    <th style={{ ...thStyle, textAlign: 'right' }}>Labeled</th>
                    <th style={{ ...thStyle, textAlign: 'right' }}>Skipped</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.per_user.map((row, i) => (
                    <tr
                      key={row.user}
                      style={{
                        borderBottom: i < stats.per_user.length - 1 ? '1px solid var(--border-color)' : 'none',
                        background: i % 2 === 1 ? 'var(--bg-hover)' : 'transparent',
                      }}
                    >
                      <td style={tdStyle}>{row.user}</td>
                      <td style={{ ...tdStyle, textAlign: 'right', color: 'var(--status-success)' }}>
                        {row.labeled}
                      </td>
                      <td style={{ ...tdStyle, textAlign: 'right', color: 'var(--text-muted)' }}>
                        {row.skipped}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Project info */}
          <div className="card" style={{ marginTop: '1.5rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
              <h3 style={{ fontWeight: 600, fontSize: '1rem', margin: 0 }}>
                Project Info
              </h3>
              {!editing ? (
                <button
                  className="btn-secondary"
                  onClick={startEditing}
                  style={{ padding: '0.3rem 0.75rem', fontSize: '0.8rem' }}
                >
                  Edit
                </button>
              ) : (
                <div style={{ display: 'flex', gap: '0.5rem' }}>
                  <button
                    className="btn-secondary"
                    onClick={cancelEditing}
                    disabled={saving}
                    style={{ padding: '0.3rem 0.75rem', fontSize: '0.8rem' }}
                  >
                    Cancel
                  </button>
                  <button
                    className="btn-primary"
                    onClick={handleSave}
                    disabled={saving}
                    style={{ padding: '0.3rem 0.75rem', fontSize: '0.8rem' }}
                  >
                    {saving ? 'Saving...' : 'Save'}
                  </button>
                </div>
              )}
            </div>

            {!editing ? (
              <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr', gap: '0.4rem', fontSize: '0.85rem' }}>
                <span style={{ color: 'var(--text-muted)' }}>Name</span>
                <span>{project.name}</span>
                <span style={{ color: 'var(--text-muted)' }}>Description</span>
                <span style={{ color: project.description ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                  {project.description || '(none)'}
                </span>
                <span style={{ color: 'var(--text-muted)' }}>Source</span>
                <span style={{ wordBreak: 'break-all' }}>{project.source_volume}</span>
                <span style={{ color: 'var(--text-muted)' }}>Classes</span>
                <span>
                  {project.class_list.map((c) => (
                    <span key={c} className="badge badge-blue" style={{ marginRight: '0.25rem' }}>{c}</span>
                  ))}
                </span>
                <span style={{ color: 'var(--text-muted)' }}>Endpoint</span>
                <span style={{ color: project.serving_endpoint ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                  {project.serving_endpoint || '(none)'}
                </span>
                <span style={{ color: 'var(--text-muted)' }}>Version</span>
                <span>
                  v{project.version || 1}
                  {project.parent_project_id && (
                    <button
                      onClick={() => navigate(`/projects/${project.parent_project_id}`)}
                      style={{
                        background: 'none',
                        border: 'none',
                        color: 'var(--accent-blue)',
                        cursor: 'pointer',
                        fontSize: '0.85rem',
                        marginLeft: '0.5rem',
                        textDecoration: 'underline',
                      }}
                    >
                      View parent project
                    </button>
                  )}
                </span>
              </div>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr', gap: '0.6rem', fontSize: '0.85rem', alignItems: 'start' }}>
                <span style={{ color: 'var(--text-muted)', paddingTop: '0.4rem' }}>Name</span>
                <input
                  type="text"
                  value={editForm.name}
                  onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
                  className="input"
                  style={{ padding: '0.4rem 0.6rem', fontSize: '0.85rem' }}
                />
                <span style={{ color: 'var(--text-muted)', paddingTop: '0.4rem' }}>Description</span>
                <textarea
                  value={editForm.description}
                  onChange={(e) => setEditForm({ ...editForm, description: e.target.value })}
                  className="input"
                  rows={2}
                  style={{ padding: '0.4rem 0.6rem', fontSize: '0.85rem', resize: 'vertical' }}
                />
                <span style={{ color: 'var(--text-muted)', paddingTop: '0.4rem' }}>Source</span>
                <div>
                  <input
                    type="text"
                    value={editForm.source_volume}
                    onChange={(e) => setEditForm({ ...editForm, source_volume: e.target.value })}
                    className="input"
                    style={{ padding: '0.4rem 0.6rem', fontSize: '0.85rem', width: '100%' }}
                  />
                  {editForm.source_volume !== project.source_volume && (
                    <div style={{
                      fontSize: '0.75rem',
                      color: '#ff6b6b',
                      marginTop: '0.25rem',
                    }}>
                      Warning: changing the source will delete all samples and annotations.
                    </div>
                  )}
                </div>
                <span style={{ color: 'var(--text-muted)', paddingTop: '0.4rem' }}>Classes</span>
                <div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.25rem', marginBottom: '0.5rem' }}>
                    {editForm.class_list.map((c) => (
                      <span key={c} className="badge badge-blue" style={{ display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }}>
                        {c}
                        <button
                          onClick={() => removeClassFromList(c)}
                          style={{
                            background: 'none', border: 'none', color: 'inherit',
                            cursor: 'pointer', padding: 0, fontSize: '0.7rem', lineHeight: 1, opacity: 0.7,
                          }}
                        >
                          &#x2715;
                        </button>
                      </span>
                    ))}
                  </div>
                  <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <input
                      type="text"
                      value={newClass}
                      onChange={(e) => setNewClass(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addClassToList())}
                      placeholder="Add class..."
                      className="input"
                      style={{ padding: '0.3rem 0.6rem', fontSize: '0.8rem', flex: 1 }}
                    />
                    <button
                      className="btn-secondary"
                      onClick={addClassToList}
                      style={{ padding: '0.3rem 0.6rem', fontSize: '0.8rem' }}
                    >
                      Add
                    </button>
                  </div>
                </div>
                <span style={{ color: 'var(--text-muted)', paddingTop: '0.4rem' }}>Endpoint</span>
                <div>
                  <input
                    type="text"
                    value={editForm.serving_endpoint}
                    onChange={(e) => setEditForm({ ...editForm, serving_endpoint: e.target.value })}
                    placeholder="Model Serving endpoint name"
                    className="input"
                    style={{ padding: '0.4rem 0.6rem', fontSize: '0.85rem', width: '100%' }}
                  />
                  <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.15rem' }}>
                    Databricks Model Serving endpoint for pre-annotation. Leave blank to disable.
                  </div>
                </div>
                <span style={{ color: 'var(--text-muted)' }}>Version</span>
                <span style={{ color: 'var(--text-muted)' }}>v{project.version || 1} (not editable)</span>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function VelocityChart({ data }) {
  if (!data || data.length === 0) {
    return (
      <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem', padding: '2rem 0', textAlign: 'center' }}>
        No labeling activity in the last 30 days
      </div>
    );
  }

  const W = 480, H = 160, PAD_L = 40, PAD_R = 12, PAD_T = 12, PAD_B = 28;
  const chartW = W - PAD_L - PAD_R;
  const chartH = H - PAD_T - PAD_B;
  const maxCount = Math.max(...data.map(d => d.count), 1);
  const yTicks = [0, Math.round(maxCount / 2), maxCount];

  const points = data.map((d, i) => {
    const x = PAD_L + (data.length === 1 ? chartW / 2 : (i / (data.length - 1)) * chartW);
    const y = PAD_T + chartH - (d.count / maxCount) * chartH;
    return `${x},${y}`;
  }).join(' ');

  const areaPath = data.map((d, i) => {
    const x = PAD_L + (data.length === 1 ? chartW / 2 : (i / (data.length - 1)) * chartW);
    const y = PAD_T + chartH - (d.count / maxCount) * chartH;
    return `${i === 0 ? 'M' : 'L'}${x},${y}`;
  }).join(' ') + ` L${PAD_L + (data.length === 1 ? chartW / 2 : chartW)},${PAD_T + chartH} L${PAD_L + (data.length === 1 ? chartW / 2 : 0)},${PAD_T + chartH} Z`;

  const xLabels = [];
  if (data.length <= 7) {
    data.forEach((d, i) => xLabels.push({ i, label: d.date.slice(5) }));
  } else {
    [0, Math.floor(data.length / 2), data.length - 1].forEach(i => {
      xLabels.push({ i, label: data[i].date.slice(5) });
    });
  }

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto' }}>
      {yTicks.map(t => {
        const y = PAD_T + chartH - (t / maxCount) * chartH;
        return (
          <g key={t}>
            <line x1={PAD_L} x2={W - PAD_R} y1={y} y2={y}
              stroke="var(--border-color)" strokeWidth="0.5" strokeDasharray="3,3" />
            <text x={PAD_L - 6} y={y + 3} textAnchor="end"
              fill="var(--text-muted)" fontSize="9">{t}</text>
          </g>
        );
      })}
      <defs>
        <linearGradient id="vel-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--accent-blue)" stopOpacity="0.25" />
          <stop offset="100%" stopColor="var(--accent-blue)" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={areaPath} fill="url(#vel-grad)" />
      <polyline points={points} fill="none" stroke="var(--accent-blue)" strokeWidth="2"
        strokeLinecap="round" strokeLinejoin="round" />
      {data.map((d, i) => {
        const x = PAD_L + (data.length === 1 ? chartW / 2 : (i / (data.length - 1)) * chartW);
        const y = PAD_T + chartH - (d.count / maxCount) * chartH;
        return <circle key={i} cx={x} cy={y} r="2.5" fill="var(--accent-blue)" />;
      })}
      {xLabels.map(({ i, label }) => {
        const x = PAD_L + (data.length === 1 ? chartW / 2 : (i / (data.length - 1)) * chartW);
        return (
          <text key={i} x={x} y={H - 4} textAnchor="middle"
            fill="var(--text-muted)" fontSize="9">{label}</text>
        );
      })}
    </svg>
  );
}

const thStyle = {
  textAlign: 'left',
  padding: '0.5rem 0.75rem',
  fontWeight: 600,
  fontSize: '0.8rem',
  color: 'var(--text-secondary)',
};

const tdStyle = {
  padding: '0.5rem 0.75rem',
};
