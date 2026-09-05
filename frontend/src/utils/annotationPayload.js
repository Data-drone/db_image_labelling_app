/**
 * Payload builders for the annotate-batch endpoint.
 *
 * That endpoint *replaces* every annotation on a sample. A page that edits one
 * annotation type therefore has to resubmit the rest, and every one of the
 * data-loss bugs this module exists to prevent came from getting that wrong:
 * projecting preserved annotations down to the fields the editor happens to
 * read, dropping records the editor cannot represent, refusing to save an empty
 * set so deletions never persisted, and re-encoding a mask this build cannot
 * decode over the original.
 *
 * These live outside the component so they can be tested as plain functions --
 * see frontend/test/annotationPayload.test.mjs.
 */

// Extension included so this module also loads under plain node for the tests.
import { readMask } from './rle.js';

/**
 * Fields the server assigns and will not take back. Everything else round-trips,
 * `created_by` included: the server keeps a `model:` provenance marker on a
 * preserved draft and ignores any other claim, and without it the draft-clearing
 * endpoints stop recognising the drafts the client handed back.
 */
export const SERVER_ASSIGNED = ['id', 'sample_id', 'project_id', 'created_at'];

/** A server annotation record reduced to what annotate-batch accepts. */
export function stripServerFields(a) {
  const out = {};
  for (const [k, v] of Object.entries(a)) {
    if (!SERVER_ASSIGNED.includes(k)) out[k] = v;
  }
  return out;
}

/** The payload field the editor for each annotation type is able to represent. */
const OWNED_PAYLOAD = { bbox: 'bbox_json', mask: 'mask_json' };

/**
 * Every annotation a save path must hand back unchanged.
 *
 * Two rules, both learned the hard way:
 *
 *   - Verbatim means the server's record minus the fields the server assigns.
 *     Projecting it down to the fields one page reads is how `is_draft` got
 *     dropped and every preserved draft came back accepted.
 *   - A record of the owned type that the editor cannot represent (a bbox with
 *     no bbox_json, a mask with no mask_json) is preserved too. Those fell
 *     between the editor's filter and the preservation filter, and the next
 *     save deleted them.
 */
export function preserveExcept(annotations, ownedType) {
  return (annotations || [])
    .filter((a) => {
      if (a.ann_type !== ownedType) return true;
      const payload = OWNED_PAYLOAD[ownedType];
      return Boolean(payload) && !a[payload];
    })
    .map(stripServerFields);
}

/** True when a wire mask is well-formed on its own terms (size vs counts). */
export function maskIsUsable(mask) {
  return Boolean(mask) && !readMask(mask).error;
}

/**
 * One mask layer as annotate-batch should store it, or null to drop it.
 *
 * A layer carries `source` (the server record it was loaded from, or null when
 * the user or the model created it) and `dirty` (whether it has been touched
 * since). Those two are what make the difference between preserving a mask and
 * rewriting it.
 */
export function maskLayerPayload(l) {
  // Never edited: the server's own record goes back untouched. This is what
  // keeps a draft a draft, and what keeps a mask this build cannot decode
  // byte-identical instead of overwriting it with a re-encoded guess.
  if (l.source && !l.dirty) return l.source;
  // Erased empty. Dropping it out of the replacement is how a deletion actually
  // persists -- the old code refused to save at all, so the server copy lived
  // forever and reloading resurrected it.
  if (!l.mask_json) return null;
  if (!maskIsUsable(l.mask_json)) {
    // MaskCanvas refuses to paint an unreadable layer, so the only edit that
    // can land here is a relabel. Carry the original payload across.
    return l.source ? { ...l.source, label: l.label } : null;
  }
  return {
    ...(l.source || {}),
    label: l.label,
    ann_type: 'mask',
    mask_json: l.mask_json,
    // Save is the accept action for a model draft -- Reject is a separate
    // button -- so an edited or saved draft is stored accepted.
    is_draft: false,
  };
}

/** The sample status implied by what annotate-batch actually stored. */
export function statusFromSaved(saved) {
  if (!saved || saved.length === 0) return 'unlabeled';
  return saved.some((a) => a.is_draft) ? 'pre_labeled' : 'labeled';
}
