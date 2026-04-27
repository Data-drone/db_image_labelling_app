/**
 * Labeling View — project-centric annotation interface.
 * 3-zone layout: top bar, center image (75%), right panel (25%).
 * Supports classification (numbered buttons) and detection (bbox canvas).
 * Sample scrubber: navigate back/forth through all samples.
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import Spinner from '../components/Spinner';
import BBoxCanvas, { getClassColor } from '../components/BBoxCanvas';
import {
  fetchProject,
  fetchProjectStats,
  fetchSamples,
  fetchSample,
  fetchNextSample,
  annotateSampleBatch,
  skipSample,
  sampleImageUrl,
  addProjectClass,
  fetchSampleHistory,
} from '../api/client';

export default function LabelingView() {
  const { id: projectId } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const [project, setProject] = useState(null);
  const [stats, setStats] = useState(null);
  const [sample, setSample] = useState(null);
  const [loading, setLoading] = useState(true);
  const [imageLoaded, setImageLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [newClassName, setNewClassName] = useState('');
  const [addingClass, setAddingClass] = useState(false);

  // Sample navigation
  const [sampleList, setSampleList] = useState([]); // [{id, status}, ...]
  const [currentIndex, setCurrentIndex] = useState(-1);

  // Detection mode state
  const [boxes, setBoxes] = useState([]);
  const [selectedBoxId, setSelectedBoxId] = useState(null);
  const [activeClassIndex, setActiveClassIndex] = useState(0);
  const nextBoxId = useRef(0);

  // Undo stack for detection box operations (max 20)
  const undoStack = useRef([]);
  const MAX_UNDO = 20;

  // Multi-label classification state
  const [selectedLabels, setSelectedLabels] = useState(new Set());

  // Flash feedback for keyboard class selection
  const [flashIndex, setFlashIndex] = useState(null);
  const flashTimeout = useRef(null);

  // History panel
  const [history, setHistory] = useState([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);

  const isDetection = project?.task_type === 'detection';
  const total = sampleList.length;

  // Load project info
  useEffect(() => {
    fetchProject(projectId)
      .then(setProject)
      .catch(() => navigate('/'));
  }, [projectId, navigate]);

  // Load stats
  const loadStats = useCallback(() => {
    fetchProjectStats(projectId).then(setStats).catch(console.error);
  }, [projectId]);

  useEffect(() => { loadStats(); }, [loadStats]);

  // Load full sample list (IDs + statuses) on mount
  useEffect(() => {
    if (!project) return;
    fetchSamples(projectId, { page: 0, page_size: 10000 })
      .then((page) => {
        const list = page.items.map(s => ({ id: s.id, status: s.status }));
        setSampleList(list);
      })
      .catch(console.error);
  }, [project, projectId]);

  // Once sample list is loaded, navigate to initial sample
  useEffect(() => {
    if (sampleList.length === 0) return;

    // Check for ?sample=ID in URL
    const sampleParam = searchParams.get('sample');
    if (sampleParam) {
      const idx = sampleList.findIndex(s => s.id === parseInt(sampleParam));
      if (idx >= 0) {
        setCurrentIndex(idx);
        return;
      }
    }

    // Otherwise find first unlabeled
    const unlabeledIdx = sampleList.findIndex(s => s.status === 'unlabeled');
    if (unlabeledIdx >= 0) {
      setCurrentIndex(unlabeledIdx);
    } else {
      // All labeled — start at first sample
      setCurrentIndex(0);
    }
  }, [sampleList]); // Only run when sampleList first loads

  // Load sample when currentIndex changes
  const loadSampleAtIndex = useCallback(async (idx) => {
    if (idx < 0 || idx >= sampleList.length) return;
    setLoading(true);
    setImageLoaded(false);
    try {
      const s = await fetchSample(projectId, sampleList[idx].id);
      setSample(s);

      // Load existing annotations for re-labeling
      if (s.annotations && s.annotations.length > 0) {
        const existingBoxes = s.annotations
          .filter(a => a.ann_type === 'bbox' && a.bbox_json)
          .map(a => ({
            id: `existing-${nextBoxId.current++}`,
            label: a.label,
            classIndex: Math.max(0, (project?.class_list || []).indexOf(a.label)),
            ...a.bbox_json,
          }));
        setBoxes(existingBoxes);

        const existingLabels = new Set(
          s.annotations
            .filter(a => a.ann_type === 'classification')
            .map(a => a.label)
        );
        setSelectedLabels(existingLabels);
      } else {
        setBoxes([]);
        setSelectedLabels(new Set());
      }
      setSelectedBoxId(null);
      undoStack.current = [];
    } catch (err) {
      console.error('Failed to load sample:', err);
    } finally {
      setLoading(false);
    }
  }, [sampleList, projectId, project]);

  useEffect(() => {
    if (currentIndex >= 0 && sampleList.length > 0) {
      loadSampleAtIndex(currentIndex);
    }
  }, [currentIndex, loadSampleAtIndex]);

  // Load history when sample changes or panel is opened
  const loadHistory = useCallback(async () => {
    if (!sample) return;
    const requestedId = sample.id;
    setHistoryLoading(true);
    try {
      const h = await fetchSampleHistory(projectId, requestedId);
      setSample(cur => {
        if (cur?.id === requestedId) setHistory(h);
        return cur;
      });
    } catch {
      setSample(cur => {
        if (cur?.id === requestedId) setHistory([]);
        return cur;
      });
    } finally {
      setHistoryLoading(false);
    }
  }, [projectId, sample]);

  useEffect(() => {
    if (historyOpen && sample) {
      loadHistory();
    }
  }, [historyOpen, sample, loadHistory]);

  // Navigation
  const goTo = (idx) => {
    if (idx >= 0 && idx < sampleList.length && idx !== currentIndex) {
      setCurrentIndex(idx);
    }
  };

  const goPrev = () => goTo(currentIndex - 1);
  const goNext = () => goTo(currentIndex + 1);

  const goNextUnlabeled = () => {
    // Find next unlabeled from current position (wrapping)
    for (let i = 1; i <= sampleList.length; i++) {
      const idx = (currentIndex + i) % sampleList.length;
      if (sampleList[idx].status === 'unlabeled') {
        goTo(idx);
        return;
      }
    }
    // All done — stay on current
  };

  // After annotating, update local sample list status and advance
  const markCurrentAndAdvance = useCallback(() => {
    setSampleList(prev => prev.map((s, i) =>
      i === currentIndex ? { ...s, status: 'labeled' } : s
    ));
    loadStats();
    if (historyOpen) loadHistory();
    // Go to next unlabeled
    const nextUnlabeled = sampleList.findIndex((s, i) =>
      i > currentIndex && s.status === 'unlabeled'
    );
    if (nextUnlabeled >= 0) {
      goTo(nextUnlabeled);
    } else {
      const wrapped = sampleList.findIndex(s => s.status === 'unlabeled');
      if (wrapped >= 0 && wrapped !== currentIndex) {
        goTo(wrapped);
      } else if (currentIndex < sampleList.length - 1) {
        goTo(currentIndex + 1);
      }
    }
  }, [currentIndex, sampleList, loadStats, historyOpen, loadHistory]);

  // Multi-label classification: toggle a label on/off
  const toggleLabel = useCallback((label) => {
    setSelectedLabels(prev => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  }, []);

  // Multi-label classification: save all selected labels and advance
  const handleSaveClassification = useCallback(async () => {
    if (!sample || saving || selectedLabels.size === 0) return;
    setSaving(true);
    try {
      const annotations = [...selectedLabels].map(label => ({
        label,
        ann_type: 'classification',
      }));
      await annotateSampleBatch(projectId, sample.id, annotations);
      markCurrentAndAdvance();
    } catch (err) {
      console.error('Annotation failed:', err);
    } finally {
      setSaving(false);
    }
  }, [sample, saving, selectedLabels, projectId, markCurrentAndAdvance]);

  // Skip
  const handleSkip = async () => {
    if (!sample || saving) return;
    setSaving(true);
    try {
      await skipSample(projectId, sample.id);
      setSampleList(prev => prev.map((s, i) =>
        i === currentIndex ? { ...s, status: 'skipped' } : s
      ));
      loadStats();
      goNext();
    } catch (err) {
      console.error('Skip failed:', err);
    } finally {
      setSaving(false);
    }
  };

  // Add new class
  const handleAddClass = async () => {
    const trimmed = newClassName.trim();
    if (!trimmed || addingClass) return;
    if (project.class_list.includes(trimmed)) {
      setNewClassName('');
      return;
    }
    setAddingClass(true);
    try {
      const result = await addProjectClass(projectId, trimmed);
      setProject({ ...project, class_list: result.class_list });
      setNewClassName('');
    } catch (err) {
      console.error('Failed to add class:', err);
    } finally {
      setAddingClass(false);
    }
  };

  // Detection: box CRUD
  const handleBoxCreated = (rect) => {
    const id = `box-${nextBoxId.current++}`;
    const newBox = {
      id,
      label: project.class_list[activeClassIndex] || '',
      classIndex: activeClassIndex,
      ...rect,
    };
    setBoxes(prev => [...prev, newBox]);
    setSelectedBoxId(id);
    undoStack.current = [
      ...undoStack.current.slice(-MAX_UNDO + 1),
      { action: 'create', box: newBox },
    ];
  };

  const handleBoxUpdated = (id, updates) => {
    setBoxes(prev => prev.map(b => b.id === id ? { ...b, ...updates } : b));
  };

  const handleBoxDeleted = useCallback((id) => {
    const box = boxes.find(b => b.id === id);
    if (box) {
      undoStack.current = [
        ...undoStack.current.slice(-MAX_UNDO + 1),
        { action: 'delete', box },
      ];
    }
    setBoxes(prev => prev.filter(b => b.id !== id));
    setSelectedBoxId(prev => prev === id ? null : prev);
  }, [boxes]);

  const handleUndo = useCallback(() => {
    const entry = undoStack.current.pop();
    if (!entry) return;
    if (entry.action === 'create') {
      setBoxes(prev => prev.filter(b => b.id !== entry.box.id));
      setSelectedBoxId(prev => prev === entry.box.id ? null : prev);
    } else if (entry.action === 'delete') {
      setBoxes(prev => [...prev, entry.box]);
      setSelectedBoxId(entry.box.id);
    }
  }, []);

  const cycleSelectedBox = useCallback((reverse) => {
    if (boxes.length === 0) return;
    if (!selectedBoxId) {
      setSelectedBoxId(reverse ? boxes[boxes.length - 1].id : boxes[0].id);
      return;
    }
    const idx = boxes.findIndex(b => b.id === selectedBoxId);
    const next = reverse
      ? (idx - 1 + boxes.length) % boxes.length
      : (idx + 1) % boxes.length;
    setSelectedBoxId(boxes[next].id);
  }, [boxes, selectedBoxId]);

  // Detection: Save & Next
  const handleSaveBoxes = async () => {
    if (!sample || saving || boxes.length === 0) return;
    setSaving(true);
    try {
      const annotations = boxes.map(b => ({
        label: b.label,
        ann_type: 'bbox',
        bbox_json: { x: b.x, y: b.y, w: b.w, h: b.h },
      }));
      await annotateSampleBatch(projectId, sample.id, annotations);
      markCurrentAndAdvance();
    } catch (err) {
      console.error('Save failed:', err);
    } finally {
      setSaving(false);
    }
  };

  // Keyboard shortcuts
  useEffect(() => {
    if (!project || !sample) return;

    const handler = (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

      const isMod = e.ctrlKey || e.metaKey;

      // Ctrl/Cmd+Z (without Shift): undo last box operation (detection only)
      if (isMod && e.key === 'z' && !e.shiftKey && isDetection) {
        e.preventDefault();
        handleUndo();
        return;
      }

      // [ / ]: cycle box selection (detection only)
      if ((e.key === '[' || e.key === ']') && isDetection) {
        e.preventDefault();
        cycleSelectedBox(e.key === '[');
        return;
      }

      // Arrow keys: navigate samples
      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        goPrev();
        return;
      }
      if (e.key === 'ArrowRight') {
        e.preventDefault();
        goNext();
        return;
      }

      // Number keys 1-9: class selection (detection) or label toggle (classification)
      if (e.key >= '1' && e.key <= '9') {
        const idx = parseInt(e.key) - 1;
        if (idx < project.class_list.length) {
          if (flashTimeout.current) clearTimeout(flashTimeout.current);
          setFlashIndex(idx);
          flashTimeout.current = setTimeout(() => setFlashIndex(null), 250);

          if (isDetection) {
            setActiveClassIndex(idx);
          } else {
            toggleLabel(project.class_list[idx]);
          }
        }
        return;
      }

      // N: jump to next unlabeled sample
      if ((e.key === 'n' || e.key === 'N') && !isMod) {
        e.preventDefault();
        goNextUnlabeled();
        return;
      }

      if (e.key === 's' || e.key === 'S') {
        e.preventDefault();
        handleSkip();
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (isDetection) {
          handleSaveBoxes();
        } else {
          handleSaveClassification();
        }
      } else if (e.key === 'Escape') {
        if (isDetection && selectedBoxId) {
          setSelectedBoxId(null);
        } else {
          navigate(`/projects/${projectId}`);
        }
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [project, sample, saving, projectId, navigate, isDetection, selectedBoxId, boxes, currentIndex, sampleList, handleUndo, cycleSelectedBox, selectedLabels, handleSaveClassification, toggleLabel]);

  const labeled = stats?.labeled || 0;
  const progressPct = total > 0 ? Math.round((labeled / total) * 100) : 0;
  const currentStatus = currentIndex >= 0 && currentIndex < sampleList.length
    ? sampleList[currentIndex].status : '';

  if (!project) return <Spinner label="Loading project..." />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 4rem)' }}>
      {/* Top bar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.75rem',
          padding: '0.75rem 0',
          borderBottom: '1px solid var(--border-color)',
          marginBottom: '1rem',
          flexShrink: 0,
        }}
      >
        <button
          onClick={() => navigate(`/projects/${projectId}`)}
          style={{
            background: 'none',
            border: 'none',
            color: 'var(--text-secondary)',
            cursor: 'pointer',
            fontSize: '1.2rem',
            padding: '0.25rem',
          }}
          title="Back to project"
        >
          &#x2190;
        </button>
        <h2 style={{ fontWeight: 600, fontSize: '1.1rem', margin: 0 }}>
          {project.name}
        </h2>
        <span className={`badge ${isDetection ? 'badge-yellow' : 'badge-blue'}`}>
          {project.task_type}
        </span>

        {isDetection && project.class_list[activeClassIndex] && (
          <span style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.35rem',
            fontSize: '0.8rem',
            fontWeight: 600,
            color: getClassColor(activeClassIndex),
            padding: '0.2rem 0.6rem',
            borderRadius: 6,
            background: getClassColor(activeClassIndex) + '18',
            border: `1.5px solid ${getClassColor(activeClassIndex)}50`,
          }}>
            <span style={{
              width: 8, height: 8, borderRadius: '50%',
              background: getClassColor(activeClassIndex),
            }} />
            Active: {project.class_list[activeClassIndex]}
          </span>
        )}

        <div style={{ flex: 1 }} />

        {/* Sample scrubber */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
          <button
            onClick={goPrev}
            disabled={currentIndex <= 0}
            style={{
              background: 'none',
              border: '1px solid var(--border-color)',
              borderRadius: 4,
              color: currentIndex > 0 ? 'var(--text-primary)' : 'var(--text-muted)',
              cursor: currentIndex > 0 ? 'pointer' : 'default',
              padding: '0.2rem 0.5rem',
              fontSize: '0.85rem',
            }}
            title="Previous sample (←)"
          >
            &#x25C0;
          </button>
          <span style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', minWidth: 60, textAlign: 'center' }}>
            <strong style={{ color: 'var(--accent-blue)' }}>{currentIndex + 1}</strong> / {total}
          </span>
          <button
            onClick={goNext}
            disabled={currentIndex >= total - 1}
            style={{
              background: 'none',
              border: '1px solid var(--border-color)',
              borderRadius: 4,
              color: currentIndex < total - 1 ? 'var(--text-primary)' : 'var(--text-muted)',
              cursor: currentIndex < total - 1 ? 'pointer' : 'default',
              padding: '0.2rem 0.5rem',
              fontSize: '0.85rem',
            }}
            title="Next sample (→)"
          >
            &#x25B6;
          </button>
        </div>

        {/* Status badge */}
        {currentStatus && (
          <span className={`badge ${
            currentStatus === 'labeled' ? 'badge-green'
            : currentStatus === 'skipped' ? 'badge-yellow'
            : 'badge-muted'
          }`} style={{ fontSize: '0.7rem' }}>
            {currentStatus}
          </span>
        )}
        {currentStatus === 'labeled' && (
          <span style={{
            fontSize: '0.7rem',
            color: '#f59e0b',
            fontWeight: 600,
            padding: '0.15rem 0.4rem',
            borderRadius: 4,
            background: 'rgba(245, 158, 11, 0.12)',
            border: '1px solid rgba(245, 158, 11, 0.3)',
          }}>
            Re-labeling
          </span>
        )}

        {/* Progress */}
        <span style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
          <strong style={{ color: 'var(--accent-blue)' }}>{labeled}</strong> labeled
        </span>
        <div style={{ width: 100 }}>
          <div className="progress-bar">
            <div className="progress-fill" style={{ width: `${progressPct}%` }} />
          </div>
        </div>
        <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{progressPct}%</span>
      </div>

      {/* Main area: image + right panel */}
      <div style={{ display: 'flex', gap: '1rem', flex: 1, minHeight: 0 }}>
        {/* Center: Image or BBoxCanvas */}
        <div
          style={{
            flex: 3,
            background: 'var(--bg-secondary)',
            borderRadius: 8,
            border: '1px solid var(--border-color)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            overflow: 'hidden',
            position: 'relative',
          }}
        >
          {loading ? (
            <Spinner label="Loading image..." />
          ) : sample ? (
            isDetection ? (
              <BBoxCanvas
                imageSrc={sampleImageUrl(projectId, sample.id)}
                boxes={boxes}
                selectedBoxId={selectedBoxId}
                activeClassIndex={activeClassIndex}
                classList={project.class_list}
                onBoxCreated={handleBoxCreated}
                onBoxUpdated={handleBoxUpdated}
                onBoxSelected={setSelectedBoxId}
                onBoxDeleted={handleBoxDeleted}
              />
            ) : (
              <>
                {!imageLoaded && (
                  <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <Spinner size={40} label="" />
                  </div>
                )}
                <img
                  src={sampleImageUrl(projectId, sample.id)}
                  alt={sample.filename}
                  onLoad={() => setImageLoaded(true)}
                  style={{
                    maxWidth: '100%',
                    maxHeight: '100%',
                    objectFit: 'contain',
                    opacity: imageLoaded ? 1 : 0,
                    transition: 'opacity 0.2s',
                  }}
                />
              </>
            )
          ) : (
            <div style={{ color: 'var(--text-muted)', fontSize: '0.9rem' }}>No samples</div>
          )}
        </div>

        {/* Right panel */}
        <div
          style={{
            flex: 1,
            minWidth: 220,
            maxWidth: 280,
            background: 'var(--bg-card)',
            borderRadius: 8,
            border: '1px solid var(--border-color)',
            padding: '1rem',
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
          }}
        >
          {sample && (
            <>
              {/* File info */}
              <div style={{ marginBottom: '1rem' }}>
                <div style={{ fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.25rem' }}>
                  {sample.filename}
                </div>
                <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', wordBreak: 'break-all' }}>
                  {sample.filepath}
                </div>
              </div>

              <div style={{ borderTop: '1px solid var(--border-color)', margin: '0 0 1rem' }} />

              {isDetection ? (
                /* ===== DETECTION MODE ===== */
                <>
                  {/* Class selector */}
                  <div style={{ marginBottom: '0.75rem' }}>
                    <h4 style={{ fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.5rem', color: 'var(--text-secondary)' }}>
                      Active Class
                    </h4>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
                      {project.class_list.map((cls, i) => (
                        <button
                          key={cls}
                          className="btn-secondary"
                          onClick={() => setActiveClassIndex(i)}
                          style={{
                            textAlign: 'left',
                            fontSize: '0.85rem',
                            padding: '0.5rem 0.75rem',
                            display: 'flex',
                            alignItems: 'center',
                            gap: '0.5rem',
                            borderLeft: i === activeClassIndex ? `4px solid ${getClassColor(i)}` : '4px solid transparent',
                            border: i === activeClassIndex ? `2px solid ${getClassColor(i)}` : undefined,
                            borderLeftWidth: i === activeClassIndex ? 4 : 4,
                            borderLeftColor: i === activeClassIndex ? getClassColor(i) : 'transparent',
                            background: i === flashIndex ? getClassColor(i) + '60'
                              : i === activeClassIndex ? getClassColor(i) + '20' : undefined,
                            transition: 'background 0.15s ease-out, border-color 0.15s ease-out',
                            transform: i === flashIndex ? 'scale(1.03)' : undefined,
                            fontWeight: i === activeClassIndex ? 600 : 400,
                          }}
                        >
                          <span style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            width: 22,
                            height: 22,
                            borderRadius: 4,
                            background: getClassColor(i) + '33',
                            color: getClassColor(i),
                            fontSize: '0.75rem',
                            fontWeight: 700,
                            flexShrink: 0,
                          }}>
                            {i + 1}
                          </span>
                          <span style={{ width: 8, height: 8, borderRadius: '50%', background: getClassColor(i), flexShrink: 0 }} />
                          {cls}
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* Add class input */}
                  <div style={{ display: 'flex', gap: '0.3rem', marginBottom: '0.75rem' }}>
                    <input
                      type="text"
                      value={newClassName}
                      onChange={(e) => setNewClassName(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); handleAddClass(); } }}
                      placeholder="New class..."
                      style={{
                        flex: 1,
                        padding: '0.35rem 0.5rem',
                        background: 'var(--bg-input)',
                        color: 'var(--text-primary)',
                        border: '1px solid var(--border-color)',
                        borderRadius: 4,
                        fontSize: '0.75rem',
                      }}
                    />
                    <button
                      type="button"
                      onClick={handleAddClass}
                      disabled={addingClass || !newClassName.trim()}
                      className="btn-secondary"
                      style={{ padding: '0.35rem 0.5rem', fontSize: '0.75rem', whiteSpace: 'nowrap' }}
                    >
                      + Add
                    </button>
                  </div>

                  <div style={{ borderTop: '1px solid var(--border-color)', margin: '0 0 0.75rem' }} />

                  {/* Annotation list */}
                  <div style={{ flex: 1, overflowY: 'auto', marginBottom: '0.75rem' }}>
                    <h4 style={{ fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.5rem', color: 'var(--text-secondary)' }}>
                      Annotations ({boxes.length})
                    </h4>
                    {boxes.length === 0 ? (
                      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>
                        Draw boxes on the image
                      </div>
                    ) : (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
                        {boxes.map((box) => (
                          <div
                            key={box.id}
                            onClick={() => setSelectedBoxId(box.id)}
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              gap: '0.4rem',
                              padding: '0.3rem 0.5rem',
                              borderRadius: 4,
                              fontSize: '0.8rem',
                              cursor: 'pointer',
                              background: box.id === selectedBoxId ? 'var(--bg-hover)' : 'transparent',
                              border: box.id === selectedBoxId ? '1px solid var(--border-hover)' : '1px solid transparent',
                            }}
                          >
                            <span style={{ width: 8, height: 8, borderRadius: '50%', background: getClassColor(box.classIndex), flexShrink: 0 }} />
                            <span style={{ flex: 1 }}>{box.label}</span>
                            <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                              {Math.round(box.w * 100)}x{Math.round(box.h * 100)}
                            </span>
                            <button
                              onClick={(e) => { e.stopPropagation(); handleBoxDeleted(box.id); }}
                              style={{
                                background: 'none',
                                border: 'none',
                                color: 'var(--text-muted)',
                                cursor: 'pointer',
                                padding: '0 0.2rem',
                                fontSize: '0.75rem',
                              }}
                            >
                              &#x2715;
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  <div style={{ borderTop: '1px solid var(--border-color)', margin: '0 0 0.75rem' }} />

                  {/* Save & Next + Skip */}
                  <button
                    className="btn-primary"
                    onClick={handleSaveBoxes}
                    disabled={saving || boxes.length === 0}
                    style={{ width: '100%', fontSize: '0.85rem', marginBottom: '0.5rem' }}
                  >
                    {saving ? 'Saving...' : `Save & Next (${boxes.length})`}
                  </button>

                  <button
                    className="btn-secondary"
                    onClick={handleSkip}
                    disabled={saving}
                    style={{ width: '100%', fontSize: '0.85rem', marginBottom: '0.75rem' }}
                  >
                    Skip (S)
                  </button>

                  <KeyboardShortcutLegend maxClassKey={Math.min(9, project.class_list.length)} />
                </>
              ) : (
                /* ===== CLASSIFICATION MODE (multi-label) ===== */
                <>
                  <div style={{ marginBottom: '0.5rem' }}>
                    <h4 style={{ fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.25rem', color: 'var(--text-secondary)' }}>
                      Select labels:
                    </h4>
                    <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                      {selectedLabels.size} of {project.class_list.length} selected
                    </span>
                  </div>
                  <div style={{ flex: 1, overflowY: 'auto' }}>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
                      {project.class_list.map((cls, i) => {
                        const isSelected = selectedLabels.has(cls);
                        return (
                          <button
                            key={cls}
                            className="btn-secondary"
                            onClick={() => toggleLabel(cls)}
                            disabled={saving}
                            style={{
                              textAlign: 'left',
                              fontSize: '0.85rem',
                              padding: '0.5rem 0.75rem',
                              display: 'flex',
                              alignItems: 'center',
                              gap: '0.5rem',
                              border: isSelected ? '2px solid var(--accent-blue)' : undefined,
                              background: i === flashIndex
                                ? 'rgba(66, 153, 224, 0.35)'
                                : isSelected
                                  ? 'rgba(66, 153, 224, 0.15)'
                                  : undefined,
                              transition: 'background 0.15s ease-out',
                              transform: i === flashIndex ? 'scale(1.03)' : undefined,
                            }}
                          >
                            <span style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              width: 22,
                              height: 22,
                              borderRadius: 4,
                              background: isSelected ? 'var(--accent-blue)' : 'rgba(66, 153, 224, 0.2)',
                              color: isSelected ? '#fff' : 'var(--accent-blue)',
                              fontSize: '0.75rem',
                              fontWeight: 700,
                              flexShrink: 0,
                            }}>
                              {isSelected ? '\u2713' : i + 1}
                            </span>
                            {cls}
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  {/* Add class input */}
                  <div style={{ display: 'flex', gap: '0.3rem', marginTop: '0.5rem' }}>
                    <input
                      type="text"
                      value={newClassName}
                      onChange={(e) => setNewClassName(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); handleAddClass(); } }}
                      placeholder="New class..."
                      style={{
                        flex: 1,
                        padding: '0.35rem 0.5rem',
                        background: 'var(--bg-input)',
                        color: 'var(--text-primary)',
                        border: '1px solid var(--border-color)',
                        borderRadius: 4,
                        fontSize: '0.75rem',
                      }}
                    />
                    <button
                      type="button"
                      onClick={handleAddClass}
                      disabled={addingClass || !newClassName.trim()}
                      className="btn-secondary"
                      style={{ padding: '0.35rem 0.5rem', fontSize: '0.75rem', whiteSpace: 'nowrap' }}
                    >
                      + Add
                    </button>
                  </div>

                  <div style={{ borderTop: '1px solid var(--border-color)', margin: '0.75rem 0 0.75rem' }} />

                  {/* Save & Next + Skip */}
                  <button
                    className="btn-primary"
                    onClick={handleSaveClassification}
                    disabled={saving || selectedLabels.size === 0}
                    style={{ width: '100%', fontSize: '0.85rem', marginBottom: '0.5rem' }}
                  >
                    {saving
                      ? 'Saving...'
                      : selectedLabels.size === 0
                        ? 'Save & Next'
                        : (() => {
                            const labels = [...selectedLabels];
                            const summary = labels.length <= 2
                              ? labels.join(', ')
                              : `${labels.slice(0, 2).join(', ')}, +${labels.length - 2} more`;
                            return `Save & Next (${summary})`;
                          })()}
                  </button>

                  <button
                    className="btn-secondary"
                    onClick={handleSkip}
                    disabled={saving}
                    style={{ width: '100%', fontSize: '0.85rem', marginBottom: '0.75rem' }}
                  >
                    Skip (S)
                  </button>

                  <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                    [1-{Math.min(9, project.class_list.length)}] toggle &middot; [Enter] save &middot; [S] skip &middot; [&larr;&rarr;] nav &middot; [Esc] back
                  </div>
                </>
              )}

              {/* History panel */}
              <div style={{ borderTop: '1px solid var(--border-color)', marginTop: '0.75rem', paddingTop: '0.5rem' }}>
                <button
                  onClick={() => setHistoryOpen(prev => !prev)}
                  style={{
                    background: 'none',
                    border: 'none',
                    color: 'var(--text-secondary)',
                    cursor: 'pointer',
                    fontSize: '0.8rem',
                    fontWeight: 600,
                    padding: '0.25rem 0',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.4rem',
                    width: '100%',
                  }}
                >
                  <span style={{
                    display: 'inline-block',
                    transition: 'transform 0.15s',
                    transform: historyOpen ? 'rotate(90deg)' : 'rotate(0deg)',
                    fontSize: '0.7rem',
                  }}>&#x25B6;</span>
                  History
                </button>
                {historyOpen && (
                  <div style={{ marginTop: '0.4rem', maxHeight: 180, overflowY: 'auto' }}>
                    {historyLoading ? (
                      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>Loading...</div>
                    ) : history.length === 0 ? (
                      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>No history yet</div>
                    ) : (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
                        {history.map((h) => (
                          <div
                            key={h.id}
                            style={{
                              fontSize: '0.72rem',
                              padding: '0.35rem 0.5rem',
                              borderRadius: 4,
                              background: 'var(--bg-secondary)',
                              border: '1px solid var(--border-color)',
                              lineHeight: 1.4,
                            }}
                          >
                            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.15rem' }}>
                              <span style={{
                                fontWeight: 600,
                                color: h.action === 'create' ? '#22c55e'
                                  : h.action === 'update' ? '#f59e0b'
                                  : '#ef4444',
                                textTransform: 'uppercase',
                                fontSize: '0.65rem',
                              }}>
                                {h.action}
                              </span>
                              <span style={{ color: 'var(--text-muted)', fontSize: '0.65rem' }}>
                                {new Date(h.changed_at).toLocaleString(undefined, {
                                  month: 'short', day: 'numeric',
                                  hour: '2-digit', minute: '2-digit',
                                })}
                              </span>
                            </div>
                            <div style={{ color: 'var(--text-secondary)' }}>
                              {h.old_label && h.new_label ? (
                                <span>{h.old_label} <span style={{ color: 'var(--text-muted)' }}>&rarr;</span> {h.new_label}</span>
                              ) : h.new_label ? (
                                <span>{h.new_label}</span>
                              ) : h.old_label ? (
                                <span style={{ textDecoration: 'line-through' }}>{h.old_label}</span>
                              ) : null}
                            </div>
                            <div style={{ color: 'var(--text-muted)', fontSize: '0.65rem' }}>
                              {h.changed_by}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

const kbdStyle = {
  display: 'inline-block',
  padding: '1px 5px',
  borderRadius: 3,
  background: 'var(--bg-secondary, #2a2a2a)',
  border: '1px solid var(--border-color, #444)',
  fontSize: '0.65rem',
  fontFamily: 'ui-monospace, monospace',
  fontWeight: 600,
  lineHeight: '1.4',
  color: 'var(--text-primary)',
  minWidth: 18,
  textAlign: 'center',
};

function KeyboardShortcutLegend({ maxClassKey }) {
  const shortcuts = [
    { keys: [`1-${maxClassKey}`], desc: 'Select class' },
    { keys: ['Enter'], desc: 'Save & next' },
    { keys: ['Del'], desc: 'Delete box' },
    { keys: ['\u2318/Ctrl', 'Z'], desc: 'Undo' },
    { keys: [']'], desc: 'Next box' },
    { keys: ['['], desc: 'Prev box' },
    { keys: ['N'], desc: 'Next unlabeled' },
    { keys: ['S'], desc: 'Skip' },
    { keys: ['\u2190 \u2192'], desc: 'Navigate' },
    { keys: ['Esc'], desc: 'Deselect / Back' },
  ];

  return (
    <div style={{
      borderTop: '1px solid var(--border-color)',
      paddingTop: '0.5rem',
    }}>
      <div style={{
        fontSize: '0.7rem',
        fontWeight: 600,
        color: 'var(--text-secondary)',
        marginBottom: '0.35rem',
      }}>
        Shortcuts
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem' }}>
        {shortcuts.map(({ keys, desc }) => (
          <div key={desc} style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.7rem' }}>
            <span style={{ display: 'flex', gap: 2 }}>
              {keys.map(k => <kbd key={k} style={kbdStyle}>{k}</kbd>)}
            </span>
            <span style={{ color: 'var(--text-muted)' }}>{desc}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
