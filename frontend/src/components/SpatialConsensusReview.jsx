import { useMemo, useState } from 'react';
import BBoxCanvas from './BBoxCanvas';
import MaskCanvas from './MaskCanvas';
import { sampleImageUrl } from '../api/client';
import {
  normalizeSpatialReviewDraft,
  normalizeSpatialReviewSubmissions,
} from '../utils/consensus';

const SUBMISSION_COLORS = [
  '#38bdf8', '#f97316', '#a78bfa', '#22c55e', '#f43f5e',
  '#eab308', '#14b8a6', '#ec4899',
];

function cleanAnnotation(annotation) {
  return {
    label: annotation.label,
    ann_type: annotation.ann_type,
    ...(annotation.bbox_json ? { bbox_json: annotation.bbox_json } : {}),
    ...(annotation.mask_json ? { mask_json: annotation.mask_json } : {}),
  };
}

export default function SpatialConsensusReview({
  projectId,
  project,
  reviewCase,
  saving,
  onAct,
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(() => normalizeSpatialReviewDraft(
    reviewCase?.proposed_annotations,
    project?.class_list,
  ));
  const [selectedId, setSelectedId] = useState(null);
  const [activeClassIndex, setActiveClassIndex] = useState(0);
  const [tool, setTool] = useState('brush');

  const comparison = useMemo(() => normalizeSpatialReviewSubmissions(
    reviewCase?.submissions,
    project?.class_list,
    SUBMISSION_COLORS,
  ), [reviewCase, project]);

  const visible = editing ? draft : comparison;
  const boxes = visible
    .filter((annotation) => annotation.ann_type === 'bbox' && annotation.bbox_json)
    .map((annotation) => ({ ...annotation, ...annotation.bbox_json }));
  const maskLayers = visible
    .filter((annotation) => annotation.ann_type === 'mask' && annotation.mask_json)
    .map((annotation) => ({ ...annotation, mask_json: annotation.mask_json }));

  const replaceClusterWithMember = (cluster, member) => {
    const submission = reviewCase.submissions.find((row) => row.task_id === member.task_id);
    const annotation = submission?.annotations?.find(
      (row) => row.id === member.task_annotation_id,
    );
    if (!annotation) return;
    setDraft((current) => [
      ...current.filter((row) => row._clusterId !== cluster.cluster_id),
      {
        ...annotation,
        id: `picked-${cluster.cluster_id}-${member.task_annotation_id}`,
        _clusterId: cluster.cluster_id,
        classIndex: Math.max(0, project.class_list.indexOf(annotation.label)),
      },
    ]);
    setEditing(true);
  };

  const saveCorrected = () => onAct('edit', {
    annotations: draft
      .filter((annotation) => annotation.ann_type === 'bbox' || annotation.mask_json)
      .map(cleanAnnotation),
  });

  const canvasProps = {
    imageSrc: sampleImageUrl(projectId, reviewCase.sample_id),
    activeClassIndex,
    classList: project.class_list || [],
  };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(260px, 340px)', gap: '1rem' }}>
      <div className="card" style={{ minHeight: 560, padding: '0.75rem' }}>
        <div style={{ height: 520 }}>
          {reviewCase.task_type === 'detection' ? (
            <BBoxCanvas
              {...canvasProps}
              boxes={boxes}
              selectedBoxId={selectedId}
              readOnly={!editing}
              onBoxSelected={setSelectedId}
              onBoxCreated={(rect) => {
                const label = project.class_list[activeClassIndex];
                const id = `review-box-${Date.now()}`;
                setDraft((current) => [...current, {
                  id, label, ann_type: 'bbox', bbox_json: rect,
                  classIndex: activeClassIndex, ...rect,
                }]);
                setSelectedId(id);
              }}
              onBoxUpdated={(id, updates) => setDraft((current) => current.map((row) => (
                row.id === id
                  ? { ...row, ...updates, bbox_json: { ...row.bbox_json, ...updates } }
                  : row
              )))}
              onBoxDeleted={(id) => setDraft((current) => current.filter((row) => row.id !== id))}
            />
          ) : (
            <MaskCanvas
              {...canvasProps}
              masks={maskLayers}
              activeMaskId={selectedId}
              readOnly={!editing}
              tool={tool}
              brushSize={24}
              onMaskSelected={setSelectedId}
              onMaskUpdated={(id, maskJson) => setDraft((current) => current.map((row) => (
                String(row.id) === String(id) ? { ...row, mask_json: maskJson } : row
              )))}
              onNeedLayer={() => {
                const id = `review-mask-${Date.now()}`;
                setDraft((current) => [...current, {
                  id,
                  label: project.class_list[activeClassIndex],
                  ann_type: 'mask',
                  mask_json: null,
                  classIndex: activeClassIndex,
                }]);
                setSelectedId(id);
              }}
              onHistoryChange={() => {}}
            />
          )}
        </div>
      </div>

      <aside className="card" style={{ padding: '0.85rem', maxHeight: 620, overflowY: 'auto' }}>
        <h3 style={{ marginTop: 0 }}>Spatial adjudication</h3>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.75rem' }}>
          Compare blind submissions, choose an object from each cluster, or edit the proposed result.
        </p>

        <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap', marginBottom: '0.75rem' }}>
          <button className="btn-secondary" onClick={() => setEditing((value) => !value)}>
            {editing ? 'Compare submissions' : 'Edit proposed result'}
          </button>
          {reviewCase.task_type === 'segmentation' && editing && (
            <select className="input" value={tool} onChange={(event) => setTool(event.target.value)}>
              <option value="brush">Brush</option>
              <option value="eraser">Eraser</option>
              <option value="polygon">Polygon</option>
            </select>
          )}
        </div>

        {editing && (
          <div style={{ display: 'flex', gap: '0.4rem', marginBottom: '0.75rem' }}>
            <select
              className="input"
              value={activeClassIndex}
              onChange={(event) => {
                const index = Number(event.target.value);
                setActiveClassIndex(index);
                if (!selectedId) return;
                const label = project.class_list[index];
                if (!label) return;
                setDraft((current) => current.map((row) => (
                  String(row.id) === String(selectedId)
                    ? { ...row, classIndex: index, label }
                    : row
                )));
              }}
            >
              {(project.class_list || []).map((label, index) => (
                <option key={label} value={index}>{label}</option>
              ))}
            </select>
            {selectedId && (
              <button
                className="btn-secondary"
                onClick={() => setDraft((current) => current.filter((row) => String(row.id) !== String(selectedId)))}
              >
                Drop selected
              </button>
            )}
          </div>
        )}

        {(reviewCase.match_report?.clusters || []).map((cluster) => (
          <div key={cluster.cluster_id} style={{
            border: '1px solid var(--border-color)', borderRadius: 7,
            padding: '0.6rem', marginBottom: '0.55rem',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.5rem' }}>
              <strong>{cluster.label}</strong>
              <span style={{ fontSize: '0.72rem', color: cluster.accepted ? 'var(--status-success)' : '#f59e0b' }}>
                {cluster.support}/{reviewCase.required_votes} · {Math.round(cluster.agreement_score * 100)}%
              </span>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem', marginTop: '0.45rem' }}>
              {cluster.members.map((member) => (
                <button
                  key={`${member.task_id}-${member.task_annotation_id}`}
                  className="btn-secondary"
                  disabled={saving}
                  onClick={() => replaceClusterWithMember(cluster, member)}
                  style={{ textAlign: 'left', fontSize: '0.72rem' }}
                >
                  Submission {reviewCase.submissions.findIndex((row) => row.task_id === member.task_id) + 1}
                  {' · '}IoU {member.iou_to_medoid.toFixed(2)}
                  {member.task_annotation_id === cluster.medoid_annotation_id ? ' · medoid' : ''}
                </button>
              ))}
            </div>
          </div>
        ))}

        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.45rem', marginTop: '0.75rem' }}>
          <button className="btn-primary" disabled={saving} onClick={saveCorrected}>
            Save corrected geometry ({draft.filter((row) => row.bbox_json || row.mask_json).length})
          </button>
          {(reviewCase.submissions || []).map((submission, index) => (
            <button
              key={submission.task_id}
              className="btn-secondary"
              disabled={saving}
              onClick={() => onAct('pick', { source_task_id: submission.task_id })}
            >
              Pick all from submission {index + 1}
            </button>
          ))}
          <button className="btn-secondary" disabled={saving} onClick={() => onAct('relabel')}>
            Request another blind vote
          </button>
          <button
            className="btn-secondary"
            disabled={saving}
            style={{ color: '#f87171' }}
            onClick={() => {
              if (window.confirm('Reject this item and exclude it from exports?')) onAct('reject');
            }}
          >
            Reject item
          </button>
        </div>
      </aside>
    </div>
  );
}
