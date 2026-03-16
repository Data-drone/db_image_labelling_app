/**
 * Admin page — database status, Lakebase provisioning, and system info.
 */

import { useState, useEffect, useCallback } from 'react';
import Spinner from '../components/Spinner';
import {
  fetchDbStatus,
  fetchLakebaseStatus,
  provisionLakebase,
  connectLakebase,
} from '../api/client';

export default function AdminPage() {
  const [dbStatus, setDbStatus] = useState(null);
  const [lakebaseStatus, setLakebaseStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Provision form
  const [newProjectId, setNewProjectId] = useState('cv-explorer');
  const [newDisplayName, setNewDisplayName] = useState('CV Explorer');
  const [provisioning, setProvisioning] = useState(false);
  const [provisionMsg, setProvisionMsg] = useState('');

  // Connect
  const [connecting, setConnecting] = useState('');
  const [connectMsg, setConnectMsg] = useState('');

  const loadStatus = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [db, lb] = await Promise.all([fetchDbStatus(), fetchLakebaseStatus()]);
      setDbStatus(db);
      setLakebaseStatus(lb);
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadStatus(); }, [loadStatus]);

  const handleProvision = async () => {
    if (!newProjectId.trim()) return;
    setProvisioning(true);
    setProvisionMsg('');
    try {
      const result = await provisionLakebase(newProjectId.trim(), newDisplayName.trim());
      setProvisionMsg(result.message || JSON.stringify(result));
      await loadStatus();
    } catch (e) {
      setProvisionMsg('Error: ' + (e.response?.data?.detail || e.message));
    } finally {
      setProvisioning(false);
    }
  };

  const handleConnect = async (projectId) => {
    setConnecting(projectId);
    setConnectMsg('');
    try {
      const result = await connectLakebase(projectId);
      setConnectMsg(result.message || 'Connected!');
      await loadStatus();
    } catch (e) {
      setConnectMsg('Error: ' + (e.response?.data?.detail || e.message));
    } finally {
      setConnecting('');
    }
  };

  if (loading) return <Spinner label="Loading admin status..." />;

  return (
    <div style={{ maxWidth: 800, margin: '0 auto' }}>
      <h1 style={{ fontSize: '1.75rem', fontWeight: 700, marginBottom: '0.25rem' }}>
        Admin
      </h1>
      <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '2rem' }}>
        Database configuration, Lakebase provisioning, and system status.
      </p>

      {error && (
        <div style={errorStyle}>{error}</div>
      )}

      {/* Current Database Status */}
      <section style={sectionStyle}>
        <h2 style={sectionHeading}>Current Database</h2>

        <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
          <StatusCard
            label="Backend"
            value={dbStatus?.backend || 'unknown'}
            color={dbStatus?.backend === 'lakebase' ? 'var(--status-success)' : 'var(--status-warning)'}
          />
          <StatusCard
            label="Status"
            value={dbStatus?.connected ? 'Connected' : 'Disconnected'}
            color={dbStatus?.connected ? 'var(--status-success)' : 'var(--status-error)'}
          />
        </div>

        <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '1rem' }}>
          {dbStatus?.detail}
        </div>

        {dbStatus?.tables && (
          <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
            {Object.entries(dbStatus.tables).map(([name, count]) => (
              <div key={name} style={metricCard}>
                <div style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--accent-blue)' }}>{count}</div>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', textTransform: 'capitalize' }}>{name}</div>
              </div>
            ))}
          </div>
        )}

        {dbStatus?.backend === 'sqlite' && (
          <div style={warningBox}>
            SQLite data is stored at {dbStatus?.path || '/tmp/cv_explorer.db'} and will be lost when the app redeploys.
            Connect to Lakebase below for persistent storage.
          </div>
        )}
      </section>

      {/* Lakebase Projects */}
      <section style={sectionStyle}>
        <h2 style={sectionHeading}>Lakebase Projects</h2>

        {lakebaseStatus?.available === false ? (
          <div style={warningBox}>
            Lakebase is not available on this workspace: {lakebaseStatus?.error}
          </div>
        ) : (
          <>
            {lakebaseStatus?.projects?.length > 0 ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginBottom: '1.5rem' }}>
                {lakebaseStatus.projects.map((proj) => {
                  const projId = proj.name?.replace('projects/', '') || '';
                  const isActive = proj.state?.includes('ACTIVE');
                  const isCurrentBackend = dbStatus?.backend === 'lakebase' && dbStatus?.host === proj.host;

                  return (
                    <div key={proj.name} style={projectRow}>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontWeight: 600, fontSize: '0.9rem' }}>
                          {proj.display_name || projId}
                        </div>
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                          {projId} &middot; {proj.endpoints || 0} endpoint{proj.endpoints !== 1 ? 's' : ''}
                          {proj.host && ` · ${proj.host}`}
                        </div>
                      </div>
                      <span style={{
                        ...badgeStyle,
                        background: isActive ? 'rgba(0,148,0,0.15)' : 'rgba(230,167,0,0.15)',
                        color: isActive ? '#00c853' : '#ffa726',
                      }}>
                        {proj.state?.replace('ProjectState.', '') || 'unknown'}
                      </span>
                      {isCurrentBackend ? (
                        <span style={{ ...badgeStyle, background: 'rgba(66,153,224,0.15)', color: 'var(--accent-blue)' }}>
                          Active
                        </span>
                      ) : isActive ? (
                        <button
                          className="btn-primary"
                          onClick={() => handleConnect(projId)}
                          disabled={connecting === projId}
                          style={{ padding: '0.35rem 0.75rem', fontSize: '0.8rem' }}
                        >
                          {connecting === projId ? 'Connecting...' : 'Connect'}
                        </button>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            ) : (
              <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '1.5rem' }}>
                No Lakebase projects found.
              </div>
            )}

            {connectMsg && (
              <div style={{ ...infoBox, marginBottom: '1rem' }}>{connectMsg}</div>
            )}

            {/* Provision new */}
            <h3 style={{ fontSize: '0.9rem', fontWeight: 600, marginBottom: '0.75rem', color: 'var(--text-secondary)' }}>
              Create New Lakebase Project
            </h3>
            <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '0.5rem' }}>
              <div style={{ flex: 1, minWidth: 150 }}>
                <label style={labelStyle}>Project ID</label>
                <input
                  type="text"
                  value={newProjectId}
                  onChange={(e) => setNewProjectId(e.target.value)}
                  style={inputStyle}
                  placeholder="cv-explorer"
                />
              </div>
              <div style={{ flex: 1, minWidth: 150 }}>
                <label style={labelStyle}>Display Name</label>
                <input
                  type="text"
                  value={newDisplayName}
                  onChange={(e) => setNewDisplayName(e.target.value)}
                  style={inputStyle}
                  placeholder="CV Explorer"
                />
              </div>
              <div style={{ alignSelf: 'flex-end' }}>
                <button
                  className="btn-primary"
                  onClick={handleProvision}
                  disabled={provisioning || !newProjectId.trim()}
                  style={{ padding: '0.5rem 1rem', whiteSpace: 'nowrap' }}
                >
                  {provisioning ? 'Creating...' : 'Create Project'}
                </button>
              </div>
            </div>
            {provisionMsg && (
              <div style={infoBox}>{provisionMsg}</div>
            )}
          </>
        )}
      </section>

      {/* Lakehouse Sync Info */}
      <section style={sectionStyle}>
        <h2 style={sectionHeading}>Lakehouse Sync</h2>
        <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
          {dbStatus?.backend === 'lakebase' ? (
            <>
              <p style={{ marginBottom: '0.75rem' }}>
                Lakehouse Sync replicates your Lakebase tables to Delta tables in Unity Catalog
                automatically via CDC. Once enabled, annotation data is queryable from notebooks
                and available for model training without any export step.
              </p>
              <p style={{ marginBottom: '0.75rem' }}>
                <strong style={{ color: 'var(--text-primary)' }}>To enable:</strong>
              </p>
              <ol style={{ paddingLeft: '1.25rem', marginBottom: '0.75rem' }}>
                <li>Open your Lakebase project in the Databricks UI</li>
                <li>Go to Branch Overview &rarr; Lakehouse Sync tab</li>
                <li>Click Start Sync, choose a destination catalog and schema</li>
              </ol>
              <p>
                Delta tables created:
                <code style={codeStyle}>lb_labeling_projects_history</code>,
                <code style={codeStyle}>lb_project_samples_history</code>,
                <code style={codeStyle}>lb_annotations_history</code>
              </p>
            </>
          ) : (
            <p>
              Connect to Lakebase first to enable Lakehouse Sync.
              Lakehouse Sync requires a Lakebase (PostgreSQL) backend — it is not available with SQLite.
            </p>
          )}
        </div>
      </section>

      {/* Refresh */}
      <div style={{ marginTop: '1rem' }}>
        <button className="btn-secondary" onClick={loadStatus} style={{ fontSize: '0.85rem' }}>
          Refresh Status
        </button>
      </div>
    </div>
  );
}

