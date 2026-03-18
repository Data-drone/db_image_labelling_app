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
  annotateSample,
  annotateSampleBatch,
  skipSample,
  sampleImageUrl,
  addProjectClass,
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

      // Load existing annotations for detection re-labeling
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
      } else {
        setBoxes([]);
      }
      setSelectedBoxId(null);
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
  const markCurrentAndAdvance = () => {
    setSampleList(prev => prev.map((s, i) =>
      i === currentIndex ? { ...s, status: 'labeled' } : s
    ));
    loadStats();
    // Go to next unlabeled
    const nextUnlabeled = sampleList.findIndex((s, i) =>
      i > currentIndex && s.status === 'unlabeled'
    );
    if (nextUnlabeled >= 0) {
      goTo(nextUnlabeled);
    } else {
      // Wrap around
      const wrapped = sampleList.findIndex(s => s.status === 'unlabeled');
      if (wrapped >= 0 && wrapped !== currentIndex) {
        goTo(wrapped);
      } else if (currentIndex < sampleList.length - 1) {
        goTo(currentIndex + 1);
      }
    }
  };

  // Classify (classification mode)
  const handleClassify = async (label) => {
    if (!sample || saving) return;
    setSaving(true);
    try {
      await annotateSample(projectId, sample.id, {
        label,
        ann_type: 'classification',
      });
      markCurrentAndAdvance();
    } catch (err) {
      console.error('Annotation failed:', err);
    } finally {
      setSaving(false);
    }
  };

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
    setBoxes(prev => [...prev, {
      id,
      label: project.class_list[activeClassIndex] || '',
      classIndex: activeClassIndex,
      ...rect,
    }]);
    setSelectedBoxId(id);
  };

  const handleBoxUpdated = (id, updates) => {
    setBoxes(prev => prev.map(b => b.id === id ? { ...b, ...updates } : b));
  };

  const handleBoxDeleted = useCallback((id) => {
    setBoxes(prev => prev.filter(b => b.id !== id));
    setSelectedBoxId(prev => prev === id ? null : prev);
  }, []);

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

      // Number keys 1-9: class selection
      if (e.key >= '1' && e.key <= '9') {
        const idx = parseInt(e.key) - 1;
        if (idx < project.class_list.length) {
          if (isDetection) {
            setActiveClassIndex(idx);
          } else {
            handleClassify(project.class_list[idx]);
          }
        }
        return;
      }
      if (e.key === 's' || e.key === 'S') {
        e.preventDefault();
        handleSkip();
      } else if (e.key === 'Enter' && isDetection) {
        e.preventDefault();
        handleSaveBoxes();
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
  }, [project, sample, saving, projectId, navigate, isDetection, selectedBoxId, boxes, currentIndex, sampleList]);

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
                            border: i === activeClassIndex ? `2px solid ${getClassColor(i)}` : undefined,
                            background: i === activeClassIndex ? getClassColor(i) + '20' : undefined,
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

                  <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                    [1-{Math.min(9, project.class_list.length)}] class &middot; [S] skip &middot; [Enter] save &middot; [Del] remove &middot; [&larr;&rarr;] nav &middot; [Esc] back
                  </div>
                </>
              ) : (
                /* ===== CLASSIFICATION MODE ===== */
                <>
                  <div style={{ flex: 1, overflowY: 'auto' }}>
                    <h4 style={{ fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.5rem', color: 'var(--text-secondary)' }}>
                      Classify as:
                    </h4>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem' }}>
                      {project.class_list.map((cls, i) => (
                        <button
                          key={cls}
                          className="btn-secondary"
                          onClick={() => handleClassify(cls)}
                          disabled={saving}
                          style={{
                            textAlign: 'left',
                            fontSize: '0.85rem',
                            padding: '0.5rem 0.75rem',
                            display: 'flex',
                            alignItems: 'center',
                            gap: '0.5rem',
                          }}
                        >
                          <span style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            width: 22,
                            height: 22,
                            borderRadius: 4,
                            background: 'rgba(66, 153, 224, 0.2)',
                            color: 'var(--accent-blue)',
                            fontSize: '0.75rem',
                            fontWeight: 700,
                            flexShrink: 0,
                          }}>
                            {i + 1}
                          </span>
                          {cls}
                        </button>
                      ))}
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

                  {/* Skip + shortcuts */}
                  <button
                    className="btn-secondary"
                    onClick={handleSkip}
                    disabled={saving}
                    style={{ width: '100%', fontSize: '0.85rem', marginBottom: '0.75rem' }}
                  >
                    Skip (S)
                  </button>

                  <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                    [1-{Math.min(9, project.class_list.length)}] label &middot; [S] skip &middot; [&larr;&rarr;] nav &middot; [Esc] back
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
