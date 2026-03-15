/**
 * Project Dashboard — per-project stats, progress, and per-user breakdown.
 */

import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { fetchProject, fetchProjectStats } from '../api/client';
import Spinner from '../components/Spinner';

export default function ProjectDashboard() {
  const { id: projectId } = useParams();
  const navigate = useNavigate();

  const [project, setProject] = useState(null);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);

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
        <button
          className="btn-primary"
          onClick={() => navigate(`/projects/${projectId}/label`)}
          style={{ padding: '0.6rem 1.5rem' }}
        >
          Start Labeling
        </button>
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
            <h3 style={{ fontWeight: 600, fontSize: '1rem', marginBottom: '0.75rem' }}>
              Project Info
            </h3>
            <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr', gap: '0.4rem', fontSize: '0.85rem' }}>
              <span style={{ color: 'var(--text-muted)' }}>Source</span>
              <span style={{ wordBreak: 'break-all' }}>{project.source_volume}</span>
              <span style={{ color: 'var(--text-muted)' }}>Classes</span>
              <span>
                {project.class_list.map((c) => (
                  <span key={c} className="badge badge-blue" style={{ marginRight: '0.25rem' }}>{c}</span>
                ))}
              </span>
            </div>
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
