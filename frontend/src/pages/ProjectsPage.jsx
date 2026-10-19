/**
 * Projects list page — collapsible project groups with version rows.
 */

import { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchProjects, deleteProject, archiveProject, unarchiveProject } from '../api/client';
import { humanizeApiError } from '../api/errors';
import Spinner from '../components/Spinner';
import { taskTypeBadgeClass } from '../utils/taskType';

/**
 * A single version row inside an expanded project group.
 */
function VersionRow({ p, navigate, onDelete, onArchive, onUnarchive }) {
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
      <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', flexShrink: 0, width: p.consensus_enabled ? '8rem' : '5rem', textAlign: 'right' }}>
        {p.consensus_enabled
          ? `${p.consensus_resolved_items}/${p.sample_count} resolved`
          : `${p.labeled_count}/${p.sample_count}`}
      </span>
      <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', flexShrink: 0, width: '2.5rem', textAlign: 'right' }}>
        {pct}%
      </span>
      {p.archived_at ? (
        <button
          className="btn-secondary"
          onClick={(e) => { e.stopPropagation(); onUnarchive(p.id); }}
          title="Restore this version"
          style={{ padding: '0.2rem 0.5rem', fontSize: '0.7rem', flexShrink: 0 }}
        >
          Restore
        </button>
      ) : (
        <button
          className="btn-secondary"
          onClick={(e) => { e.stopPropagation(); onArchive(p.id, p.name); }}
          title="Archive this version"
          style={{ padding: '0.2rem 0.5rem', fontSize: '0.7rem', flexShrink: 0 }}
        >
          Archive
        </button>
      )}
      <button
        onClick={(e) => { e.stopPropagation(); onDelete(e, p.id, p.name); }}
        title="Delete version permanently"
        style={{
          background: 'none', border: 'none', color: '#f87171',
          cursor: 'pointer', padding: '0.2rem 0.35rem', fontSize: '0.75rem', lineHeight: 1, flexShrink: 0,
        }}
      >
        Delete
      </button>
    </div>
  );
}

/**
 * Collapsible project group — shows aggregate stats when collapsed,
 * version rows when expanded.
 */
