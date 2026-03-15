/**
 * Labeling page - Annotate images one at a time.
 * Modes: Classification, Bounding Box, Polygon, Tagging.
 * Mirrors Streamlit page 3 (Labeling).
 */

import { useState, useEffect, useCallback } from 'react';
import Spinner from '../components/Spinner';
import DatasetSelector from '../components/DatasetSelector';
import { useDataset } from '../contexts/DatasetContext';
import AnnotationCanvas from '../components/AnnotationCanvas';
import {
  fetchSamples,
  fetchDatasetStats,
  createAnnotation,
  createAnnotationsBatch,
  createTag,
  imageUrl,
} from '../api/client';

const MODES = ['Classification', 'Bounding Box', 'Polygon', 'Tagging'];
const DEFAULT_CLASSES = ['car', 'truck', 'person', 'bicycle', 'sign'];
const QUICK_TAGS = ['good', 'bad', 'review', 'skip', 'flagged'];

export default function LabelingView() {
  const { dataset } = useDataset();
  const [stats, setStats] = useState(null);
  const [samples, setSamples] = useState([]);
  const [totalSamples, setTotalSamples] = useState(0);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [mode, setMode] = useState('Classification');
  const [classes, setClasses] = useState(DEFAULT_CLASSES);
  const [selectedClass, setSelectedClass] = useState(DEFAULT_CLASSES[0]);
  const [newClass, setNewClass] = useState('');
  const [autosave, setAutosave] = useState(true);
  const [pendingAnnotations, setPendingAnnotations] = useState([]);
  const [imageLoaded, setImageLoaded] = useState(false);

  // Load all sample IDs for navigation
  useEffect(() => {
    if (!dataset) return;
    fetchSamples(dataset.id, { page: 0, page_size: 9999 })
      .then((data) => {
        setSamples(data.items);
        setTotalSamples(data.total);
        setCurrentIndex(0);
        setPendingAnnotations([]);
      })
      .catch(console.error);
  }, [dataset]);

  useEffect(() => {
    if (!dataset) return;
    fetchDatasetStats(dataset.id)
      .then(setStats)
      .catch(console.error);
  }, [dataset, currentIndex]);

  useEffect(() => { setImageLoaded(false); }, [currentIndex]);

  const currentSample = samples[currentIndex] || null;

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e) => {
      // Number keys 1-9 for class selection in Classification mode
      if (mode === 'Classification' && e.key >= '1' && e.key <= '9') {
        const idx = parseInt(e.key) - 1;
        if (idx < classes.length) {
          handleClassify(classes[idx]);
        }
        return;
      }
      // Arrow keys for navigation
      if (e.key === 'ArrowRight' || e.key === 'n') {
        goNext();
      } else if (e.key === 'ArrowLeft' || e.key === 'p') {
        goPrev();
      } else if (e.key === 's') {
        handleSkip();
      } else if (e.key === 'f') {
        handleFlag();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [mode, classes, currentIndex, totalSamples, currentSample]);

  const goNext = useCallback(() => {
    setCurrentIndex((i) => Math.min(i + 1, totalSamples - 1));
    setPendingAnnotations([]);
  }, [totalSamples]);

  const goPrev = useCallback(() => {
    setCurrentIndex((i) => Math.max(i - 1, 0));
    setPendingAnnotations([]);
  }, []);

  const refreshCurrentSample = useCallback(async () => {
    if (!dataset) return;
    try {
      const data = await fetchSamples(dataset.id, { page: 0, page_size: 9999 });
      setSamples(data.items);
      setTotalSamples(data.total);
    } catch (err) {
      console.error(err);
    }
  }, [dataset]);

  // Classification
  const handleClassify = async (label) => {
    if (!currentSample) return;
    try {
      await createAnnotation({
        sample_id: currentSample.id,
        ann_type: 'classification',
        label,
      });
      await refreshCurrentSample();
      goNext();
    } catch (err) {
      console.error('Failed to save classification:', err);
    }
  };

  // Annotation from canvas (bbox or polygon)
  const handleCanvasAnnotation = async (item) => {
    if (autosave && currentSample) {
      try {
        const payload = {
          sample_id: currentSample.id,
          ann_type: item.type,
          label: item.label,
        };
        if (item.type === 'detection') {
          payload.bbox_json = JSON.stringify(item.bbox);
        } else if (item.type === 'segmentation') {
          payload.polygon_json = JSON.stringify(item.polygon);
        }
        await createAnnotation(payload);
        await refreshCurrentSample();
      } catch (err) {
        console.error('Failed to save annotation:', err);
      }
    } else {
      setPendingAnnotations((prev) => [...prev, item]);
    }
  };

  // Save pending annotations (manual save)
  const savePending = async () => {
    if (!currentSample || pendingAnnotations.length === 0) return;
    try {
      const batch = pendingAnnotations.map((item) => {
        const payload = {
          sample_id: currentSample.id,
          ann_type: item.type,
          label: item.label,
        };
        if (item.type === 'detection') {
          payload.bbox_json = JSON.stringify(item.bbox);
        } else if (item.type === 'segmentation') {
          payload.polygon_json = JSON.stringify(item.polygon);
        }
        return payload;
      });
      await createAnnotationsBatch(batch);
      setPendingAnnotations([]);
      await refreshCurrentSample();
      goNext();
    } catch (err) {
      console.error('Failed to save batch:', err);
    }
  };

  // Tagging
  const handleTag = async (tag) => {
    if (!currentSample) return;
    try {
      await createTag({ sample_id: currentSample.id, tag });
      await refreshCurrentSample();
      goNext();
    } catch (err) {
      console.error('Failed to save tag:', err);
    }
  };

  const handleSkip = () => handleTag('skip');
  const handleFlag = () => handleTag('flagged');

  // Add class
  const addClass = () => {
    if (newClass && !classes.includes(newClass)) {
      setClasses((prev) => [...prev, newClass]);
      setNewClass('');
    }
  };

  const labeledCount = stats?.labeled_count || 0;
  const progressPct = totalSamples > 0 ? (labeledCount / totalSamples) * 100 : 0;

  return (
    <div>
      <h1 style={{ fontSize: '1.75rem', fontWeight: 700, marginBottom: '0.5rem' }}>
        Labeling
      </h1>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '1rem', fontSize: '0.9rem' }}>
        Annotate images: classify, draw boxes/polygons, or tag.
      </p>

      {/* Dataset selector */}
      <div style={{ marginBottom: '1rem' }}>
        <DatasetSelector />
      </div>

      {dataset && (
        <>
          {/* Progress bar */}
          <div style={{ marginBottom: '1.5rem' }}>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                fontSize: '0.8rem',
                color: 'var(--text-secondary)',
                marginBottom: '0.25rem',
              }}
            >
              <span>{labeledCount} of {totalSamples} labeled</span>
              <span>{progressPct.toFixed(0)}%</span>
            </div>
            <div className="progress-bar">
              <div
                className="progress-bar-fill"
                style={{ width: `${progressPct}%` }}
              />
            </div>
          </div>

          <div style={{ display: 'flex', gap: '1.5rem', height: 'calc(100vh - 200px)' }}>
            {/* Image + Canvas area */}
            <div style={{ flex: 1, minWidth: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
              {currentSample && (
                <>
                  <div
                    style={{
                      fontSize: '0.875rem',
                      color: 'var(--text-secondary)',
                      marginBottom: '0.5rem',
                    }}
                  >
                    <strong style={{ color: 'var(--text-primary)' }}>
                      Sample {currentIndex + 1} / {totalSamples}
                    </strong>
                    {' -- '}
                    <code style={{ color: 'var(--accent-teal)' }}>
                      {currentSample.filename}
                    </code>
                  </div>

                  {mode === 'Bounding Box' || mode === 'Polygon' ? (
                    <AnnotationCanvas
                      imageUrl={imageUrl(currentSample.id)}
                      mode={mode === 'Bounding Box' ? 'bbox' : 'polygon'}
                      annotations={currentSample.annotations || []}
                      classes={classes}
                      currentLabel={selectedClass}
                      onAnnotation={handleCanvasAnnotation}
                      width={800}
                      height={600}
                    />
                  ) : (
                    <div
                      style={{
                        background: 'var(--bg-secondary)',
                        borderRadius: 8,
                        border: '1px solid var(--border-color)',
                        overflow: 'hidden',
                        display: 'inline-block',
                        position: 'relative',
                      }}
                    >
                      {!imageLoaded && (
                        <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 200 }}>
                          <Spinner size={40} label="" />
                        </div>
                      )}
                      <img
                        src={imageUrl(currentSample.id)}
                        alt={currentSample.filename}
                        onLoad={() => setImageLoaded(true)}
                        style={{
                          maxWidth: '100%',
                          maxHeight: 'calc(100vh - 280px)',
                          objectFit: 'contain',
                          opacity: imageLoaded ? 1 : 0,
                          transition: 'opacity 0.2s',
                        }}
                      />
                    </div>
                  )}

                  {/* Tags display */}
                  {currentSample.tags?.length > 0 && (
                    <div style={{ marginTop: '0.5rem' }}>
                      {currentSample.tags.map((t) => (
                        <span
                          key={t.id}
                          className="badge badge-teal"
                          style={{ marginRight: '0.25rem' }}
                        >
                          {t.tag}
                        </span>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>

            {/* Right control panel */}
            <div
              style={{
                width: 260,
                minWidth: 260,
                background: 'var(--bg-card)',
                borderRadius: 12,
                border: '1px solid var(--border-color)',
                padding: '1rem',
                display: 'flex',
                flexDirection: 'column',
                overflow: 'hidden',
              }}
            >
              <div style={{ flex: 1, overflowY: 'auto' }}>
                {/* Mode selector */}
                <h3 style={{ fontSize: '0.9rem', fontWeight: 600, marginBottom: '0.75rem' }}>
                  Mode
                </h3>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem', marginBottom: '1rem' }}>
                  {MODES.map((m) => (
                    <button
                      key={m}
                      className={m === mode ? 'btn-primary' : 'btn-secondary'}
                      onClick={() => setMode(m)}
                      style={{ textAlign: 'left', fontSize: '0.8rem' }}
                    >
                      {m}
                    </button>
                  ))}
                </div>

                {/* Autosave toggle */}
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.5rem',
                    marginBottom: '1rem',
                    fontSize: '0.8rem',
                  }}
                >
                  <input
                    type="checkbox"
                    checked={autosave}
                    onChange={(e) => setAutosave(e.target.checked)}
                    style={{ accentColor: 'var(--accent-teal)' }}
                  />
                  <span>Autosave {autosave ? '(on)' : '(off)'}</span>
                </div>

                <div style={{ borderTop: '1px solid var(--border-color)', margin: '0.75rem 0' }} />

                {/* Mode-specific controls */}
                {mode === 'Classification' && (
                  <div>
                    <h4 style={{ fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.5rem' }}>
                      Pick a class:
                    </h4>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
                      {classes.map((cls, i) => (
                        <button
                          key={cls}
                          className="btn-secondary"
                          onClick={() => handleClassify(cls)}
                          style={{ textAlign: 'left', fontSize: '0.8rem' }}
                        >
                          <span style={{ color: 'var(--accent-teal)', marginRight: '0.5rem' }}>
                            {i + 1}
                          </span>
                          {cls}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {(mode === 'Bounding Box' || mode === 'Polygon') && (
                  <div>
                    <h4 style={{ fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.5rem' }}>
                      Active label:
                    </h4>
                    <select
                      value={selectedClass}
                      onChange={(e) => setSelectedClass(e.target.value)}
                      style={{
                        width: '100%',
                        padding: '0.4rem 0.6rem',
                        background: 'var(--bg-input)',
                        color: 'var(--text-primary)',
                        border: '1px solid var(--border-color)',
                        borderRadius: 6,
                        fontSize: '0.8rem',
                        marginBottom: '0.75rem',
                      }}
                    >
                      {classes.map((c) => (
                        <option key={c} value={c}>{c}</option>
                      ))}
                    </select>

                    {!autosave && pendingAnnotations.length > 0 && (
                      <button
                        className="btn-primary"
                        onClick={savePending}
                        style={{ width: '100%', marginBottom: '0.5rem' }}
                      >
                        Save {pendingAnnotations.length} annotation(s)
                      </button>
                    )}
                  </div>
                )}

                {mode === 'Tagging' && (
                  <div>
                    <h4 style={{ fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.5rem' }}>
                      Quick tags:
                    </h4>
                    <div
                      style={{
                        display: 'grid',
                        gridTemplateColumns: '1fr 1fr',
                        gap: '0.25rem',
                        marginBottom: '0.75rem',
                      }}
                    >
                      {QUICK_TAGS.map((tag) => (
                        <button
                          key={tag}
                          className="btn-secondary"
                          onClick={() => handleTag(tag)}
                          style={{ fontSize: '0.75rem' }}
                        >
                          {tag}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                <div style={{ borderTop: '1px solid var(--border-color)', margin: '0.75rem 0' }} />

                {/* Class manager */}
                <h4 style={{ fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.5rem' }}>
                  Classes ({classes.length})
                </h4>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>
                  {classes.join(', ')}
                </div>
                <div style={{ display: 'flex', gap: '0.25rem' }}>
                  <input
                    type="text"
                    value={newClass}
                    onChange={(e) => setNewClass(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && addClass()}
                    placeholder="New class..."
                    style={{
                      flex: 1,
                      padding: '0.3rem 0.5rem',
                      background: 'var(--bg-input)',
                      color: 'var(--text-primary)',
                      border: '1px solid var(--border-color)',
                      borderRadius: 6,
                      fontSize: '0.75rem',
                    }}
                  />
                  <button
                    className="btn-primary"
                    onClick={addClass}
                    style={{ fontSize: '0.75rem', padding: '0.3rem 0.6rem' }}
                  >
                    Add
                  </button>
                </div>
              </div>

              {/* Navigation */}
              <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: '0.75rem', marginTop: '0.75rem' }}>
                <h4 style={{ fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.5rem' }}>
                  Navigation
                </h4>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.25rem' }}>
                  <button
                    className="btn-secondary"
                    onClick={goPrev}
                    disabled={currentIndex === 0}
                    style={{ fontSize: '0.8rem' }}
                  >
                    Prev
                  </button>
                  <button
                    className="btn-secondary"
                    onClick={goNext}
                    disabled={currentIndex >= totalSamples - 1}
                    style={{ fontSize: '0.8rem' }}
                  >
                    Next
                  </button>
                  <button
                    className="btn-secondary"
                    onClick={handleSkip}
                    style={{ fontSize: '0.8rem' }}
                  >
                    Skip
                  </button>
                  <button
                    className="btn-secondary"
                    onClick={handleFlag}
                    style={{ fontSize: '0.8rem' }}
                  >
                    Flag
                  </button>
                </div>

                <div
                  style={{
                    marginTop: '0.75rem',
                    fontSize: '0.7rem',
                    color: 'var(--text-muted)',
                  }}
                >
                  Shortcuts: [n] next, [p] prev, [s] skip, [f] flag, [1-9] classify
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