function StatusCard({ label, value, color }) {
  return (
    <div style={metricCard}>
      <div style={{ fontSize: '1rem', fontWeight: 700, color, textTransform: 'capitalize' }}>{value}</div>
      <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{label}</div>
    </div>
  );
}

const sectionStyle = {
  background: 'var(--bg-card)',
  border: '1px solid var(--border-color)',
  borderRadius: 10,
  padding: '1.25rem',
  marginBottom: '1.5rem',
};

const sectionHeading = {
  fontSize: '1rem',
  fontWeight: 600,
  marginBottom: '1rem',
  paddingBottom: '0.5rem',
  borderBottom: '1px solid var(--border-color)',
};

const metricCard = {
  background: 'var(--bg-secondary)',
  borderRadius: 8,
  padding: '0.75rem 1rem',
  minWidth: 80,
  textAlign: 'center',
};

const projectRow = {
  display: 'flex',
  alignItems: 'center',
  gap: '0.75rem',
  padding: '0.75rem 1rem',
  background: 'var(--bg-secondary)',
  borderRadius: 8,
  border: '1px solid var(--border-color)',
};

const badgeStyle = {
  padding: '0.2rem 0.6rem',
  borderRadius: 9999,
  fontSize: '0.7rem',
  fontWeight: 600,
  whiteSpace: 'nowrap',
};

