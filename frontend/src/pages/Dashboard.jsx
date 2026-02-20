/**
 * Dashboard page - Dataset statistics, class distributions, labeling progress.
 * Mirrors Streamlit page 5 (Dashboard).
 * Uses Recharts for charts.
 */

import { useState, useEffect } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  PieChart, Pie, Cell,
  ResponsiveContainer,
} from 'recharts';
import DatasetSelector from '../components/DatasetSelector';
import { fetchDatasetStats } from '../api/client';

const CHART_COLORS = [
  '#00b4d8', '#eb1600', '#00c853', '#ffa726', '#9400d3',
  '#ff1493', '#008080', '#ff6347', '#48cae4', '#dc143c',
];

export default function Dashboard() {
  const [dataset, setDataset] = useState(null);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!dataset) return;
    setLoading(true);
    fetchDatasetStats(dataset.id)
      .then(setStats)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [dataset]);

  const classData = stats
    ? Object.entries(stats.class_distribution).map(([name, count]) => ({ name, count }))
    : [];

  const tagData = stats
    ? Object.entries(stats.tag_distribution).map(([name, count]) => ({ name, count }))
    : [];

  const progressPct = stats && stats.total_samples > 0
    ? (stats.labeled_count / stats.total_samples) * 100
    : 0;

  return (
    <div>
      <h1 style={{ fontSize: '1.75rem', fontWeight: 700, marginBottom: '0.5rem' }}>
        Dashboard
      </h1>
      <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem', fontSize: '0.9rem' }}>
        Dataset statistics, class distributions, and annotation progress.
      </p>

      <div style={{ marginBottom: '1.5rem' }}>
        <DatasetSelector value={dataset?.id} onChange={setDataset} />
      </div>

      {loading && (
        <div style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-muted)' }}>
          Loading statistics...
        </div>
      )}

      {stats && !loading && (
        <>
          {/* Metric cards */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
              gap: '1rem',
              marginBottom: '2rem',
            }}
          >
            <div className="metric-card">
              <div className="metric-value">{stats.total_samples.toLocaleString()}</div>
              <div className="metric-label">Total Samples</div>
            </div>
            <div className="metric-card">
              <div className="metric-value" style={{ color: 'var(--status-success)' }}>
                {stats.labeled_count.toLocaleString()}
              </div>
              <div className="metric-label">
                Labeled ({progressPct.toFixed(0)}%)
              </div>
            </div>
            <div className="metric-card">
              <div className="metric-value" style={{ color: 'var(--status-warning)' }}>
                {stats.unlabeled_count.toLocaleString()}
              </div>
              <div className="metric-label">Unlabeled</div>
            </div>
            <div className="metric-card">
              <div className="metric-value">{stats.class_count}</div>
              <div className="metric-label">Classes</div>
            </div>
          </div>

          {/* Progress bar */}
          <div className="card" style={{ marginBottom: '2rem' }}>
            <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.75rem' }}>
              Labeling Progress
            </h3>
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                fontSize: '0.8rem',
                color: 'var(--text-secondary)',
                marginBottom: '0.5rem',
              }}
            >
              <span>{stats.labeled_count} of {stats.total_samples}</span>
              <span>{progressPct.toFixed(1)}%</span>
            </div>
            <div className="progress-bar" style={{ height: 12 }}>
              <div
                className="progress-bar-fill"
                style={{ width: `${progressPct}%` }}
              />
            </div>
            {stats.unlabeled_count > 0 && (
              <p
                style={{
                  fontSize: '0.8rem',
                  color: 'var(--text-muted)',
                  marginTop: '0.75rem',
                }}
              >
                {stats.unlabeled_count.toLocaleString()} samples still need labeling.
                Head to the Labeling page to annotate them.
              </p>
            )}
          </div>

          {/* Charts row */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: classData.length > 0 && tagData.length > 0 ? '1fr 1fr' : '1fr',
              gap: '1.5rem',
              marginBottom: '2rem',
            }}
          >
            {/* Class distribution */}
            {classData.length > 0 && (
              <div className="card">
                <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '1rem' }}>
                  Class Distribution
                </h3>
                <ResponsiveContainer width="100%" height={300}>
                  <BarChart data={classData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border-color)" />
                    <XAxis
                      dataKey="name"
                      tick={{ fill: 'var(--text-secondary)', fontSize: 12 }}
                      axisLine={{ stroke: 'var(--border-color)' }}
                    />
                    <YAxis
                      tick={{ fill: 'var(--text-secondary)', fontSize: 12 }}
                      axisLine={{ stroke: 'var(--border-color)' }}
                    />
                    <Tooltip
                      contentStyle={{
                        background: 'var(--bg-card)',
                        border: '1px solid var(--border-color)',
                        borderRadius: 8,
                        color: 'var(--text-primary)',
                      }}
                    />
                    <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                      {classData.map((_, i) => (
                        <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}

            {/* Tag distribution */}
            {tagData.length > 0 && (
              <div className="card">
                <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '1rem' }}>
                  Tag Distribution
                </h3>
                <ResponsiveContainer width="100%" height={300}>
                  <PieChart>
                    <Pie
                      data={tagData}
                      dataKey="count"
                      nameKey="name"
                      cx="50%"
                      cy="50%"
                      outerRadius={100}
                      label={({ name, percent }) =>
                        `${name} (${(percent * 100).toFixed(0)}%)`
                      }
                      labelLine={{ stroke: 'var(--text-muted)' }}
                    >
                      {tagData.map((_, i) => (
                        <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip
                      contentStyle={{
                        background: 'var(--bg-card)',
                        border: '1px solid var(--border-color)',
                        borderRadius: 8,
                        color: 'var(--text-primary)',
                      }}
                    />
                  </PieChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>

          {/* Empty state for no annotations */}
          {classData.length === 0 && tagData.length === 0 && (
            <div
              className="card"
              style={{
                textAlign: 'center',
                padding: '2rem',
                color: 'var(--text-muted)',
              }}
            >
              No annotations or tags found yet. Start labeling to see statistics here.
            </div>
          )}
        </>
      )}
    </div>
  );
}
