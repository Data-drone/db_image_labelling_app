import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildConsensusAnnotations,
  buildConsensusBboxAnnotations,
  buildConsensusMaskAnnotations,
  consensusProgressPercent,
  normalizeSpatialReviewDraft,
  normalizeSpatialReviewSubmissions,
} from '../src/utils/consensus.js';

test('consensus payload is canonical and classification-only', () => {
  assert.deepEqual(
    buildConsensusAnnotations(new Set(['dog', 'cat', ' dog '])),
    [
      { label: 'cat', ann_type: 'classification' },
      { label: 'dog', ann_type: 'classification' },
    ],
  );
});

test('detection consensus payload preserves normalized geometry', () => {
  assert.deepEqual(
    buildConsensusBboxAnnotations([
      { label: 'cat', x: 0.1, y: 0.2, w: 0.3, h: 0.4 },
      { label: '', x: 0, y: 0, w: 1, h: 1 },
    ]),
    [{
      label: 'cat',
      ann_type: 'bbox',
      bbox_json: { x: 0.1, y: 0.2, w: 0.3, h: 0.4 },
    }],
  );
});

test('segmentation consensus payload keeps usable masks only', () => {
  const mask = { size: [2, 2], counts: [0, 1, 3] };
  assert.deepEqual(
    buildConsensusMaskAnnotations([
      { label: 'part', mask_json: mask },
      { label: 'empty', mask_json: null },
    ]),
    [{ label: 'part', ann_type: 'mask', mask_json: mask }],
  );
});

test('consensus progress is safe for empty and complete queues', () => {
  assert.equal(consensusProgressPercent(null), 0);
  assert.equal(consensusProgressPercent({ submitted_tasks: 1, total_tasks: 3 }), 33);
  assert.equal(consensusProgressPercent({ submitted_tasks: 4, total_tasks: 3 }), 100);
});

test('spatial review proposal normalization preserves cluster identity', () => {
  assert.deepEqual(
    normalizeSpatialReviewDraft([
      { label: 'cat', ann_type: 'bbox', cluster_id: 7, bbox_json: { x: 0, y: 0, w: 1, h: 1 } },
      { label: 'unknown', ann_type: 'mask', mask_json: { size: [1, 1], counts: [0, 1] } },
    ], ['dog', 'cat']),
    [
      {
        label: 'cat',
        ann_type: 'bbox',
        cluster_id: 7,
        bbox_json: { x: 0, y: 0, w: 1, h: 1 },
        id: 'proposal-0',
        _clusterId: 7,
        classIndex: 1,
      },
      {
        label: 'unknown',
        ann_type: 'mask',
        mask_json: { size: [1, 1], counts: [0, 1] },
        id: 'proposal-1',
        _clusterId: 1,
        classIndex: 0,
      },
    ],
  );
});

test('spatial review submission normalization assigns stable labels and colors', () => {
  const normalized = normalizeSpatialReviewSubmissions([
    { task_id: 42, annotations: [{ id: 3, label: 'cat', ann_type: 'bbox' }] },
    { task_id: 99, annotations: [{ label: 'dog', ann_type: 'bbox' }] },
  ], ['dog', 'cat'], ['blue', 'orange']);

  assert.equal(normalized[0].id, 'submission-42-3');
  assert.equal(normalized[0].displayLabel, 'S1 · cat');
  assert.equal(normalized[0].classIndex, 1);
  assert.equal(normalized[0].color, 'blue');
  assert.equal(normalized[0]._taskId, 42);
  assert.equal(normalized[1].id, 'submission-99-0');
  assert.equal(normalized[1].displayLabel, 'S2 · dog');
  assert.equal(normalized[1].color, 'orange');
  assert.equal(normalized[1].dashed, true);
});
