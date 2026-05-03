/**
 * Project Dashboard — per-project stats, progress, and per-user breakdown.
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  fetchProject, fetchProjectStats, fetchDetailedProjectStats, cloneProject, updateProject,
  fetchSamples, sampleThumbnailUrl, exportProject, fetchEndpointStatus,
  preAnnotateProject, preAnnotateProjectStream,
  acceptAllDrafts, clearAllModelDrafts,
  fetchInferenceSettings, enqueuePreannotateJob, fetchPreannotateRun, fetchLatestPreannotateRun,
  fetchAppConfig, triggerFinetune, fetchLatestFinetuneRun,
  startEmbeddingRun, fetchEmbeddingRun, fetchLatestEmbeddingRun, fetchSimilarSamples,
  propagateLabels, detectNearDuplicates,
} from '../api/client';
import { humanizeApiError } from '../api/errors';
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

  const [configExportVolume, setConfigExportVolume] = useState('');

  // Finetuning state
  const [finetuneConfigured, setFinetuneConfigured] = useState(false);
  const [triggerFinetuneAfterExport, setTriggerFinetuneAfterExport] = useState(false);
  const [finetuneTriggering, setFinetuneTriggering] = useState(false);
  const [finetuneRun, setFinetuneRun] = useState(null);
  const [finetuneError, setFinetuneError] = useState('');
  const finetunePollingRef = useRef(null);

  // Endpoint / pre-annotation state
  const [endpointStatus, setEndpointStatus] = useState(null);
  const [preAnnotating, setPreAnnotating] = useState(false);
  const [preAnnotateResult, setPreAnnotateResult] = useState(null);
  const [preAnnotateError, setPreAnnotateError] = useState('');
  const [preAnnotateProgress, setPreAnnotateProgress] = useState(null);
  const [includePreLabeledInPreAnnotate, setIncludePreLabeledInPreAnnotate] = useState(false);
  const [preAnnotatePrompt, setPreAnnotatePrompt] = useState('');
  const [draftActionBusy, setDraftActionBusy] = useState(false);
  const [draftActionMessage, setDraftActionMessage] = useState('');
  const [inferenceSettings, setInferenceSettings] = useState(null);
  const [asyncPreAnnotating, setAsyncPreAnnotating] = useState(false);
  const [activeAsyncRunId, setActiveAsyncRunId] = useState(null);
  const [asyncRunMessage, setAsyncRunMessage] = useState('');
  const [asyncRunStatus, setAsyncRunStatus] = useState(null);
  const [asyncDatabricksRunId, setAsyncDatabricksRunId] = useState(null);

  // Actions dropdown state
  const [actionsOpen, setActionsOpen] = useState(false);
  const actionsRef = useRef(null);

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

  // Embedding / similarity state
  const [embeddingRun, setEmbeddingRun] = useState(null);
  const [embeddingError, setEmbeddingError] = useState('');
  const [similarMode, setSimilarMode] = useState(null);
  const [similarSamples, setSimilarSamples] = useState([]);
  const embeddingPollRef = useRef(null);

  // Label propagation state
  const [propagating, setPropagating] = useState(false);
  const [propagateThreshold, setPropagateThreshold] = useState(0.85);
  const [propagateResult, setPropagateResult] = useState(null);
  const [propagateError, setPropagateError] = useState('');

  // Near-duplicate detection state
  const [dupMode, setDupMode] = useState(false);
  const [dupLoading, setDupLoading] = useState(false);
  const [dupGroups, setDupGroups] = useState([]);
  const [dupTotalDuplicates, setDupTotalDuplicates] = useState(0);
  const [dupThreshold, setDupThreshold] = useState(0.95);
  const [dupError, setDupError] = useState('');

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
    if (!actionsOpen) return;
    const onClickOutside = (e) => {
      if (actionsRef.current && !actionsRef.current.contains(e.target)) setActionsOpen(false);
    };
    document.addEventListener('mousedown', onClickOutside);
    return () => document.removeEventListener('mousedown', onClickOutside);
  }, [actionsOpen]);

  useEffect(() => {
    setActiveAsyncRunId(null);
    setAsyncRunMessage('');
    setAsyncRunStatus(null);
    setAsyncDatabricksRunId(null);
    setAsyncPreAnnotating(false);
    setPreAnnotating(false);
    setPreAnnotateResult(null);
    setPreAnnotateError('');
    setPreAnnotateProgress(null);
    setFinetuneRun(null);
    setFinetuneError('');
    setEmbeddingRun(null);
    setEmbeddingError('');
    if (embeddingPollRef.current) { clearInterval(embeddingPollRef.current); embeddingPollRef.current = null; }
    setProject(null);
    setLoading(true);

    Promise.all([
      fetchProject(projectId),
      fetchProjectStats(projectId),
      fetchDetailedProjectStats(projectId),
      fetchInferenceSettings(projectId).catch(() => null),
      fetchAppConfig().catch(() => ({})),
    ])
      .then(([proj, st, detailed, inf, cfg]) => {
        setProject(proj);
        setStats(st);
        setDetailedStats(detailed);
        setInferenceSettings(inf);
        setFinetuneConfigured(!!cfg.finetune_job_configured);
        if (cfg.export_volume_path) setConfigExportVolume(cfg.export_volume_path);
      })
      .catch(() => navigate('/projects'))
      .finally(() => setLoading(false));
  }, [projectId, navigate]);

  useEffect(() => {
    if (!project) return;
    fetchEndpointStatus(projectId)
      .then(setEndpointStatus)
      .catch(() => setEndpointStatus({ status: 'error', error: 'Could not check endpoint' }));
  }, [project, projectId]);

  useEffect(() => {
    if (!project || activeAsyncRunId) return;
    fetchLatestPreannotateRun(projectId)
      .then((r) => {
        if (['pending', 'queued', 'running'].includes(r.status)) {
          setActiveAsyncRunId(r.id);
          if (r.databricks_run_id) setAsyncDatabricksRunId(r.databricks_run_id);
          if (r.total_planned > 0) {
            const done = r.completed + r.failed + r.skipped;
            const pct = Math.round((done / r.total_planned) * 100);
            setAsyncRunMessage(`Background job: ${done} / ${r.total_planned} (${pct}%) — ${r.completed} pre-labeled, ${r.skipped} skipped, ${r.failed} failed`);
          } else {
            setAsyncRunMessage(`Background job in progress (run #${r.id})…`);
          }
        } else if (r.status === 'failed' || r.status === 'cancelled') {
          setAsyncRunStatus(r.status);
          if (r.databricks_run_id) setAsyncDatabricksRunId(r.databricks_run_id);
          setAsyncRunMessage(
            `Background pre-label ${r.status}${r.error_message ? `: ${r.error_message.slice(0, 200)}` : ''}`,
          );
        }
      })
      .catch(() => {});
  }, [project, projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!activeAsyncRunId) return;
    const tick = async () => {
      try {
        const r = await fetchPreannotateRun(projectId, activeAsyncRunId);
        if (r.databricks_run_id) setAsyncDatabricksRunId(r.databricks_run_id);
        if (r.total_planned > 0) {
          const pct = Math.round(((r.completed + r.failed + r.skipped) / r.total_planned) * 100);
          setAsyncRunMessage(`Background job: ${r.completed + r.failed + r.skipped} / ${r.total_planned} (${pct}%) — ${r.completed} pre-labeled, ${r.skipped} skipped, ${r.failed} failed`);
        }
        if (['succeeded', 'failed', 'cancelled'].includes(r.status)) {
          setActiveAsyncRunId(null);
          setAsyncRunStatus(r.status);
          setAsyncRunMessage(
            r.status === 'succeeded'
              ? `Background pre-label finished (${r.completed} samples pre-labeled, ${r.skipped} skipped, ${r.failed} failed).`
              : `Background pre-label ${r.status}${r.error_message ? `: ${r.error_message.slice(0, 200)}` : ''}`,
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
      alert('Failed to create new version: ' + humanizeApiError(err));
    } finally {
      setCloning(false);
    }
  };

  const SYNC_THRESHOLD = 100;

  const eligibleCount = (() => {
    if (!stats) return 0;
    return includePreLabeledInPreAnnotate
      ? stats.unlabeled + (stats.pre_labeled || 0)
      : stats.unlabeled;
  })();

  const jobAvailable = !!inferenceSettings?.async_preannotate_job_configured;
  const willUseJob = jobAvailable && eligibleCount > SYNC_THRESHOLD;

  const buildJobRunUrl = (databricksRunId) => {
    const host = inferenceSettings?.workspace_host;
    const jobId = inferenceSettings?.pre_annotate_databricks_job_id;
    if (!host || !jobId || !databricksRunId) return null;
    return `${host}/jobs/${jobId}/runs/${databricksRunId}`;
  };

  const handlePreAnnotate = async () => {
    if (preAnnotating || asyncPreAnnotating) return;

    const scope = includePreLabeledInPreAnnotate
      ? 'unlabeled and pre-labeled images (model drafts on pre-labeled rows will be replaced)'
      : 'unlabeled images only';

    if (willUseJob) {
      const msg = `${eligibleCount} samples to pre-label — this will run as a background Databricks Job (${scope}).\nYou can close this page; progress updates every few seconds.\n\nContinue?`;
      if (!confirm(msg)) return;
      setAsyncPreAnnotating(true);
      setAsyncRunMessage('');
      setAsyncRunStatus(null);
      setAsyncDatabricksRunId(null);
      try {
        const r = await enqueuePreannotateJob(projectId, {
          include_pre_labeled: includePreLabeledInPreAnnotate,
          max_samples: 0,
          ...(preAnnotatePrompt.trim() && { text_prompt: preAnnotatePrompt.trim() }),
        });
        setActiveAsyncRunId(r.id);
        if (r.databricks_run_id) setAsyncDatabricksRunId(r.databricks_run_id);
        setAsyncRunMessage(`Background job started for ${eligibleCount} samples (run #${r.id}).`);
      } catch (err) {
        setAsyncRunMessage(humanizeApiError(err) || 'Failed to start job');
      } finally {
        setAsyncPreAnnotating(false);
      }
    } else {
      const msg = `Pre-label ${eligibleCount} ${scope}?\n\nYou'll see a live progress bar.`;
      if (!confirm(msg)) return;
      setPreAnnotating(true);
      setPreAnnotateError('');
      setPreAnnotateResult(null);
      setPreAnnotateProgress(null);
      try {
        const result = await preAnnotateProjectStream(
          projectId,
          {
            include_pre_labeled: includePreLabeledInPreAnnotate,
            ...(preAnnotatePrompt.trim() && { text_prompt: preAnnotatePrompt.trim() }),
          },
          { onProgress: (p) => setPreAnnotateProgress(p) },
        );
        setPreAnnotateResult(result);
        setPreAnnotateProgress(null);
        const [st, detailed] = await Promise.all([
          fetchProjectStats(projectId),
          fetchDetailedProjectStats(projectId),
        ]);
        setStats(st);
        setDetailedStats(detailed);
        setGalleryPage(0);
      } catch (err) {
        setPreAnnotateError(err?.message || humanizeApiError(err));
        setPreAnnotateProgress(null);
      } finally {
        setPreAnnotating(false);
      }
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
      setDraftActionMessage(humanizeApiError(err) || 'Failed');
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
      setDraftActionMessage(humanizeApiError(err) || 'Failed');
    } finally {
      setDraftActionBusy(false);
    }
  };

  const openExportModal = () => {
    if (configExportVolume) {
      setExportVolume(configExportVolume.replace(/\/+$/, '') + '/exports');
    } else if (project?.source_volume) {
      setExportVolume(project.source_volume.replace(/\/+$/, '') + '/exports');
    }
    setExportResult(null);
    setExportError('');
    setFinetuneRun(null);
    setFinetuneError('');
    setTriggerFinetuneAfterExport(false);
    setShowExport(true);
  };

  const handleExport = async () => {
    if (exporting || !exportVolume.trim()) return;
    setExporting(true);
    setExportError('');
    setExportResult(null);
    setFinetuneRun(null);
    setFinetuneError('');
    try {
      const result = await exportProject(projectId, exportVolume.trim());
      setExportResult(result);

      if (triggerFinetuneAfterExport && result.export_path) {
        setFinetuneTriggering(true);
        try {
          const ftRun = await triggerFinetune(projectId, result.export_path);
          setFinetuneRun(ftRun);
          startFinetunePolling(ftRun.id);
        } catch (ftErr) {
          setFinetuneError(humanizeApiError(ftErr));
        } finally {
          setFinetuneTriggering(false);
        }
      }
    } catch (err) {
      setExportError(humanizeApiError(err));
    } finally {
      setExporting(false);
    }
  };

  const startFinetunePolling = (runId) => {
    if (finetunePollingRef.current) clearInterval(finetunePollingRef.current);
    finetunePollingRef.current = setInterval(async () => {
      try {
        const r = await fetchLatestFinetuneRun(projectId);
        setFinetuneRun(r);
        if (['succeeded', 'failed', 'cancelled'].includes(r.status)) {
          clearInterval(finetunePollingRef.current);
          finetunePollingRef.current = null;
        }
      } catch {
        clearInterval(finetunePollingRef.current);
        finetunePollingRef.current = null;
      }
    }, 5000);
  };

  useEffect(() => {
    return () => {
      if (finetunePollingRef.current) clearInterval(finetunePollingRef.current);
    };
  }, []);

  const startEmbeddingPolling = useCallback((runId) => {
    if (embeddingPollRef.current) clearInterval(embeddingPollRef.current);
    embeddingPollRef.current = setInterval(async () => {
      try {
        const r = await fetchEmbeddingRun(projectId, runId);
        setEmbeddingRun(r);
        if (['succeeded', 'failed'].includes(r.status)) {
          clearInterval(embeddingPollRef.current);
          embeddingPollRef.current = null;
          fetchProjectStats(projectId).then(setStats).catch(() => {});
        }
      } catch {
        if (embeddingPollRef.current) {
          clearInterval(embeddingPollRef.current);
          embeddingPollRef.current = null;
        }
      }
    }, 2500);
  }, [projectId]);

  useEffect(() => () => {
    if (embeddingPollRef.current) {
      clearInterval(embeddingPollRef.current);
      embeddingPollRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!project) return;
    fetchLatestEmbeddingRun(projectId)
      .then((r) => {
        if (r.status === 'running') {
          setEmbeddingRun(r);
          startEmbeddingPolling(r.id);
        }
      })
      .catch(() => {});
  }, [project, projectId, startEmbeddingPolling]);

  const handleGenerateEmbeddings = async () => {
    if (embeddingRun?.status === 'running') return;
    setEmbeddingError('');
    try {
      const run = await startEmbeddingRun(projectId, {});
      setEmbeddingRun(run);
      if (run.status === 'running') {
        startEmbeddingPolling(run.id);
      }
      fetchProjectStats(projectId).then(setStats).catch(() => {});
    } catch (e) {
      setEmbeddingError(humanizeApiError(e) || e?.response?.data?.detail || e.message || 'Embedding generation failed');
    }
  };

  const handleFindSimilar = async (sampleId, filename) => {
    try {
      const results = await fetchSimilarSamples(projectId, sampleId, galleryPageSize);
      setSimilarSamples(results);
      setSimilarMode({ sampleId, filename });
    } catch (e) {
      setEmbeddingError(e?.response?.data?.detail || 'Could not find similar samples. Generate embeddings first.');
    }
  };

  const clearSimilarMode = () => {
    setSimilarMode(null);
    setSimilarSamples([]);
  };

  const handlePropagateLabels = async () => {
    if (propagating) return;
    const msg = `Propagate labels from labeled samples to similar unlabeled ones (threshold: ${(propagateThreshold * 100).toFixed(0)}%)?\n\nThis creates draft annotations that you can review before accepting.`;
    if (!confirm(msg)) return;
    setPropagating(true);
    setPropagateError('');
    setPropagateResult(null);
    try {
      const result = await propagateLabels(projectId, {
        similarity_threshold: propagateThreshold,
      });
      setPropagateResult(result);
      const [st, detailed] = await Promise.all([
        fetchProjectStats(projectId),
        fetchDetailedProjectStats(projectId),
      ]);
      setStats(st);
      setDetailedStats(detailed);
      setGalleryPage(0);
    } catch (e) {
      setPropagateError(humanizeApiError(e) || e?.response?.data?.detail || 'Label propagation failed');
    } finally {
      setPropagating(false);
    }
  };

  const handleDetectDuplicates = async () => {
    setDupLoading(true);
    setDupError('');
    setDupGroups([]);
    setDupTotalDuplicates(0);
    try {
      const result = await detectNearDuplicates(projectId, { threshold: dupThreshold });
      setDupGroups(result.groups);
      setDupTotalDuplicates(result.total_duplicates);
      setDupMode(true);
    } catch (e) {
      setDupError(humanizeApiError(e) || e?.response?.data?.detail || 'Duplicate detection failed');
    } finally {
      setDupLoading(false);
    }
  };

  const clearDupMode = () => {
    setDupMode(false);
    setDupGroups([]);
    setDupTotalDuplicates(0);
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
      alert(humanizeApiError(err));
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
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '1rem' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.25rem' }}>
            <button
              onClick={() => navigate('/projects')}
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
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          <div className="dropdown-wrapper" ref={actionsRef}>
            <button
              className="btn-secondary"
              onClick={() => setActionsOpen(o => !o)}
              style={{ padding: '0.6rem 1rem', fontSize: '0.85rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}
            >
              Actions <span style={{ fontSize: '0.6rem', lineHeight: 1 }}>{actionsOpen ? '▲' : '▼'}</span>
            </button>
            {actionsOpen && (
              <div className="dropdown-menu">
                <button
                  className="dropdown-item"
                  onClick={() => { openExportModal(); setActionsOpen(false); }}
                  disabled={!stats || stats.labeled === 0}
                >
                  Export Dataset
                </button>
                <button
                  className="dropdown-item"
                  onClick={() => { handleClone(); setActionsOpen(false); }}
                  disabled={cloning}
                >
                  {cloning ? 'Creating…' : 'New Version'}
                </button>
                {stats && stats.pre_labeled > 0 && (
                  <>
                    <div className="dropdown-divider" />
                    <button
                      className="dropdown-item"
                      onClick={() => { handleAcceptAllDrafts(); setActionsOpen(false); }}
                      disabled={draftActionBusy}
                    >
                      Accept all drafts ({stats.pre_labeled})
                    </button>
                    <button
                      className="dropdown-item danger"
                      onClick={() => { handleClearAllDrafts(); setActionsOpen(false); }}
                      disabled={draftActionBusy}
                    >
                      Clear model drafts
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
          <button
            className="btn-primary"
            onClick={() => navigate(`/projects/${projectId}/label`)}
            style={{ padding: '0.6rem 1.5rem' }}
          >
            Start Labeling
          </button>
        </div>
      </div>

      {/* AI Tools panel */}
      {stats && (
        <div className="card" style={{ marginBottom: '1rem', padding: 0, overflow: 'hidden' }}>
          <div style={{ padding: '0.6rem 1rem', borderBottom: '1px solid var(--border-color)', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <span style={{ fontSize: '0.95rem', fontWeight: 700 }}>AI Tools</span>
            {endpointStatus && (
              <span style={{
                display: 'inline-flex', alignItems: 'center', gap: '0.3rem',
                fontSize: '0.7rem', color: 'var(--text-muted)',
                padding: '0.15rem 0.5rem', borderRadius: 99,
                background: endpointStatus.status === 'ready' ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)',
                border: `1px solid ${endpointStatus.status === 'ready' ? 'rgba(34,197,94,0.25)' : 'rgba(239,68,68,0.25)'}`,
              }}>
                <span style={{
                  width: 6, height: 6, borderRadius: '50%',
                  background: endpointStatus.status === 'ready' ? 'var(--status-success)'
                    : endpointStatus.status === 'not_ready' ? 'var(--status-warning)'
                    : endpointStatus.status === 'not_configured' ? 'var(--text-muted)'
                    : '#ef4444',
                }} />
                {endpointStatus.status === 'ready' ? endpointStatus.endpoint
                  : endpointStatus.status === 'not_ready' ? 'Endpoint updating'
                  : endpointStatus.status === 'not_configured' ? 'Not configured'
                  : 'Endpoint error'}
              </span>
            )}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: project.task_type === 'classification' ? '1fr 1fr 1fr' : '1fr 1fr', minHeight: 0 }}>
            {/* Pre-label section */}
            <div style={{ padding: '0.75rem 1rem', borderRight: '1px solid var(--border-color)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.5rem' }}>
                <span style={{ fontSize: '0.8rem', fontWeight: 600 }}>Pre-label</span>
                {stats && (
                  <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                    {eligibleCount > 0 ? `${eligibleCount} eligible` : 'none eligible'}
                  </span>
                )}
              </div>

              {endpointStatus?.status === 'ready' && eligibleCount > 0 ? (
                <>
                  <input
                    type="text"
                    value={preAnnotatePrompt}
                    onChange={(e) => setPreAnnotatePrompt(e.target.value)}
                    placeholder={project.class_list?.join('. ') || 'Detection prompt…'}
                    className="input"
                    style={{ padding: '0.35rem 0.6rem', fontSize: '0.8rem', width: '100%', marginBottom: '0.5rem' }}
                    title="Text prompt sent to the model. Leave blank to use class list."
                  />
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                    <button
                      className="btn-primary"
                      onClick={handlePreAnnotate}
                      disabled={preAnnotating || asyncPreAnnotating || Boolean(activeAsyncRunId)}
                      style={{ padding: '0.35rem 0.85rem', fontSize: '0.8rem', whiteSpace: 'nowrap' }}
                      title={willUseJob
                        ? `${eligibleCount} samples → runs as background Databricks Job`
                        : `${eligibleCount} samples → runs in-app with live progress`}
                    >
                      {preAnnotating ? 'Running…'
                        : asyncPreAnnotating ? 'Queueing…'
                        : activeAsyncRunId ? 'Job running…'
                        : willUseJob ? `Run Job (${eligibleCount})` : `Run (${eligibleCount})`}
                    </button>
                    <label style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.7rem', color: 'var(--text-muted)', userSelect: 'none', whiteSpace: 'nowrap' }}>
                      <input
                        type="checkbox"
                        checked={includePreLabeledInPreAnnotate}
                        onChange={(e) => setIncludePreLabeledInPreAnnotate(e.target.checked)}
                        style={{ width: 13, height: 13 }}
                      />
                      Re-run pre-labeled
                    </label>
                  </div>
                </>
              ) : (
                <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', padding: '0.5rem 0' }}>
                  {!endpointStatus || endpointStatus.status === 'not_configured'
                    ? 'Configure a serving endpoint to enable pre-labeling.'
                    : endpointStatus.status !== 'ready'
                      ? 'Waiting for endpoint to become ready…'
                      : 'All samples already labeled.'}
                </div>
              )}

              {/* Pre-label progress */}
              {preAnnotating && preAnnotateProgress && preAnnotateProgress.total > 0 && (
                <div style={{ marginTop: '0.5rem' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>
                    <span>Pre-labeling…</span>
                    <span>{preAnnotateProgress.current} / {preAnnotateProgress.total} ({Math.round((preAnnotateProgress.current / preAnnotateProgress.total) * 100)}%)</span>
                  </div>
                  <div className="progress-bar" style={{ height: 6 }}>
                    <div className="progress-fill" style={{ width: `${(preAnnotateProgress.current / preAnnotateProgress.total) * 100}%`, transition: 'width 0.2s ease' }} />
                  </div>
                  <div style={{ display: 'flex', gap: '0.75rem', marginTop: '0.25rem', fontSize: '0.65rem', color: 'var(--text-muted)' }}>
                    <span style={{ color: 'var(--status-success)' }}>{preAnnotateProgress.completed} done</span>
                    <span>{preAnnotateProgress.skipped} skipped</span>
                    {preAnnotateProgress.failed > 0 && <span style={{ color: '#ef4444' }}>{preAnnotateProgress.failed} failed</span>}
                  </div>
                </div>
              )}
              {preAnnotateResult && (
                <div style={{ marginTop: '0.5rem', padding: '0.35rem 0.5rem', borderRadius: 4, background: 'rgba(34,197,94,0.08)', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                  <span style={{ color: 'var(--status-success)', fontWeight: 600 }}>Done: </span>
                  {preAnnotateResult.completed} pre-labeled, {preAnnotateResult.skipped} skipped, {preAnnotateResult.failed} failed
                </div>
              )}
              {preAnnotateError && (
                <div style={{ marginTop: '0.5rem', padding: '0.35rem 0.5rem', borderRadius: 4, background: 'rgba(239,68,68,0.08)', fontSize: '0.75rem', color: '#ef4444' }}>
                  {preAnnotateError}
                </div>
              )}
              {asyncRunMessage && (
                <div style={{
                  marginTop: '0.5rem', padding: '0.35rem 0.5rem', borderRadius: 4, fontSize: '0.75rem',
                  background: asyncRunStatus === 'failed' || asyncRunStatus === 'cancelled' ? 'rgba(239,68,68,0.08)'
                    : asyncRunStatus === 'succeeded' ? 'rgba(34,197,94,0.08)' : 'rgba(59,130,246,0.06)',
                  color: asyncRunStatus === 'failed' || asyncRunStatus === 'cancelled' ? '#ef4444' : 'var(--text-secondary)',
                }}>
                  {asyncRunMessage}
                  {asyncDatabricksRunId && buildJobRunUrl(asyncDatabricksRunId) && (
                    <a href={buildJobRunUrl(asyncDatabricksRunId)} target="_blank" rel="noopener noreferrer"
                      style={{ marginLeft: '0.4rem', color: 'var(--accent-blue)', textDecoration: 'underline', fontSize: '0.7rem' }}>
                      View Job ↗
                    </a>
                  )}
                </div>
              )}
            </div>

            {/* Embeddings section */}
            <div style={{ padding: '0.75rem 1rem', ...(project.task_type === 'classification' ? { borderRight: '1px solid var(--border-color)' } : {}) }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.5rem' }}>
                <span style={{ fontSize: '0.8rem', fontWeight: 600 }}>Embeddings</span>
                {stats.embedded > 0 && (
                  <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                    {stats.embedded} / {stats.total} embedded
                  </span>
                )}
              </div>

              {stats.total > 0 ? (
                <>
                  {stats.embedded > 0 && stats.total > 0 && (
                    <div className="progress-bar" style={{ height: 4, marginBottom: '0.5rem' }}>
                      <div className="progress-fill" style={{ width: `${(stats.embedded / stats.total) * 100}%`, background: '#60a5fa' }} />
                    </div>
                  )}
                  <button
                    className="btn-primary"
                    onClick={handleGenerateEmbeddings}
                    disabled={embeddingRun?.status === 'running'}
                    style={{ padding: '0.35rem 0.85rem', fontSize: '0.8rem' }}
                  >
                    {embeddingRun?.status === 'running' ? 'Generating…'
                      : stats.embedded >= stats.total ? 'Regenerate Embeddings'
                      : stats.embedded > 0 ? `Resume (${stats.total - stats.embedded} remaining)`
                      : 'Generate Embeddings'}
                  </button>
                  <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.35rem' }}>
                    {stats.embedded > 0
                      ? 'Enables "Find Similar" on each sample in the gallery below.'
                      : 'Build a vector index with DINOv3 to enable visual similarity search.'}
                  </div>
                </>
              ) : (
                <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', padding: '0.5rem 0' }}>
                  No samples to embed.
                </div>
              )}

              {/* Embedding progress */}
              {embeddingRun?.status === 'running' && embeddingRun.total_planned > 0 && (() => {
                const done = embeddingRun.completed + embeddingRun.failed + embeddingRun.skipped;
                const pct = Math.round((done / embeddingRun.total_planned) * 100);
                return (
                  <div style={{ marginTop: '0.5rem' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>
                      <span>Generating…</span>
                      <span>{done} / {embeddingRun.total_planned} ({pct}%)</span>
                    </div>
                    <div className="progress-bar" style={{ height: 6 }}>
                      <div className="progress-fill" style={{ width: `${(done / embeddingRun.total_planned) * 100}%`, transition: 'width 0.2s ease', background: '#60a5fa' }} />
                    </div>
                    <div style={{ display: 'flex', gap: '0.75rem', marginTop: '0.25rem', fontSize: '0.65rem', color: 'var(--text-muted)' }}>
                      <span style={{ color: 'var(--status-success)' }}>{embeddingRun.completed} done</span>
                      <span>{embeddingRun.skipped} skipped</span>
                      {embeddingRun.failed > 0 && <span style={{ color: '#ef4444' }}>{embeddingRun.failed} failed</span>}
                    </div>
                  </div>
                );
              })()}
              {embeddingRun?.status === 'succeeded' && (
                <div style={{ marginTop: '0.5rem', padding: '0.35rem 0.5rem', borderRadius: 4, background: 'rgba(34,197,94,0.08)', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                  <span style={{ color: 'var(--status-success)', fontWeight: 600 }}>Done: </span>
                  {embeddingRun.completed} new, {embeddingRun.skipped} skipped, {embeddingRun.failed} failed
                </div>
              )}
              {embeddingRun?.status === 'failed' && (
                <div style={{ marginTop: '0.5rem', padding: '0.35rem 0.5rem', borderRadius: 4, background: 'rgba(239,68,68,0.08)', fontSize: '0.75rem', color: '#ef4444' }}>
                  {embeddingRun.error_message || 'Embedding generation failed.'}
                </div>
              )}
              {embeddingError && (
                <div style={{ marginTop: '0.5rem', padding: '0.35rem 0.5rem', borderRadius: 4, background: 'rgba(239,68,68,0.08)', fontSize: '0.75rem', color: '#ef4444' }}>
                  {embeddingError}
                </div>
              )}
            </div>

            {/* Label Propagation section — classification only */}
            {project.task_type === 'classification' && <div style={{ padding: '0.75rem 1rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.5rem' }}>
                <span style={{ fontSize: '0.8rem', fontWeight: 600 }}>Propagate Labels</span>
                {stats.embedded > 0 && stats.labeled > 0 && stats.unlabeled > 0 && (
                  <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                    {stats.labeled} → {stats.unlabeled}
                  </span>
                )}
              </div>

              {stats.embedded > 0 && stats.labeled > 0 && stats.unlabeled > 0 ? (
                <>
                  <div style={{ marginBottom: '0.5rem' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.2rem' }}>
                      <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Threshold</span>
                      <span style={{ fontSize: '0.7rem', fontWeight: 600, color: 'var(--text-secondary)' }}>
                        {(propagateThreshold * 100).toFixed(0)}%
                      </span>
                    </div>
                    <input
                      type="range"
                      min="0.50"
                      max="0.99"
                      step="0.01"
                      value={propagateThreshold}
                      onChange={(e) => setPropagateThreshold(parseFloat(e.target.value))}
                      style={{ width: '100%', accentColor: '#a78bfa' }}
                    />
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.6rem', color: 'var(--text-muted)' }}>
                      <span>More matches</span>
                      <span>Higher accuracy</span>
                    </div>
                  </div>
                  <button
                    className="btn-primary"
                    onClick={handlePropagateLabels}
                    disabled={propagating}
                    style={{ padding: '0.35rem 0.85rem', fontSize: '0.8rem', background: '#7c3aed' }}
                  >
                    {propagating ? 'Propagating…' : 'Propagate'}
                  </button>
                  <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.35rem' }}>
                    Copy labels from labeled samples to visually similar unlabeled ones as drafts.
                  </div>
                </>
              ) : (
                <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', padding: '0.5rem 0' }}>
                  {stats.embedded === 0
                    ? 'Generate embeddings first to enable label propagation.'
                    : stats.labeled === 0
                      ? 'Label some samples first — propagation copies existing labels.'
                      : 'All samples are already labeled.'}
                </div>
              )}

              {propagateResult && (
                <div style={{ marginTop: '0.5rem', padding: '0.35rem 0.5rem', borderRadius: 4, background: 'rgba(124,58,237,0.08)', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                  <span style={{ color: '#a78bfa', fontWeight: 600 }}>Done: </span>
                  {propagateResult.propagated} samples pre-labeled, {propagateResult.skipped} below threshold
                </div>
              )}
              {propagateError && (
                <div style={{ marginTop: '0.5rem', padding: '0.35rem 0.5rem', borderRadius: 4, background: 'rgba(239,68,68,0.08)', fontSize: '0.75rem', color: '#ef4444' }}>
                  {propagateError}
                </div>
              )}
            </div>}
          </div>

          {/* Draft actions row */}
          {draftActionMessage && (
            <div style={{
              padding: '0.4rem 1rem', borderTop: '1px solid var(--border-color)',
              fontSize: '0.75rem', color: 'var(--text-secondary)', background: 'rgba(59,130,246,0.04)',
            }}>
              {draftActionMessage}
            </div>
          )}
        </div>
      )}

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

          {finetuneConfigured && !exportResult && (
            <label style={{
              display: 'flex', alignItems: 'center', gap: '0.5rem',
              fontSize: '0.85rem', color: 'var(--text-secondary)',
              marginBottom: '0.75rem', cursor: 'pointer',
            }}>
              <input
                type="checkbox"
                checked={triggerFinetuneAfterExport}
                onChange={(e) => setTriggerFinetuneAfterExport(e.target.checked)}
                disabled={exporting}
              />
              Trigger finetuning job after export
            </label>
          )}

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

          {finetuneError && (
            <div style={{
              padding: '0.5rem 0.75rem',
              borderRadius: 4,
              background: 'rgba(239, 68, 68, 0.1)',
              border: '1px solid rgba(239, 68, 68, 0.3)',
              color: '#ef4444',
              fontSize: '0.8rem',
              marginBottom: '0.75rem',
            }}>
              Finetuning trigger failed: {finetuneError}
            </div>
          )}

          {finetuneRun && (
            <div style={{
              padding: '0.75rem',
              borderRadius: 4,
              background: finetuneRun.status === 'failed'
                ? 'rgba(239, 68, 68, 0.1)'
                : finetuneRun.status === 'succeeded'
                  ? 'rgba(34, 197, 94, 0.1)'
                  : 'rgba(59, 130, 246, 0.1)',
              border: `1px solid ${
                finetuneRun.status === 'failed'
                  ? 'rgba(239, 68, 68, 0.3)'
                  : finetuneRun.status === 'succeeded'
                    ? 'rgba(34, 197, 94, 0.3)'
                    : 'rgba(59, 130, 246, 0.3)'
              }`,
              fontSize: '0.8rem',
              marginBottom: '0.75rem',
            }}>
              <div style={{
                fontWeight: 600, marginBottom: '0.3rem',
                color: finetuneRun.status === 'failed' ? '#ef4444'
                  : finetuneRun.status === 'succeeded' ? 'var(--status-success)'
                    : 'var(--accent-blue)',
              }}>
                Finetuning: {finetuneRun.status}
                {['queued', 'pending', 'running'].includes(finetuneRun.status) && (
                  <span style={{ fontWeight: 400, marginLeft: '0.5rem' }}>
                    (polling for updates...)
                  </span>
                )}
              </div>
              {finetuneRun.databricks_run_id && (
                <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem' }}>
                  Databricks Run ID: {finetuneRun.databricks_run_id}
                </div>
              )}
              {finetuneRun.error_message && (
                <div style={{ color: '#ef4444', marginTop: '0.3rem', fontSize: '0.75rem' }}>
                  {finetuneRun.error_message.slice(0, 300)}
                </div>
              )}
            </div>
          )}

          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button
              className="btn-primary"
              onClick={handleExport}
              disabled={exporting || finetuneTriggering || !exportVolume.trim() || !!exportResult}
              style={{ padding: '0.5rem 1.25rem', fontSize: '0.85rem' }}
            >
              {exporting ? 'Exporting...' : finetuneTriggering ? 'Triggering finetuning...' : 'Export'}
            </button>
            {exportResult && finetuneConfigured && !finetuneRun && !finetuneError && (
              <button
                className="btn-primary"
                onClick={async () => {
                  setFinetuneTriggering(true);
                  try {
                    const ftRun = await triggerFinetune(projectId, exportResult.export_path);
                    setFinetuneRun(ftRun);
                    startFinetunePolling(ftRun.id);
                  } catch (ftErr) {
                    setFinetuneError(humanizeApiError(ftErr));
                  } finally {
                    setFinetuneTriggering(false);
                  }
                }}
                disabled={finetuneTriggering}
                style={{ padding: '0.5rem 1.25rem', fontSize: '0.85rem' }}
              >
                {finetuneTriggering ? 'Triggering...' : 'Trigger Finetuning'}
              </button>
            )}
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
              ...(stats.embedded > 0 ? [{ label: 'Embedded', value: stats.embedded, color: '#60a5fa' }] : []),
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
                {similarMode
                  ? <>Similar to <span style={{ color: 'var(--accent-blue)' }}>{similarMode.filename}</span></>
                  : dupMode
                    ? <>Near Duplicates <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem', fontWeight: 400 }}>({dupGroups.length} groups, {dupTotalDuplicates} duplicates)</span></>
                    : 'Samples'}
              </h3>
              {similarMode ? (
                <button
                  className="btn-secondary"
                  onClick={clearSimilarMode}
                  style={{ padding: '0.25rem 0.6rem', fontSize: '0.75rem' }}
                >
                  Back to gallery
                </button>
              ) : dupMode ? (
                <button
                  className="btn-secondary"
                  onClick={clearDupMode}
                  style={{ padding: '0.25rem 0.6rem', fontSize: '0.75rem' }}
                >
                  Back to gallery
                </button>
              ) : (
              <div style={{ display: 'flex', gap: '0.3rem', alignItems: 'center' }}>
                {stats?.embedded > 0 && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', marginRight: '0.5rem' }}>
                    <button
                      className="btn-secondary"
                      onClick={handleDetectDuplicates}
                      disabled={dupLoading}
                      style={{
                        padding: '0.25rem 0.6rem',
                        fontSize: '0.75rem',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '0.3rem',
                        border: '1px solid rgba(249,115,22,0.3)',
                        color: '#f97316',
                      }}
                    >
                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                        <rect x="2" y="2" width="8" height="8" rx="1" />
                        <rect x="14" y="2" width="8" height="8" rx="1" />
                        <rect x="8" y="14" width="8" height="8" rx="1" />
                      </svg>
                      {dupLoading ? 'Scanning…' : 'Find Duplicates'}
                    </button>
                  </div>
                )}
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
              )}
            </div>

            {/* Duplicate threshold control */}
            {dupMode && (
              <div style={{
                display: 'flex', alignItems: 'center', gap: '0.75rem',
                marginBottom: '0.75rem', padding: '0.5rem 0.75rem',
                background: 'rgba(249,115,22,0.04)', borderRadius: 6,
                border: '1px solid rgba(249,115,22,0.15)',
              }}>
                <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
                  Similarity threshold:
                </span>
                <input
                  type="range"
                  min="0.80"
                  max="0.99"
                  step="0.01"
                  value={dupThreshold}
                  onChange={(e) => setDupThreshold(parseFloat(e.target.value))}
                  style={{ flex: 1, maxWidth: 200, accentColor: '#f97316' }}
                />
                <span style={{ fontSize: '0.75rem', fontWeight: 600, color: '#f97316', minWidth: 36 }}>
                  {(dupThreshold * 100).toFixed(0)}%
                </span>
                <button
                  className="btn-secondary"
                  onClick={handleDetectDuplicates}
                  disabled={dupLoading}
                  style={{ padding: '0.2rem 0.6rem', fontSize: '0.7rem' }}
                >
                  {dupLoading ? 'Scanning…' : 'Refresh'}
                </button>
              </div>
            )}

            {dupError && (
              <div style={{ marginBottom: '0.75rem', padding: '0.35rem 0.5rem', borderRadius: 4, background: 'rgba(239,68,68,0.08)', fontSize: '0.75rem', color: '#ef4444' }}>
                {dupError}
              </div>
            )}

            {/* Search & filter bar */}
            {!similarMode && !dupMode && <div style={{
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
            </div>}

            {similarMode ? (
              similarSamples.length === 0 ? (
                <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem', padding: '1rem 0', textAlign: 'center' }}>
                  No similar samples found
                </div>
              ) : (
                <div style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))',
                  gap: '0.5rem',
                }}>
                  {similarSamples.map((s) => (
                    <div
                      key={s.sample_id}
                      onClick={() => navigate(`/projects/${projectId}/label?sample=${s.sample_id}`)}
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
                          src={sampleThumbnailUrl(projectId, s.sample_id, 200)}
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
                          top: 4, left: 4,
                          padding: '0.1rem 0.35rem',
                          borderRadius: 3,
                          fontSize: '0.6rem',
                          fontWeight: 600,
                          background: 'rgba(59,130,246,0.85)',
                          color: '#fff',
                        }}>
                          {(s.similarity * 100).toFixed(1)}%
                        </span>
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
                      </div>
                    </div>
                  ))}
                </div>
              )
            ) : dupMode ? (
              dupGroups.length === 0 ? (
                <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem', padding: '1rem 0', textAlign: 'center' }}>
                  No near-duplicates found at {(dupThreshold * 100).toFixed(0)}% threshold. Try lowering it.
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                  {dupGroups.map((group) => (
                    <div
                      key={group.representative_id}
                      style={{
                        padding: '0.75rem',
                        borderRadius: 8,
                        border: '1px solid rgba(249,115,22,0.2)',
                        background: 'rgba(249,115,22,0.02)',
                      }}
                    >
                      <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '0.5rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                        <span style={{
                          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                          width: 20, height: 20, borderRadius: '50%',
                          background: 'rgba(249,115,22,0.15)', color: '#f97316',
                          fontSize: '0.65rem', fontWeight: 700,
                        }}>
                          {group.members.length + 1}
                        </span>
                        <span style={{ fontWeight: 600 }}>Group</span>
                        <span style={{ color: 'var(--text-muted)' }}>— {group.members.length} duplicate{group.members.length !== 1 ? 's' : ''}</span>
                      </div>
                      <div style={{
                        display: 'grid',
                        gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))',
                        gap: '0.4rem',
                      }}>
                        {/* Representative */}
                        <div
                          onClick={() => navigate(`/projects/${projectId}/label?sample=${group.representative_id}`)}
                          style={{
                            cursor: 'pointer',
                            borderRadius: 6,
                            border: '2px solid #f97316',
                            overflow: 'hidden',
                            background: 'var(--bg-secondary)',
                          }}
                        >
                          <div style={{ position: 'relative', paddingTop: '100%' }}>
                            <img
                              src={sampleThumbnailUrl(projectId, group.representative_id, 200)}
                              alt={group.representative_filename}
                              loading="lazy"
                              style={{
                                position: 'absolute', top: 0, left: 0,
                                width: '100%', height: '100%', objectFit: 'cover',
                              }}
                            />
                            <span style={{
                              position: 'absolute', top: 4, left: 4,
                              padding: '0.1rem 0.35rem', borderRadius: 3,
                              fontSize: '0.55rem', fontWeight: 700,
                              background: '#f97316', color: '#fff',
                            }}>
                              ORIGINAL
                            </span>
                          </div>
                          <div style={{ padding: '0.25rem 0.35rem' }}>
                            <div style={{
                              fontSize: '0.6rem', color: 'var(--text-secondary)',
                              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                            }}>
                              {group.representative_filename}
                            </div>
                          </div>
                        </div>
                        {/* Duplicate members */}
                        {group.members.map((m) => (
                          <div
                            key={m.sample_id}
                            onClick={() => navigate(`/projects/${projectId}/label?sample=${m.sample_id}`)}
                            style={{
                              cursor: 'pointer',
                              borderRadius: 6,
                              border: '1px solid var(--border-color)',
                              overflow: 'hidden',
                              background: 'var(--bg-secondary)',
                              transition: 'border-color 0.15s',
                            }}
                            onMouseEnter={(e) => e.currentTarget.style.borderColor = '#f97316'}
                            onMouseLeave={(e) => e.currentTarget.style.borderColor = 'var(--border-color)'}
                          >
                            <div style={{ position: 'relative', paddingTop: '100%' }}>
                              <img
                                src={sampleThumbnailUrl(projectId, m.sample_id, 200)}
                                alt={m.filename}
                                loading="lazy"
                                style={{
                                  position: 'absolute', top: 0, left: 0,
                                  width: '100%', height: '100%', objectFit: 'cover',
                                }}
                              />
                              <span style={{
                                position: 'absolute', top: 4, left: 4,
                                padding: '0.1rem 0.35rem', borderRadius: 3,
                                fontSize: '0.6rem', fontWeight: 600,
                                background: 'rgba(249,115,22,0.85)', color: '#fff',
                              }}>
                                {(m.similarity * 100).toFixed(1)}%
                              </span>
                              <span style={{
                                position: 'absolute', top: 4, right: 4,
                                padding: '0.1rem 0.35rem', borderRadius: 3,
                                fontSize: '0.6rem', fontWeight: 600,
                                background: m.status === 'labeled' ? 'var(--status-success)'
                                  : m.status === 'pre_labeled' ? '#a78bfa'
                                  : m.status === 'skipped' ? 'var(--status-warning)'
                                  : 'rgba(255,255,255,0.15)',
                                color: m.status === 'unlabeled' ? 'var(--text-muted)' : '#fff',
                              }}>
                                {m.status}
                              </span>
                            </div>
                            <div style={{ padding: '0.25rem 0.35rem' }}>
                              <div style={{
                                fontSize: '0.6rem', color: 'var(--text-secondary)',
                                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                              }}>
                                {m.filename}
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )
            ) : gallerySamples.length === 0 ? (
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
                    style={{
                      borderRadius: 6,
                      border: '1px solid var(--border-color)',
                      overflow: 'hidden',
                      background: 'var(--bg-secondary)',
                      transition: 'border-color 0.15s',
                    }}
                    onMouseEnter={(e) => e.currentTarget.style.borderColor = 'var(--accent-blue)'}
                    onMouseLeave={(e) => e.currentTarget.style.borderColor = 'var(--border-color)'}
                  >
                    <div
                      className="sample-thumb-wrap"
                      onClick={() => navigate(`/projects/${projectId}/label?sample=${s.id}`)}
                      style={{ cursor: 'pointer', position: 'relative', paddingTop: '100%' }}
                    >
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
                      {stats?.embedded > 0 && (
                        <button
                          className="find-similar-overlay"
                          onClick={(e) => { e.stopPropagation(); handleFindSimilar(s.id, s.filename); }}
                          style={{
                            position: 'absolute', bottom: 4, left: 4,
                            display: 'flex', alignItems: 'center', gap: '0.25rem',
                            padding: '0.2rem 0.5rem',
                            borderRadius: 4,
                            border: 'none',
                            background: 'rgba(0,0,0,0.7)',
                            color: '#fff',
                            fontSize: '0.65rem',
                            fontWeight: 600,
                            cursor: 'pointer',
                            opacity: 0,
                            transition: 'opacity 0.15s',
                          }}
                        >
                          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                            <circle cx="11" cy="11" r="8" />
                            <path d="M21 21l-4.35-4.35" />
                          </svg>
                          Find Similar
                        </button>
                      )}
                    </div>
                    <div style={{ padding: '0.3rem 0.4rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <div style={{
                        fontSize: '0.65rem',
                        color: 'var(--text-secondary)',
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        flex: 1,
                      }}>
                        {s.filename}
                      </div>
                      {stats?.embedded > 0 && (
                        <button
                          onClick={(e) => { e.stopPropagation(); handleFindSimilar(s.id, s.filename); }}
                          title="Find similar"
                          style={{
                            background: 'none', border: 'none', cursor: 'pointer',
                            padding: '0 0.15rem', color: 'var(--text-muted)',
                            fontSize: '0.7rem', flexShrink: 0,
                          }}
                        >
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <circle cx="11" cy="11" r="8" />
                            <path d="M21 21l-4.35-4.35" />
                          </svg>
                        </button>
                      )}
                    </div>
                    {s.labels && s.labels.length > 0 && (
                      <div style={{ padding: '0 0.4rem 0.3rem', display: 'flex', flexWrap: 'wrap', gap: '0.15rem' }}>
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
                ))}
              </div>
            )}

            {/* Pagination */}
            {!similarMode && !dupMode && galleryTotalPages > 1 && (
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
