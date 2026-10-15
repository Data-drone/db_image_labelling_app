export function buildConsensusAnnotations(labels) {
  return [...new Set(
    [...labels]
      .map((label) => String(label).trim())
      .filter(Boolean),
  )]
    .sort()
    .map((label) => ({ label, ann_type: 'classification' }));
}

export function buildConsensusBboxAnnotations(boxes) {
  return (boxes || [])
    .filter((box) => box?.label && [box.x, box.y, box.w, box.h].every(Number.isFinite))
    .map((box) => ({
      label: String(box.label).trim(),
      ann_type: 'bbox',
      bbox_json: { x: box.x, y: box.y, w: box.w, h: box.h },
    }));
}

export function buildConsensusMaskAnnotations(layers) {
  return (layers || [])
    .filter((layer) => layer?.label && layer?.mask_json)
    .map((layer) => ({
      label: String(layer.label).trim(),
      ann_type: 'mask',
      mask_json: layer.mask_json,
    }));
}

export function consensusProgressPercent(progress) {
  const submitted = Number(progress?.submitted_tasks || 0);
  const total = Number(progress?.total_tasks || 0);
  if (total <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((submitted / total) * 100)));
}

export function normalizeSpatialReviewDraft(proposedAnnotations, classList = []) {
  return (proposedAnnotations || []).map((annotation, index) => ({
    ...annotation,
    id: `proposal-${index}`,
    _clusterId: annotation.cluster_id ?? index,
    classIndex: Math.max(0, classList.indexOf(annotation.label)),
  }));
}

export function normalizeSpatialReviewSubmissions(submissions, classList = [], colors = []) {
  return (submissions || []).flatMap((submission, submissionIndex) => (
    (submission.annotations || []).map((annotation, annotationIndex) => ({
      ...annotation,
      id: `submission-${submission.task_id}-${annotation.id ?? annotationIndex}`,
      classIndex: Math.max(0, classList.indexOf(annotation.label)),
      ...(colors.length > 0 ? { color: colors[submissionIndex % colors.length] } : {}),
      dashed: true,
      displayLabel: `S${submissionIndex + 1} · ${annotation.label}`,
      _taskId: submission.task_id,
    }))
  ));
}
