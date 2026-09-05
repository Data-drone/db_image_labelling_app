/**
 * Tests for the annotate-batch payload builders.
 *
 * annotate-batch replaces every annotation on a sample, so a wrong payload here
 * does not fail loudly -- it deletes someone's labels. Each case below is a bug
 * that shipped or nearly shipped. Plain `node`, same rationale as rle.test.mjs.
 * `npm run test:payload`.
 */

import assert from 'node:assert/strict';
import {
  stripServerFields, preserveExcept, maskIsUsable, maskLayerPayload, statusFromSaved,
} from '../src/utils/annotationPayload.js';

let passed = 0;
let failed = 0;
function test(name, fn) {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    console.error(`FAIL ${name}\n  ${e.message}`);
  }
}

/** A 4x4 mask with a 2x2 block in the corner: 4 background, then runs. */
const goodMask = { size: [4, 4], counts: [0, 2, 2, 2, 10] };
const badMask = { size: [4, 4], counts: [1] };            // sum != 16
const negMask = { size: [4, 4], counts: [-1, 17] };       // negative run

const serverBox = {
  id: 7, sample_id: 3, project_id: 1, created_at: '2026-01-01T00:00:00',
  created_by: 'model:sam-3-1', label: 'scratch', ann_type: 'bbox',
  bbox_json: { x: 0.1, y: 0.1, w: 0.2, h: 0.2 }, mask_json: null,
  is_draft: true, confidence: 0.83,
};

// ---------------------------------------------------------------- strip

test('stripServerFields drops only the fields the server assigns', () => {
  const out = stripServerFields(serverBox);
  assert.deepEqual(Object.keys(out).sort(), [
    'ann_type', 'bbox_json', 'confidence', 'created_by', 'is_draft', 'label', 'mask_json',
  ]);
});

test('stripServerFields keeps is_draft and unknown future fields', () => {
  const out = stripServerFields({ ...serverBox, some_future_field: 42 });
  assert.equal(out.is_draft, true);
  assert.equal(out.confidence, 0.83);
  assert.equal(out.some_future_field, 42);
});

test('stripServerFields keeps created_by so model provenance survives', () => {
  // clear-drafts / accept-drafts match on the `model:` marker. Strip it and the
  // preserved draft becomes unclearable.
  assert.equal(stripServerFields(serverBox).created_by, 'model:sam-3-1');
});

// ---------------------------------------------------------------- preserve

test('preserveExcept hands back other types verbatim', () => {
  const cls = { id: 1, label: 'ok', ann_type: 'classification', is_draft: true };
  const out = preserveExcept([cls, serverBox], 'mask');
  assert.equal(out.length, 2);
  assert.equal(out[0].is_draft, true);
  assert.equal(out[1].confidence, 0.83);
  assert.ok(!('id' in out[0]));
});

test('preserveExcept drops the type the caller owns', () => {
  const mask = { id: 9, label: 'defect', ann_type: 'mask', mask_json: goodMask };
  assert.deepEqual(preserveExcept([mask], 'mask'), []);
  assert.equal(preserveExcept([mask], 'bbox').length, 1);
});

test('preserveExcept keeps owned-type records the editor cannot represent', () => {
  // A mask row with no mask_json is invisible to the mask editor, so it is not
  // in maskLayers either. It used to fall through both filters and be deleted.
  const orphan = { id: 9, label: 'defect', ann_type: 'mask', mask_json: null };
  assert.equal(preserveExcept([orphan], 'mask').length, 1);
  const boxOrphan = { id: 9, label: 'x', ann_type: 'bbox', bbox_json: null };
  assert.equal(preserveExcept([boxOrphan], 'bbox').length, 1);
});

test('preserveExcept tolerates a missing annotation list', () => {
  assert.deepEqual(preserveExcept(null, 'mask'), []);
  assert.deepEqual(preserveExcept(undefined, 'mask'), []);
});

test('preserveExcept never mutates the records it is given', () => {
  const src = [{ ...serverBox }];
  preserveExcept(src, 'mask');
  assert.equal(src[0].id, 7);
});

// ---------------------------------------------------------------- usable

test('maskIsUsable accepts a well-formed mask and rejects malformed ones', () => {
  assert.equal(maskIsUsable(goodMask), true);
  assert.equal(maskIsUsable(badMask), false);
  assert.equal(maskIsUsable(negMask), false);
  assert.equal(maskIsUsable(null), false);
  assert.equal(maskIsUsable({ size: [4, 4], counts: 'PPXa1' }), false);  // compressed
});

// ---------------------------------------------------------------- layers

const serverMask = {
  created_by: 'model:sam-3-1', label: 'defect', ann_type: 'mask',
  mask_json: goodMask, is_draft: true, bbox_json: { x: 0, y: 0, w: 0.5, h: 0.5 },
};

test('an untouched server layer is resubmitted byte-identical', () => {
  const out = maskLayerPayload({
    id: 'existing-1', label: 'defect', mask_json: goodMask,
    isDraft: true, source: serverMask, dirty: false,
  });
  assert.equal(out, serverMask);          // same object: nothing was rebuilt
  assert.equal(out.is_draft, true);       // a draft stays a draft
  assert.equal(out.created_by, 'model:sam-3-1');
});

test('an untouched unreadable layer is resubmitted rather than repaired', () => {
  const src = { ...serverMask, mask_json: badMask, is_draft: false };
  const out = maskLayerPayload({
    id: 'existing-1', label: 'defect', mask_json: badMask, source: src, dirty: false,
  });
  assert.equal(out.mask_json, badMask);
});

