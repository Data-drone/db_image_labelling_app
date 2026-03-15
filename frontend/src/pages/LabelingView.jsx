/**
 * Labeling View — project-centric annotation interface.
 * 3-zone layout: top bar, center image (75%), right panel (25%).
 * Supports classification (numbered buttons) and detection (bbox canvas).
 */

import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import Spinner from '../components/Spinner';
import {
  fetchProject,
  fetchProjectStats,
  fetchNextSample,
  annotateSample,
  skipSample,
  sampleImageUrl,
} from '../api/client';

export default function LabelingView() {
  const { id: projectId } = useParams();
  const navigate = useNavigate();

  const [project, setProject] = useState(null);
  const [stats, setStats] = useState(null);
  const [sample, setSample] = useState(null);
  const [loading, setLoading] = useState(true);
  const [imageLoaded, setImageLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);

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

  // Load next sample
  const loadNext = useCallback(async () => {
    setLoading(true);
    setImageLoaded(false);
    try {
      const next = await fetchNextSample(projectId);
      if (next) {
        setSample(next);
        setDone(false);
      } else {
        setSample(null);
        setDone(true);
      }
    } catch (err) {
      console.error('Failed to load next sample:', err);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { loadNext(); }, [loadNext]);

  // Classify
  const handleClassify = async (label) => {
    if (!sample || saving) return;
    setSaving(true);
    try {
      await annotateSample(projectId, sample.id, {
        label,
        ann_type: 'classification',
      });
      loadStats();
      await loadNext();
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
      loadStats();
      await loadNext();
    } catch (err) {
      console.error('Skip failed:', err);
    } finally {
      setSaving(false);
    }
  };

  // Keyboard shortcuts
  useEffect(() => {
    if (!project || !sample) return;
    const handler = (e) => {
      // Don't capture when typing in inputs
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

      // Number keys 1-9 for class labels
      if (e.key >= '1' && e.key <= '9') {
        const idx = parseInt(e.key) - 1;
        if (project.class_list && idx < project.class_list.length) {
          handleClassify(project.class_list[idx]);
        }
        return;
      }
      if (e.key === 's' || e.key === 'S') {
        e.preventDefault();
        handleSkip();
      } else if (e.key === 'Escape') {
        navigate(`/projects/${projectId}`);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [project, sample, saving, projectId, navigate]);

  const labeled = stats?.labeled || 0;
  const total = stats?.total || 0;
  const progressPct = total > 0 ? Math.round((labeled / total) * 100) : 0;

  if (!project) return <Spinner label="Loading project..." />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 4rem)' }}>
      {/* Top bar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '1rem',
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
        <span className={`badge ${project.task_type === 'detection' ? 'badge-yellow' : 'badge-blue'}`}>
          {project.task_type}
        </span>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
          <strong style={{ color: 'var(--accent-blue)' }}>{labeled}</strong> / {total}
        </span>
        <div style={{ width: 120 }}>
          <div className="progress-bar">
            <div className="progress-fill" style={{ width: `${progressPct}%` }} />
          </div>
        </div>
        <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{progressPct}%</span>
      </div>

      {/* Main area: image + right panel */}
      {done ? (
        <div style={{
          flex: 1,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexDirection: 'column',
          gap: '1rem',
        }}>
          <div style={{ fontSize: '3rem', opacity: 0.4 }}>&#x2714;</div>
          <h3 style={{ fontWeight: 600 }}>All done!</h3>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
            All samples in this project have been labeled or skipped.
          </p>
          <button className="btn-primary" onClick={() => navigate(`/projects/${projectId}`)}>
            Back to Dashboard
          </button>
        </div>
      ) : (
        <div style={{ display: 'flex', gap: '1rem', flex: 1, minHeight: 0 }}>
          {/* Center: Image */}
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
              <Spinner label="Loading next image..." />
            ) : sample ? (
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
            ) : null}
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

                {/* Class buttons */}
                <div style={{ flex: 1, overflowY: 'auto' }}>
                  <h4 style={{ fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.5rem', color: 'var(--text-secondary)' }}>
                    {project.task_type === 'classification' ? 'Classify as:' : 'Label:'}
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

                <div style={{ borderTop: '1px solid var(--border-color)', margin: '1rem 0 0.75rem' }} />

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
                  [1-{Math.min(9, project.class_list.length)}] label &middot; [S] skip &middot; [Esc] back
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