function ProjectGroup({
  root, allVersions, navigate, onDelete, onArchive, onUnarchive, onArchiveGroup, onDeleteGroup,
}) {
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
  const totalConsensusTasks = all.reduce((s, p) => s + (p.consensus_total_tasks || 0), 0);
  const totalSubmittedTasks = all.reduce((s, p) => s + (p.consensus_submitted_tasks || 0), 0);
  const totalReviewItems = all.reduce((s, p) => s + (p.consensus_review_items || 0), 0);
  const aggPct = totalSamples > 0 ? Math.round((totalLabeled / totalSamples) * 100) : 0;

  // Use the root's base name (strip version suffix if present)
  const baseName = root.name.replace(/\s*\(v\d+\)\s*$/, '');
  const allArchived = all.every((p) => p.archived_at);
  const someArchived = all.some((p) => p.archived_at);

  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden', opacity: allArchived ? 0.72 : 1 }}>
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
            <span className={`badge ${taskTypeBadgeClass(root.task_type)}`}>
              {root.task_type}
            </span>
            {root.consensus_enabled && (
              <>
                <span className="badge badge-purple">blind consensus</span>
                <span className="badge badge-blue">{root.consensus_role}</span>
              </>
            )}
            {allArchived && <span className="badge">archived</span>}
            {!allArchived && someArchived && <span className="badge">has archived versions</span>}
          </div>
          {root.description && !expanded && (
            <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', margin: '0.15rem 0 0 0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {root.description}
            </p>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexShrink: 0 }}>
          {!expanded && (
            <>
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
              <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', minWidth: root.consensus_enabled ? '18rem' : '5rem', textAlign: 'right' }}>
                {root.consensus_enabled
                  ? `${totalSubmittedTasks}/${totalConsensusTasks} votes · ${totalLabeled}/${totalSamples} resolved${totalReviewItems ? ` · ${totalReviewItems} review` : ''}`
                  : `${totalLabeled}/${totalSamples}`}
              </span>
            </>
          )}
          {allArchived ? (
            <button
              className="btn-secondary"
              onClick={(e) => { e.stopPropagation(); onUnarchive(root.id, true); }}
              style={{ padding: '0.25rem 0.6rem', fontSize: '0.75rem' }}
            >
              Restore
            </button>
          ) : (
            <button
              className="btn-secondary"
              onClick={(e) => {
                e.stopPropagation();
                onArchiveGroup(baseName, all);
              }}
              style={{ padding: '0.25rem 0.6rem', fontSize: '0.75rem' }}
            >
              Archive
            </button>
          )}
          <button
            onClick={(e) => {
              e.stopPropagation();
              onDeleteGroup(e, baseName, all);
            }}
            title="Delete project permanently"
            style={{
              background: 'none', border: 'none', color: '#f87171',
              cursor: 'pointer', padding: '0.25rem 0.4rem', fontSize: '0.75rem',
            }}
          >
            Delete
          </button>
        </div>
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
            <span style={{ width: root.consensus_enabled ? '8rem' : '5rem', textAlign: 'right' }}>{root.consensus_enabled ? 'Resolved' : 'Labeled'}</span>
            <span style={{ width: '2.5rem', textAlign: 'right' }}>%</span>
            <span style={{ width: '1rem' }}></span>
          </div>
          {all.map((v) => (
            <VersionRow
              key={v.id}
              p={v}
              navigate={navigate}
              onDelete={onDelete}
              onArchive={onArchive}
              onUnarchive={onUnarchive}
            />
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
  const [showArchived, setShowArchived] = useState(false);

  const loadProjects = (includeArchived = showArchived) => {
    setLoading(true);
    fetchProjects({ includeArchived })
      .then(setProjects)
      .catch((e) => setError(humanizeApiError(e)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    fetchProjects({ includeArchived: showArchived })
      .then(setProjects)
      .catch((e) => setError(humanizeApiError(e)))
      .finally(() => setLoading(false));
  }, [showArchived]);

  const handleDelete = async (e, id, name) => {
    e.stopPropagation();
    if (!confirm(`Permanently delete "${name}"? Labels in this app are removed. Source images and exported training datasets on the volume are kept. This cannot be undone.`)) return;
    try {
      await deleteProject(id);
      setProjects((prev) => prev.filter((p) => p.id !== id));
    } catch (err) {
      alert(humanizeApiError(err));
    }
  };

  const handleDeleteGroup = async (e, name, versions) => {
    e.stopPropagation();
    const n = versions.length;
    if (!confirm(`Permanently delete "${name}"${n > 1 ? ` and all versions` : ''}? Labels in this app are removed. Source images and exported training datasets on the volume are kept. This cannot be undone.`)) return;
    try {
      await deleteProject(versions[0].id, { allVersions: true });
      const ids = new Set(versions.map((p) => p.id));
      setProjects((prev) => prev.filter((p) => !ids.has(p.id)));
    } catch (err) {
      alert(humanizeApiError(err));
      loadProjects();
    }
  };

  const handleArchive = async (id, name) => {
    if (!confirm(`Archive "${name}"? It will be hidden from the default list. You can restore it later.`)) return;
    try {
      const updated = await archiveProject(id);
      setProjects((prev) => {
        const next = prev.map((p) => (p.id === id ? { ...p, ...updated } : p));
        return showArchived ? next : next.filter((p) => p.id !== id);
      });
    } catch (err) {
      alert(humanizeApiError(err));
    }
  };

  const handleArchiveGroup = async (name, versions) => {
    const active = versions.filter((p) => !p.archived_at);
    if (active.length === 0) return;
    if (!confirm(`Archive "${name}"${active.length > 1 ? ` (${active.length} versions)` : ''}? Hidden from the default list until restored.`)) return;
    try {
      await archiveProject(active[0].id, { allVersions: true });
      const ids = new Set(versions.map((p) => p.id));
      setProjects((prev) => {
        if (!showArchived) return prev.filter((p) => !ids.has(p.id));
        return prev.map((p) => (
          ids.has(p.id) ? { ...p, archived_at: p.archived_at || new Date().toISOString() } : p
        ));
      });
    } catch (err) {
      alert(humanizeApiError(err));
    }
  };

  const handleUnarchive = async (id, allVersions = false) => {
    try {
      const updated = await unarchiveProject(id, { allVersions });
      if (allVersions) {
        loadProjects();
        return;
      }
      setProjects((prev) => prev.map((p) => (p.id === id ? { ...p, ...updated } : p)));
    } catch (err) {
      alert(humanizeApiError(err));
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
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.85rem', color: 'var(--text-secondary)', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)}
            />
            Show archived
          </label>
          <button className="btn-primary" onClick={() => navigate('/projects/new')}>
            + New Project
          </button>
        </div>
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
          <h3 style={{ fontWeight: 600, marginBottom: '0.5rem' }}>
            {showArchived ? 'No archived projects' : 'No projects yet'}
          </h3>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '1.5rem' }}>
            {showArchived
              ? 'Archived projects will show up here. Uncheck “Show archived” to go back to the active list.'
              : 'Create your first labeling project to get started.'}
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
              onArchive={handleArchive}
              onUnarchive={handleUnarchive}
              onArchiveGroup={handleArchiveGroup}
              onDeleteGroup={handleDeleteGroup}
            />
          ))}
        </div>
      )}
    </div>
  );
}
