import { useState, useEffect, useRef } from 'react';
import {
  listExports,
  listFinetuneRuns,
  triggerFinetuneWithConfig,
} from '../api/client';
import { humanizeApiError } from '../api/errors';
import Spinner from './Spinner';

export default function FinetuneTab({ projectId, appConfig }) {
  // Export list
  const [exports, setExports] = useState([]);
  const [exportsLoading, setExportsLoading] = useState(true);
  const [selectedExport, setSelectedExport] = useState('');

  // Config
  const baseModels = appConfig?.finetune_base_models || ['facebook/sam-vit-large'];
  const [baseModel, setBaseModel] = useState(baseModels[0] || '');
  const [adapterType, setAdapterType] = useState('lora');
  const [epochs, setEpochs] = useState(appConfig?.finetune_default_epochs || 10);
  const [learningRate, setLearningRate] = useState(appConfig?.finetune_default_lr || 0.0001);
  const [ucModelName, setUcModelName] = useState(appConfig?.finetune_default_uc_model || '');

  // Run state
  const [runs, setRuns] = useState([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const pollRef = useRef(null);

  useEffect(() => {
    loadExports();
    loadRuns();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [projectId]);

  const loadExports = async () => {
    setExportsLoading(true);
    try {
      const data = await listExports(projectId);
      setExports(data);
      if (data.length > 0) setSelectedExport(data[0].export_path);
    } catch (e) {
      console.error('Failed to load exports', e);
    }
    setExportsLoading(false);
  };

  const loadRuns = async () => {
    setRunsLoading(true);
    try {
      const data = await listFinetuneRuns(projectId);
      setRuns(data);
      const active = data.find(r => ['submitting', 'queued', 'running'].includes(r.status));
      if (active) startPolling();
    } catch (e) {
      console.error('Failed to load runs', e);
    }
    setRunsLoading(false);
  };

  const startPolling = () => {
    if (pollRef.current) return;
    pollRef.current = setInterval(async () => {
      try {
        const data = await listFinetuneRuns(projectId);
        setRuns(data);
        const active = data.find(r => ['submitting', 'queued', 'running'].includes(r.status));
        if (!active) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
      } catch (e) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }, 5000);
  };

  const handleLaunch = async () => {
    if (!selectedExport) return;
    setSubmitting(true);
    setError('');
    try {
      const payload = {
        export_path: selectedExport,
        base_model: baseModel || null,
        adapter_type: adapterType,
        epochs: epochs || null,
        learning_rate: learningRate || null,
        uc_model_name: ucModelName || null,
      };
      await triggerFinetuneWithConfig(projectId, payload);
      await loadRuns();
      startPolling();
    } catch (e) {
      setError(humanizeApiError(e));
    }
    setSubmitting(false);
  };

  const activeRun = runs.find(r => ['submitting', 'queued', 'running'].includes(r.status));

  const statusColor = (status) => {
    if (status === 'succeeded') return '#10b981';
    if (status === 'failed' || status === 'cancelled') return '#ef4444';
    if (['queued', 'running', 'submitting'].includes(status)) return '#f59e0b';
    return 'var(--text-muted)';
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      {/* Active Run Banner */}
      {activeRun && (
        <div className="card" style={{
          borderLeft: '4px solid #f59e0b',
          background: 'rgba(245,158,11,0.05)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
            <Spinner size={14} />
            <span style={{ fontWeight: 600 }}>Active Run #{activeRun.id}</span>
            <span style={{ color: statusColor(activeRun.status), fontWeight: 500, fontSize: '0.85rem' }}>
              {activeRun.status}
            </span>
          </div>
          <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
            {activeRun.base_model && <span>Model: {activeRun.base_model} &middot; </span>}
            {activeRun.adapter_type && <span>Adapter: {activeRun.adapter_type} &middot; </span>}
            {activeRun.epochs && <span>Epochs: {activeRun.epochs}</span>}
          </div>
          {activeRun.databricks_run_url && (
            <a href={activeRun.databricks_run_url} target="_blank" rel="noreferrer"
              style={{ fontSize: '0.8rem', marginTop: '0.25rem', display: 'inline-block' }}>
              View in Databricks ↗
            </a>
          )}
        </div>
      )}

      {/* Launch Section */}
      <div className="card">
        <h3 style={{ fontWeight: 600, fontSize: '1rem', margin: '0 0 1rem 0' }}>
          Launch Finetuning Run
        </h3>

        {/* Export Picker */}
        <div style={{ marginBottom: '1rem' }}>
          <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.25rem' }}>
            Export Dataset
          </label>
          {exportsLoading ? (
            <Spinner size={14} />
          ) : exports.length === 0 ? (
            <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', padding: '0.5rem', background: 'var(--bg-secondary)', borderRadius: 6 }}>
              No exports available. Export a labeled dataset first from the Actions menu.
            </div>
          ) : (
            <select
              value={selectedExport}
              onChange={(e) => setSelectedExport(e.target.value)}
              style={{ width: '100%', padding: '0.5rem', borderRadius: 6, border: '1px solid var(--border-color)', fontSize: '0.85rem' }}
            >
              {exports.map((exp) => (
                <option key={exp.export_path} value={exp.export_path}>
                  {exp.project_name} v{exp.version} — {exp.image_count} images, {exp.format} ({exp.exported_at.slice(0, 10)})
                </option>
              ))}
            </select>
          )}
        </div>

        {/* Config Grid */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem', marginBottom: '1rem' }}>
          <div>
            <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.25rem' }}>
              Base Model
            </label>
            <select
              value={baseModel}
              onChange={(e) => setBaseModel(e.target.value)}
              style={{ width: '100%', padding: '0.5rem', borderRadius: 6, border: '1px solid var(--border-color)', fontSize: '0.85rem' }}
            >
              {baseModels.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </div>
          <div>
            <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.25rem' }}>
              Adapter Type
            </label>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              {['lora', 'full'].map((t) => (
                <button
                  key={t}
                  onClick={() => setAdapterType(t)}
                  style={{
                    flex: 1,
                    padding: '0.5rem',
                    borderRadius: 6,
                    border: `1px solid ${adapterType === t ? 'var(--accent-blue)' : 'var(--border-color)'}`,
                    background: adapterType === t ? 'rgba(59,130,246,0.1)' : 'var(--bg-primary)',
                    color: adapterType === t ? 'var(--accent-blue)' : 'var(--text-secondary)',
                    fontWeight: 600,
                    fontSize: '0.85rem',
                    cursor: 'pointer',
                    textTransform: 'uppercase',
                  }}
                >
                  {t === 'lora' ? 'LoRA' : 'Full'}
                </button>
              ))}
            </div>
          </div>
          <div>
            <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.25rem' }}>
              Epochs
            </label>
            <input
              type="number"
              min="1"
              max="100"
              value={epochs}
              onChange={(e) => setEpochs(parseInt(e.target.value) || 10)}
              style={{ width: '100%', padding: '0.5rem', borderRadius: 6, border: '1px solid var(--border-color)', fontSize: '0.85rem' }}
            />
          </div>
          <div>
            <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.25rem' }}>
              Learning Rate
            </label>
            <input
              type="number"
              step="0.00001"
              min="0.000001"
              max="1"
              value={learningRate}
              onChange={(e) => setLearningRate(parseFloat(e.target.value) || 0.0001)}
              style={{ width: '100%', padding: '0.5rem', borderRadius: 6, border: '1px solid var(--border-color)', fontSize: '0.85rem' }}
            />
          </div>
        </div>

        {/* UC Model Registry */}
        <div style={{ marginBottom: '1rem' }}>
          <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.25rem' }}>
            UC Model Name <span style={{ fontWeight: 400, color: 'var(--text-muted)' }}>(optional)</span>
          </label>
          <input
            type="text"
            placeholder="catalog.schema.model_name"
            value={ucModelName}
            onChange={(e) => setUcModelName(e.target.value)}
            style={{ width: '100%', padding: '0.5rem', borderRadius: 6, border: '1px solid var(--border-color)', fontSize: '0.85rem' }}
          />
          <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '0.15rem' }}>
            Unity Catalog model path for lineage tracking. The trained model will be registered here.
          </div>
        </div>

        {error && (
          <div style={{ marginBottom: '0.75rem', padding: '0.5rem 0.75rem', borderRadius: 6, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)', color: '#ef4444', fontSize: '0.8rem' }}>
            {error}
          </div>
        )}

        <button
          className="btn-primary"
          onClick={handleLaunch}
          disabled={submitting || !selectedExport || !!activeRun}
          style={{ padding: '0.6rem 1.5rem' }}
        >
          {submitting ? 'Submitting...' : activeRun ? 'Run in Progress...' : 'Launch Finetuning'}
        </button>
        {activeRun && (
          <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginLeft: '0.75rem' }}>
            Wait for the current run to finish before launching another.
          </span>
        )}
      </div>

      {/* Run History */}
      <div className="card">
        <h3 style={{ fontWeight: 600, fontSize: '1rem', margin: '0 0 0.75rem 0' }}>
          Run History
        </h3>
        {runsLoading ? (
          <Spinner size={14} />
        ) : runs.length === 0 ? (
          <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
            No finetuning runs yet.
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', fontSize: '0.8rem', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-color)' }}>
                  <th style={{ textAlign: 'left', padding: '0.4rem 0.5rem', fontWeight: 600 }}>Run</th>
                  <th style={{ textAlign: 'left', padding: '0.4rem 0.5rem', fontWeight: 600 }}>Status</th>
                  <th style={{ textAlign: 'left', padding: '0.4rem 0.5rem', fontWeight: 600 }}>Model</th>
                  <th style={{ textAlign: 'left', padding: '0.4rem 0.5rem', fontWeight: 600 }}>Adapter</th>
                  <th style={{ textAlign: 'left', padding: '0.4rem 0.5rem', fontWeight: 600 }}>Epochs</th>
                  <th style={{ textAlign: 'left', padding: '0.4rem 0.5rem', fontWeight: 600 }}>LR</th>
                  <th style={{ textAlign: 'left', padding: '0.4rem 0.5rem', fontWeight: 600 }}>Metrics</th>
                  <th style={{ textAlign: 'left', padding: '0.4rem 0.5rem', fontWeight: 600 }}>Date</th>
                  <th style={{ textAlign: 'left', padding: '0.4rem 0.5rem', fontWeight: 600 }}>Links</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id} style={{ borderBottom: '1px solid var(--border-color)' }}>
                    <td style={{ padding: '0.4rem 0.5rem' }}>#{run.id}</td>
                    <td style={{ padding: '0.4rem 0.5rem' }}>
                      <span style={{ color: statusColor(run.status), fontWeight: 500 }}>
                        {run.status}
                      </span>
                    </td>
                    <td style={{ padding: '0.4rem 0.5rem', maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {run.base_model || '—'}
                    </td>
                    <td style={{ padding: '0.4rem 0.5rem' }}>{run.adapter_type || '—'}</td>
                    <td style={{ padding: '0.4rem 0.5rem' }}>{run.epochs || '—'}</td>
                    <td style={{ padding: '0.4rem 0.5rem' }}>{run.learning_rate || '—'}</td>
                    <td style={{ padding: '0.4rem 0.5rem' }}>
                      {run.metrics_json ? (
                        <span title={JSON.stringify(run.metrics_json)}>
                          {Object.entries(run.metrics_json).slice(0, 2).map(([k, v]) =>
                            `${k}: ${typeof v === 'number' ? v.toFixed(4) : v}`
                          ).join(', ')}
                        </span>
                      ) : '—'}
                    </td>
                    <td style={{ padding: '0.4rem 0.5rem' }}>
                      {run.created_at ? new Date(run.created_at).toLocaleDateString() : '—'}
                    </td>
                    <td style={{ padding: '0.4rem 0.5rem', whiteSpace: 'nowrap' }}>
                      {run.databricks_run_url && (
                        <a href={run.databricks_run_url} target="_blank" rel="noreferrer" style={{ marginRight: '0.5rem' }}>
                          Job ↗
                        </a>
                      )}
                      {run.mlflow_run_id && (
                        <a href={`#mlflow/runs/${run.mlflow_run_id}`}
                          target="_blank" rel="noreferrer">
                          MLflow ↗
                        </a>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