test('a painted layer is stored accepted', () => {
  const out = maskLayerPayload({
    id: 'new-1', label: 'defect', mask_json: goodMask,
    isDraft: false, source: null, dirty: true,
  });
  assert.deepEqual(out, {
    label: 'defect', ann_type: 'mask', mask_json: goodMask, is_draft: false,
  });
});

test('an accepted model draft is stored accepted', () => {
  // Save is the accept action for a prediction; Reject is a separate button.
  const out = maskLayerPayload({
    id: 'pred-1', label: 'defect', mask_json: goodMask,
    isDraft: true, source: null, dirty: true,
  });
  assert.equal(out.is_draft, false);
});

test('an edited server layer keeps its other fields but is no longer a draft', () => {
  const out = maskLayerPayload({
    id: 'existing-1', label: 'scratch', mask_json: goodMask,
    isDraft: false, source: serverMask, dirty: true,
  });
  assert.equal(out.label, 'scratch');
  assert.equal(out.is_draft, false);
  assert.equal(out.created_by, 'model:sam-3-1');
  assert.notEqual(out, serverMask);
});

test('an erased layer drops out so the deletion persists', () => {
  assert.equal(maskLayerPayload({
    id: 'existing-1', label: 'defect', mask_json: null, source: serverMask, dirty: true,
  }), null);
  assert.equal(maskLayerPayload({
    id: 'new-1', label: 'defect', mask_json: null, source: null, dirty: false,
  }), null);
});

test('relabelling an unreadable layer carries the original payload across', () => {
  const src = { ...serverMask, mask_json: badMask };
  const out = maskLayerPayload({
    id: 'existing-1', label: 'scratch', mask_json: badMask, source: src, dirty: true,
  });
  assert.equal(out.mask_json, badMask);   // not re-encoded from a blank buffer
  assert.equal(out.label, 'scratch');
});

test('an unreadable layer with no server record is not invented', () => {
  assert.equal(maskLayerPayload({
    id: 'pred-1', label: 'defect', mask_json: badMask, source: null, dirty: true,
  }), null);
});

// ------------------------------------------------- end-to-end payload shape

test('a mask save on a mixed sample preserves everything it does not own', () => {
  const onServer = [
    { id: 1, sample_id: 3, project_id: 1, created_at: 'x', created_by: 'model:d',
      label: 'scratch', ann_type: 'bbox', bbox_json: { x: 0, y: 0, w: 1, h: 1 },
      mask_json: null, is_draft: true },
    { id: 2, sample_id: 3, project_id: 1, created_at: 'x', created_by: 'a@b.c',
      label: 'ok', ann_type: 'classification', bbox_json: null, mask_json: null,
      is_draft: false },
    { id: 3, sample_id: 3, project_id: 1, created_at: 'x', created_by: 'a@b.c',
      label: 'defect', ann_type: 'mask', bbox_json: null, mask_json: goodMask,
      is_draft: false },
  ];
  const layers = [
    { id: 'existing-1', label: 'defect', mask_json: goodMask,
      source: stripServerFields(onServer[2]), dirty: false },
    { id: 'new-1', label: 'scratch', mask_json: goodMask, source: null, dirty: true },
  ];
  const payload = [
    ...preserveExcept(onServer, 'mask'),
    ...layers.map(maskLayerPayload).filter(Boolean),
  ];
  assert.deepEqual(payload.map((a) => a.ann_type),
                   ['bbox', 'classification', 'mask', 'mask']);
  assert.equal(payload[0].is_draft, true);          // draft bbox stays a draft
  assert.equal(payload[0].created_by, 'model:d');   // and stays clearable
  assert.equal(payload[2].mask_json, goodMask);     // untouched mask untouched
  assert.ok(payload.every((a) => !('id' in a)));
});

test('erasing every mask yields a payload of just the preserved annotations', () => {
  const onServer = [
    { id: 1, label: 'ok', ann_type: 'classification', is_draft: false },
    { id: 2, label: 'defect', ann_type: 'mask', mask_json: goodMask, is_draft: false },
  ];
  const layers = [{ id: 'existing-1', label: 'defect', mask_json: null,
                    source: stripServerFields(onServer[1]), dirty: true }];
  const payload = [
    ...preserveExcept(onServer, 'mask'),
    ...layers.map(maskLayerPayload).filter(Boolean),
  ];
  assert.deepEqual(payload.map((a) => a.ann_type), ['classification']);
});

test('erasing the only mask yields an empty payload, not a refusal', () => {
  const source = { label: 'defect', ann_type: 'mask', mask_json: goodMask, is_draft: false };
  const payload = [
    ...preserveExcept([{ id: 2, ...source }], 'mask'),
    ...[{ id: 'existing-1', label: 'defect', mask_json: null, source, dirty: true }]
      .map(maskLayerPayload).filter(Boolean),
  ];
  assert.deepEqual(payload, []);   // the caller sends this with allow_empty
});

// ---------------------------------------------------------------- status

test('statusFromSaved describes what was stored', () => {
  assert.equal(statusFromSaved([]), 'unlabeled');
  assert.equal(statusFromSaved(null), 'unlabeled');
  assert.equal(statusFromSaved([{ is_draft: false }]), 'labeled');
  assert.equal(statusFromSaved([{ is_draft: false }, { is_draft: true }]), 'pre_labeled');
});

console.log(`${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
