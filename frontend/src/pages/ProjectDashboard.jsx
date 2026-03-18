/**
 * Project Dashboard — per-project stats, progress, and per-user breakdown.
 */

import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { fetchProject, fetchProjectStats, cloneProject, updateProject } from '../api/client';
import Spinner from '../components/Spinner';

export default function ProjectDashboard() {
  const { id: projectId } = useParams();
  const navigate = useNavigate();

  const [project, setProject] = useState(null);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [cloning, setCloning] = useState(false);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editForm, setEditForm] = useState({});
  const [newClass, setNewClass] = useState('');

  useEffect(() => {
    Promise.all([
      fetchProject(projectId),
      fetchProjectStats(projectId),
    ])
      .then(([proj, st]) => {
        setProject(proj);
        setStats(st);
      })
      .catch(() => navigate('/'))
      .finally(() => setLoading(false));
  }, [projectId, navigate]);

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

  const startEditing = () => {
    setEditForm({
      name: project.name,
      description: project.description || '',
      source_volume: project.source_volume,
      class_list: [...project.class_list],
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
      // Refresh stats if source changed (sample counts may differ)
      if (sourceChanged) {
        const st = await fetchProjectStats(projectId);
        setStats(st);
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
        <div style={{ display: 'flex', gap: '0.5rem' }}>
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
              {stats.labeled} labeled, {stats.skipped} skipped, {stats.unlabeled} remaining
            </div>
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
