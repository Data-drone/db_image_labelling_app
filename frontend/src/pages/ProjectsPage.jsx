/**
 * Projects list page — collapsible project groups with version rows.
 */

import { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchProjects, deleteProject } from '../api/client';
import Spinner from '../components/Spinner';

/**
 * A single version row inside an expanded project group.
 */
function VersionRow({ p, navigate, onDelete }) {
  const pct = p.sample_count > 0
    ? Math.round((p.labeled_count / p.sample_count) * 100)
    : 0;

  return (
    <div
      onClick={() => navigate(`/projects/${p.id}`)}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '0.75rem',
        padding: '0.6rem 1rem',
        cursor: 'pointer',
        borderRadius: 6,
        background: 'rgba(255,255,255,0.02)',
        transition: 'background 0.15s',
      }}
      onMouseEnter={(e) => e.currentTarget.style.background = 'rgba(255,255,255,0.05)'}
      onMouseLeave={(e) => e.currentTarget.style.background = 'rgba(255,255,255,0.02)'}
    >
      <span className="badge" style={{ background: 'rgba(255,255,255,0.08)', color: 'var(--text-secondary)', flexShrink: 0 }}>
        v{p.version}
      </span>
      <span style={{ fontSize: '0.85rem', color: 'var(--text-primary)', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
        {p.name}
      </span>
      <div style={{ flex: '0 0 120px' }}>
        <div className="progress-bar" style={{ height: 4 }}>
          <div className="progress-fill" style={{ width: `${pct}%` }} />
        </div>
      </div>
      <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', flexShrink: 0, width: '5rem', textAlign: 'right' }}>
        {p.labeled_count}/{p.sample_count}
      </span>
      <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', flexShrink: 0, width: '2.5rem', textAlign: 'right' }}>
        {pct}%
      </span>
      <button
        onClick={(e) => { e.stopPropagation(); onDelete(e, p.id, p.name); }}
        title="Delete version"
        style={{
          background: 'none', border: 'none', color: 'var(--text-muted)',
          cursor: 'pointer', padding: '0.2rem', fontSize: '0.75rem', lineHeight: 1, flexShrink: 0,
          opacity: 0.5,
        }}
      >
        &#x2715;
      </button>
    </div>
  );
}

/**
 * Collapsible project group — shows aggregate stats when collapsed,
 * version rows when expanded.
 */
function ProjectGroup({ root, allVersions, navigate, onDelete }) {
  const [expanded, setExpanded] = useState(false);

  // All versions including root, sorted by version number
  const all = useMemo(() => {
    const combined = [root, ...allVersions];
    combined.sort((a, b) => a.version - b.version);
    return combined;
  }, [root, allVersions]);

  const versionCount = all.length;
  const latestVersion = all[all.length - 1];

  // Aggregate stats
  const totalSamples = all.reduce((s, p) => s + (p.sample_count || 0), 0);
  const totalLabeled = all.reduce((s, p) => s + (p.labeled_count || 0), 0);
  const aggPct = totalSamples > 0 ? Math.round((totalLabeled / totalSamples) * 100) : 0;

  // Use the root's base name (strip version suffix if present)
  const baseName = root.name.replace(/\s*\(v\d+\)\s*$/, '');

  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      {/* Clickable header — always visible */}
      <div
        onClick={() => setExpanded(!expanded)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.75rem',
          padding: '1rem 1.25rem',
          cursor: 'pointer',
          userSelect: 'none',
        }}
      >
        {/* Chevron */}
        <span style={{
          fontSize: '0.7rem',
          color: 'var(--text-muted)',
          transition: 'transform 0.2s',
          transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
          flexShrink: 0,
        }}>
          &#x25B6;
        </span>

        {/* Name + description */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <h3 style={{ fontWeight: 600, fontSize: '1rem', margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {baseName}
            </h3>
            <span className={`badge ${root.task_type === 'detection' ? 'badge-yellow' : 'badge-blue'}`}>
              {root.task_type}
            </span>
          </div>
          {root.description && !expanded && (
            <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', margin: '0.15rem 0 0 0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {root.description}
            </p>
          )}
        </div>

        {/* Summary badges (visible when collapsed) */}
        {!expanded && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexShrink: 0 }}>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
              {versionCount} version{versionCount !== 1 ? 's' : ''}
            </span>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
              latest: v{latestVersion.version}
            </span>
            <div style={{ width: 80 }}>
              <div className="progress-bar" style={{ height: 4 }}>
                <div className="progress-fill" style={{ width: `${aggPct}%` }} />
              </div>
            </div>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', width: '5rem', textAlign: 'right' }}>
              {totalLabeled}/{totalSamples}
            </span>
          </div>
        )}
      </div>

      {/* Expanded: version rows */}
      {expanded && (
        <div style={{
          borderTop: '1px solid var(--border-color)',
          padding: '0.5rem 1rem 0.75rem 1rem',
          display: 'flex',
          flexDirection: 'column',
          gap: '0.25rem',
        }}>
          {/* Column header */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: '0.75rem',
            padding: '0.25rem 1rem',
            fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em',
          }}>
            <span style={{ flexShrink: 0, width: '2.5rem' }}>Ver</span>
            <span style={{ flex: 1 }}>Name</span>
            <span style={{ flex: '0 0 120px' }}>Progress</span>
            <span style={{ width: '5rem', textAlign: 'right' }}>Labeled</span>
            <span style={{ width: '2.5rem', textAlign: 'right' }}>%</span>
            <span style={{ width: '1rem' }}></span>
          </div>
          {all.map((v) => (
            <VersionRow key={v.id} p={v} navigate={navigate} onDelete={onDelete} />
          ))}
        </div>
      )}
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

  // Group projects by lineage: root + its cloned versions
  const grouped = useMemo(() => {
    const byId = Object.fromEntries(projects.map((p) => [p.id, p]));
    const childrenOf = {};
    const roots = [];

    for (const p of projects) {
      if (p.parent_project_id && byId[p.parent_project_id]) {
        let rootId = p.parent_project_id;
        while (byId[rootId]?.parent_project_id && byId[byId[rootId].parent_project_id]) {
          rootId = byId[rootId].parent_project_id;
        }
        if (!childrenOf[rootId]) childrenOf[rootId] = [];
        childrenOf[rootId].push(p);
      } else if (!p.parent_project_id) {
        roots.push(p);
      } else {
        roots.push(p);
      }
    }

    for (const id of Object.keys(childrenOf)) {
      childrenOf[id].sort((a, b) => a.version - b.version);
    }

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
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {grouped.map(({ root, versions }) => (
            <ProjectGroup
              key={root.id}
              root={root}
              allVersions={versions}
              navigate={navigate}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}
    </div>
  );
}
