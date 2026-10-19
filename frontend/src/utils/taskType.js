export function taskTypeBadgeClass(taskType) {
  if (taskType === 'classification') return 'badge-blue';
  if (taskType === 'segmentation') return 'badge-teal';
  return 'badge-yellow';
}