const warningBox = {
  background: 'rgba(230, 167, 0, 0.1)',
  border: '1px solid rgba(230, 167, 0, 0.3)',
  borderRadius: 8,
  padding: '0.75rem 1rem',
  marginTop: '0.75rem',
  fontSize: '0.85rem',
  color: '#ffa726',
};

const infoBox = {
  background: 'rgba(66, 153, 224, 0.1)',
  border: '1px solid rgba(66, 153, 224, 0.3)',
  borderRadius: 8,
  padding: '0.75rem 1rem',
  fontSize: '0.85rem',
  color: 'var(--accent-blue)',
};

const errorStyle = {
  background: 'rgba(255, 50, 50, 0.1)',
  border: '1px solid rgba(255, 50, 50, 0.3)',
  borderRadius: 8,
  padding: '0.75rem 1rem',
  marginBottom: '1rem',
  color: '#ff6b6b',
  fontSize: '0.85rem',
};

const labelStyle = {
  display: 'block',
  fontSize: '0.75rem',
  fontWeight: 500,
  color: 'var(--text-secondary)',
  marginBottom: '0.25rem',
};

const inputStyle = {
  width: '100%',
  padding: '0.5rem 0.75rem',
  background: 'var(--bg-input)',
  color: 'var(--text-primary)',
  border: '1px solid var(--border-color)',
  borderRadius: 6,
  fontSize: '0.85rem',
};

const codeStyle = {
  background: 'var(--bg-secondary)',
  padding: '0.15rem 0.4rem',
  borderRadius: 4,
  fontSize: '0.8rem',
  color: 'var(--accent-blue)',
  margin: '0 0.25rem',
};
