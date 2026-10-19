import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import Spinner from '../components/Spinner';
import SpatialConsensusReview from '../components/SpatialConsensusReview';
import {
  fetchConsensusReviewCase,
  fetchConsensusReviewQueue,
  fetchProject,
  resolveConsensusReview,
  sampleImageUrl,
} from '../api/client';
import { humanizeApiError } from '../api/errors';

export default function ConsensusReview() {
  const { id: projectId } = useParams();
  const navigate = useNavigate();
  const [project, setProject] = useState(null);
  const [cases, setCases] = useState([]);
  const [selectedCaseId, setSelectedCaseId] = useState(null);
  const [caseDetail, setCaseDetail] = useState(null);
  const [editedLabels, setEditedLabels] = useState(new Set());
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [projectResult, queue] = await Promise.all([
        fetchProject(projectId),
        fetchConsensusReviewQueue(projectId, { page: 0, page_size: 200 }),
      ]);
      setProject(projectResult);
      setCases(queue.items || []);
      setSelectedCaseId((current) => {
        if (queue.items?.some((item) => item.case_id === current)) return current;
        return queue.items?.[0]?.case_id ?? null;
      });
    } catch (err) {
      setError(humanizeApiError(err));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!selectedCaseId) {
      setCaseDetail(null);
      return;
    }
    let cancelled = false;
    fetchConsensusReviewCase(projectId, selectedCaseId)
      .then((detail) => {
        if (!cancelled) setCaseDetail(detail);
      })
      .catch((err) => {
        if (!cancelled) setError(humanizeApiError(err));
      });
    return () => { cancelled = true; };
  }, [projectId, selectedCaseId]);

  const selectedSummary = cases.find((item) => item.case_id === selectedCaseId) || null;
  const selectedCase = caseDetail?.case_id === selectedCaseId ? caseDetail : selectedSummary;

  useEffect(() => {
    setEditedLabels(new Set());
  }, [selectedCaseId]);

  const act = async (action, extra = {}) => {
    if (!selectedCase || saving) return;
    setSaving(true);
    setError('');
    try {
      const payload = { action, ...extra };
      if (action === 'edit' && selectedCase.task_type === 'classification') {
        payload.labels = [...editedLabels];
      }
      await resolveConsensusReview(projectId, selectedCase.case_id, payload);
      setCaseDetail(null);
      await load();
    } catch (err) {
      setError(humanizeApiError(err));
    } finally {
      setSaving(false);
    }
  };

  if (loading && !project) return <Spinner label="Loading review queue..." />;

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1rem' }}>
        <button className="btn-secondary" onClick={() => navigate(`/projects/${projectId}`)}>
          Back
        </button>
        <div>
          <h2 style={{ margin: 0, fontSize: '1.25rem' }}>Consensus review</h2>
          <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>
            {project?.name} · {cases.length} case{cases.length === 1 ? ' needs' : 's need'} review
          </div>
        </div>
      </div>

      {error && (
        <div role="alert" style={{
          padding: '0.75rem', marginBottom: '1rem', borderRadius: 8,
          border: '1px solid rgba(239,68,68,.45)', color: '#fca5a5',
        }}>
          {error}
        </div>
      )}

      {!selectedCase ? (
        <div className="card" style={{ padding: '2rem', textAlign: 'center' }}>
          <h3 style={{ marginTop: 0 }}>Review queue is clear</h3>
          <p style={{ color: 'var(--text-muted)', marginBottom: 0 }}>
            Tied and below-quorum items will appear here.
          </p>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(180px, 260px) 1fr', gap: '1rem' }}>
          <aside className="card" style={{ padding: '0.75rem', maxHeight: '75vh', overflowY: 'auto' }}>
            {cases.map((item) => (
              <button
                key={item.case_id}
                className="btn-secondary"
                onClick={() => setSelectedCaseId(item.case_id)}
                style={{
                  width: '100%', textAlign: 'left', marginBottom: '0.4rem',
                  borderColor: item.case_id === selectedCase.case_id ? 'var(--accent-blue)' : undefined,
                }}
              >
                <div style={{ fontWeight: 600 }}>{item.filename}</div>
                <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                  {item.submissions.length} of {item.required_votes} votes
                </div>
              </button>
            ))}
          </aside>

          <main>
            {selectedCase.task_type !== 'classification' ? (
              <SpatialConsensusReview
                key={`${selectedCase.case_id}-${caseDetail?.case_id === selectedCase.case_id ? 'detail' : 'summary'}`}
                projectId={projectId}
                project={project}
                reviewCase={selectedCase}
                saving={saving}
                onAct={act}
              />
            ) : (
              <>
            <div className="card" style={{ padding: '1rem', marginBottom: '1rem' }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'minmax(260px, 1.2fr) 1fr', gap: '1rem' }}>
                <div style={{
                  minHeight: 320, display: 'flex', alignItems: 'center', justifyContent: 'center',
                  borderRadius: 8, overflow: 'hidden', background: 'var(--bg-secondary)',
                }}>
                  <img
                    src={sampleImageUrl(projectId, selectedCase.sample_id)}
                    alt={selectedCase.filename}
                    style={{ maxWidth: '100%', maxHeight: 520, objectFit: 'contain' }}
                  />
                </div>
                <div>
                  <h3 style={{ marginTop: 0 }}>{selectedCase.filename}</h3>
                  <p style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>
                    Pick a submitted label set, enter a corrected set, reject the item,
                    or request one additional blind vote.
                  </p>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
                    {selectedCase.submissions.map((submission) => (
                      <div key={submission.task_id} style={{
                        padding: '0.75rem', border: '1px solid var(--border-color)',
                        borderRadius: 8, background: 'var(--bg-secondary)',
                      }}>
                        <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginBottom: '0.35rem' }}>
                          {submission.submitted_by}
                        </div>
                        <div style={{ fontWeight: 600, marginBottom: '0.5rem' }}>
                          {submission.labels.length ? submission.labels.join(', ') : 'No labels'}
                        </div>
                        <button
                          className="btn-primary"
                          disabled={saving}
                          onClick={() => act('pick', { source_task_id: submission.task_id })}
                          style={{ fontSize: '0.78rem' }}
                        >
                          Pick this result
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>

            <div className="card" style={{ padding: '1rem' }}>
              <h3 style={{ marginTop: 0, fontSize: '1rem' }}>Corrected result</h3>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', marginBottom: '1rem' }}>
                {(project?.class_list || []).map((label) => {
                  const checked = editedLabels.has(label);
                  return (
                    <label key={label} style={{
                      display: 'flex', alignItems: 'center', gap: '0.35rem',
                      padding: '0.4rem 0.65rem', border: '1px solid var(--border-color)',
                      borderRadius: 6, cursor: 'pointer',
                    }}>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => setEditedLabels((current) => {
                          const next = new Set(current);
                          if (next.has(label)) next.delete(label);
                          else next.add(label);
                          return next;
                        })}
                      />
                      {label}
                    </label>
                  );
                })}
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
                <button
                  className="btn-primary"
                  disabled={saving || editedLabels.size === 0}
                  onClick={() => act('edit')}
                >
                  Save corrected result
                </button>
                <button className="btn-secondary" disabled={saving} onClick={() => act('relabel')}>
                  Request another label
                </button>
                <button
                  className="btn-secondary"
                  disabled={saving}
                  onClick={() => {
                    if (window.confirm('Reject this item and exclude it from exports?')) act('reject');
                  }}
                  style={{ color: '#f87171' }}
                >
                  Reject item
                </button>
              </div>
            </div>
              </>
            )}
          </main>
        </div>
      )}
    </div>
  );
}
