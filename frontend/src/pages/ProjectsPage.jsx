/**
 * Projects list page — shows all labeling projects grouped by version lineage.
 */

import { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchProjects, deleteProject } from '../api/client';
import Spinner from '../components/Spinner';

function ProjectCard({ p, navigate, onDelete, indent = false }) {
  const pct = p.sample_count > 0
    ? Math.round((p.labeled_count / p.sample_count) * 100)
    : 0;

  return (
    <div
      className="card"
      onClick={() => navigate(`/projects/${p.id}`)}
      style={{
        cursor: 'pointer',
        position: 'relative',
        ...(indent ? { marginLeft: '1.5rem', borderLeft: '2px solid var(--border-color)' } : {}),
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '0.75rem' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h3 style={{ fontWeight: 600, fontSize: '1rem', marginBottom: '0.25rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {p.name}
          </h3>
          {p.description && (
            <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {p.description}
            </p>
          )}
        </div>
        <div style={{ display: 'flex', gap: '0.3rem', flexShrink: 0 }}>
          <span className={`badge ${p.task_type === 'detection' ? 'badge-yellow' : 'badge-blue'}`}>
            {p.task_type}
          </span>
          <span className="badge" style={{ background: 'rgba(255,255,255,0.08)', color: 'var(--text-secondary)' }}>
            v{p.version}
          </span>
        </div>
      </div>

      {/* Progress bar */}
      <div style={{ marginBottom: '0.75rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>
          <span>{p.labeled_count} / {p.sample_count} labeled</span>
          <span>{pct}%</span>
        </div>
        <div className="progress-bar">
          <div className="progress-fill" style={{ width: `${pct}%` }} />
        </div>
      </div>

      {/* Footer */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
        <span>{p.created_by || 'unknown'}</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <span>{new Date(p.created_at).toLocaleDateString()}</span>
          <button
            onClick={(e) => onDelete(e, p.id, p.name)}
            title="Delete project"
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--text-muted)',
              cursor: 'pointer',
              padding: '0.2rem',
              fontSize: '0.85rem',
              lineHeight: 1,
            }}
          >
            &#x2715;
          </button>
        </div>
      </div>
    </div>
  );
}

export default function ProjectsPage() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    fetchProjects()
      .then(setProjects)
      .catch((e) => setError(e.response?.data?.detail || e.message))
      .finally(() => setLoading(false));
  }, []);

  const handleDelete = async (e, id, name) => {
    e.stopPropagation();
    if (!confirm(`Delete project "${name}"? This cannot be undone.`)) return;
    try {
      await deleteProject(id);
      setProjects((prev) => prev.filter((p) => p.id !== id));
    } catch (err) {
      alert(err.response?.data?.detail || err.message);
    }
  };

  // Group projects: root projects (no parent) with their versions nested below
  const grouped = useMemo(() => {
    const byId = Object.fromEntries(projects.map((p) => [p.id, p]));
    const childrenOf = {};
    const roots = [];

    for (const p of projects) {
      if (p.parent_project_id && byId[p.parent_project_id]) {
        // Find the ultimate root of the lineage
        let rootId = p.parent_project_id;
        while (byId[rootId]?.parent_project_id && byId[byId[rootId].parent_project_id]) {
          rootId = byId[rootId].parent_project_id;
        }
        if (!childrenOf[rootId]) childrenOf[rootId] = [];
        childrenOf[rootId].push(p);
      } else if (!p.parent_project_id) {
        roots.push(p);
      } else {
        // Parent not in list (deleted?) — treat as root
        roots.push(p);
      }
    }

    // Sort children by version
    for (const id of Object.keys(childrenOf)) {
      childrenOf[id].sort((a, b) => a.version - b.version);
    }

    // Sort roots by creation date (newest first)
    roots.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));

    return roots.map((root) => ({
      root,
      versions: childrenOf[root.id] || [],
    }));
  }, [projects]);

  if (loading) return <Spinner label="Loading projects..." />;

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1.5rem' }}>
        <div>
          <h1 style={{ fontSize: '1.75rem', fontWeight: 700, marginBottom: '0.25rem' }}>
            Projects
          </h1>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
            Create and manage image labeling projects.
          </p>
        </div>
        <button className="btn-primary" onClick={() => navigate('/projects/new')}>
          + New Project
        </button>
      </div>

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

      {projects.length === 0 && !error ? (
        <div style={{
          textAlign: 'center',
          padding: '4rem 2rem',
          background: 'var(--bg-card)',
          borderRadius: 12,
          border: '1px solid var(--border-color)',
        }}>
          <div style={{ fontSize: '2.5rem', marginBottom: '1rem', opacity: 0.4 }}>&#x1F4F7;</div>
          <h3 style={{ fontWeight: 600, marginBottom: '0.5rem' }}>No projects yet</h3>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '1.5rem' }}>
            Create your first labeling project to get started.
          </p>
          <button className="btn-primary" onClick={() => navigate('/projects/new')}>
            + Create Project
          </button>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
          {grouped.map(({ root, versions }) => (
            <div key={root.id}>
              <ProjectCard p={root} navigate={navigate} onDelete={handleDelete} />
              {versions.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginTop: '0.5rem' }}>
                  {versions.map((v) => (
                    <ProjectCard key={v.id} p={v} navigate={navigate} onDelete={handleDelete} indent />
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
