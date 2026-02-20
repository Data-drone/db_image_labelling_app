/**
 * Reusable dataset selector dropdown.
 */

import { useState, useEffect } from 'react';
import { fetchDatasets } from '../api/client';

export default function DatasetSelector({ value, onChange }) {
  const [datasets, setDatasets] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchDatasets()
      .then(setDatasets)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <select
        disabled
        style={{
          background: 'var(--bg-input)',
          color: 'var(--text-muted)',
          border: '1px solid var(--border-color)',
          borderRadius: 8,
          padding: '0.5rem 1rem',
          fontSize: '0.875rem',
        }}
      >
        <option>Loading datasets...</option>
      </select>
    );
  }

  if (datasets.length === 0) {
    return (
      <div style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>
        No datasets found. Create one to get started.
      </div>
    );
  }

  return (
    <select
      value={value || ''}
      onChange={(e) => {
        const ds = datasets.find((d) => d.id === Number(e.target.value));
        if (ds) onChange(ds);
      }}
      style={{
        background: 'var(--bg-input)',
        color: 'var(--text-primary)',
        border: '1px solid var(--border-color)',
        borderRadius: 8,
        padding: '0.5rem 1rem',
        fontSize: '0.875rem',
        minWidth: 200,
        cursor: 'pointer',
      }}
    >
      <option value="" disabled>
        Select a dataset...
      </option>
      {datasets.map((ds) => (
        <option key={ds.id} value={ds.id}>
          {ds.name} ({ds.sample_count} samples)
        </option>
      ))}
    </select>
  );
}
