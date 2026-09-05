/**
 * Cross-language conformance test for the browser's RLE codec.
 *
 * Runs on plain `node` with no test framework, deliberately: the frontend has
 * no test runner and adding one to check ~90 lines of pure arithmetic is not a
 * trade worth making. `npm run test:rle`.
 *
 * The fixture is the same file backend/tests/test_masks.py checks against, and
 * it was generated from real pycocotools (see tools/gen_rle_fixtures.py). That
 * is the whole point: a JS encoder and decoder that only ever agree with each
 * other will happily round-trip garbage. Pinning both sides to output pycocotools
 * produced is what makes an exported dataset actually loadable.
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import assert from 'node:assert/strict';
import {
  encodeMask, decodeMask, maskArea, maskIsEmpty, countsOf, sizeOf, readMask, validateCounts,
} from '../src/utils/rle.js';

const here = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(
  readFileSync(join(here, '../../backend/tests/fixtures/coco_rle_golden.json'), 'utf8'),
);
const cases = fixture.cases || fixture;

/** The fixture's independently-generated row-major bitmap (MSB-first bits). */
function goldenBitmap(c) {
  const [h, w] = c.size;
  const bytes = Buffer.from(c.pixels_b64, 'base64');
  const buf = new Uint8Array(h * w);
  for (let i = 0; i < buf.length; i++) {
    buf[i] = (bytes[i >> 3] >> (7 - (i & 7))) & 1;
  }
  return buf;
}

let passed = 0;
const failures = [];

function check(name, fn) {
  try {
    fn();
    passed++;
  } catch (err) {
    failures.push(`${name}: ${err.message}`);
  }
}

for (const c of cases) {
  const [h, w] = c.size;

  check(`decode+encode round-trips ${c.name}`, () => {
    const buf = decodeMask(c.counts, w, h);
    assert.deepEqual(encodeMask(buf, w, h), c.counts);
  });

  check(`counts sum to h*w for ${c.name}`, () => {
    const buf = decodeMask(c.counts, w, h);
    const total = encodeMask(buf, w, h).reduce((a, b) => a + b, 0);
    assert.equal(total, h * w);
  });

  check(`area matches pycocotools for ${c.name}`, () => {
    assert.equal(maskArea(c.counts), c.area);
  });

  check(`decoded pixel count matches area for ${c.name}`, () => {
    const buf = decodeMask(c.counts, w, h);
    let n = 0;
    for (let i = 0; i < buf.length; i++) if (buf[i]) n++;
    assert.equal(n, c.area);
  });

  // The two assertions that actually pin the pixels. Round-tripping,
  // area and bbox are all satisfied by a consistently transposed or flipped
  // codec; comparing against pycocotools' own decode is not.
  check(`decode equals the pycocotools bitmap for ${c.name}`, () => {
    assert.deepEqual(decodeMask(c.counts, w, h), goldenBitmap(c));
  });

  check(`encoding the pycocotools bitmap gives the golden counts for ${c.name}`, () => {
    assert.deepEqual(encodeMask(goldenBitmap(c), w, h), c.counts);
  });

  check(`bbox matches pycocotools for ${c.name}`, () => {
    // Derived from the decoded bitmap rather than the runs, so this also
    // catches a decoder that transposes rows and columns on a square mask.
    const buf = decodeMask(c.counts, w, h);
    let minX = Infinity, minY = Infinity, maxX = -1, maxY = -1;
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        if (!buf[y * w + x]) continue;
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
    const bbox = maxX < 0 ? [0, 0, 0, 0] : [minX, minY, maxX - minX + 1, maxY - minY + 1];
    assert.deepEqual(bbox, c.bbox);
  });
}

// A mask the browser builds by stamping pixels must encode the way the backend
// expects, including the leading zero run when pixel (0,0) is foreground.
check('leading zero run when the first pixel is painted', () => {
  const buf = new Uint8Array(4);
  buf[0] = 1;
  const counts = encodeMask(buf, 2, 2);
  assert.equal(counts[0], 0);
  assert.equal(counts.reduce((a, b) => a + b, 0), 4);
});

check('empty mask encodes as a single background run', () => {
  const buf = new Uint8Array(12);
  assert.deepEqual(encodeMask(buf, 4, 3), [12]);
  assert.equal(maskIsEmpty(buf), true);
});

check('fully painted mask encodes as [0, n]', () => {
  const buf = new Uint8Array(12).fill(1);
  assert.deepEqual(encodeMask(buf, 4, 3), [0, 12]);
  assert.equal(maskIsEmpty(buf), false);
});

check('runs are column-major, not row-major', () => {
  // Left column painted on a 2-wide, 3-tall mask. Column-major that is one
  // run of 3 foreground pixels then 3 background; row-major it would alternate.
  const buf = new Uint8Array([1, 0, 1, 0, 1, 0]);
  assert.deepEqual(encodeMask(buf, 2, 3), [0, 3, 3]);
});

check('the fixture set could catch a transposed codec', () => {
  // Guard the guard: empty, full, centred-square and checkerboard fixtures are
  // symmetric and prove nothing about orientation on their own.
  const asymmetric = cases.filter((c) => {
    const [h, w] = c.size;
    if (h !== w) return true;
    const b = goldenBitmap(c);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) if (b[y * w + x] !== b[x * w + y]) return true;
    }
    return false;
  });
  assert.ok(asymmetric.length >= 12, `only ${asymmetric.length} asymmetric fixtures`);
});

check('countsOf and sizeOf tolerate both wire shapes', () => {
  assert.deepEqual(countsOf({ size: [2, 2], counts: [1, 3] }), [1, 3]);
  assert.deepEqual(countsOf([1, 3]), [1, 3]);
  assert.equal(countsOf(null), null);
  assert.equal(countsOf({ size: [2, 2], counts: [] }), null);
  // A compressed string is refused rather than misread as counts.
  assert.equal(countsOf({ size: [2, 2], counts: 'PQR' }), null);
  assert.deepEqual(sizeOf({ size: [7, 9], counts: [1] }), [7, 9]);
  assert.equal(sizeOf(null), null);
  assert.equal(sizeOf({ size: [0, 5], counts: [1] }), null);
  assert.equal(sizeOf({ size: [2.5, 5], counts: [1] }), null);
});

// Malformed masks must fail closed. A decoder that clamps or pads produces
// something renderable, which is then re-encoded on the first stroke and saved
// over the original -- silent corruption of the user's data.
check('validateCounts rejects malformed runs and sizes', () => {
  assert.throws(() => validateCounts([0, 100], 2, 2), /sum to 100/);
  assert.throws(() => validateCounts([1], 2, 2), /sum to 1/);
  assert.throws(() => validateCounts([2, -1, 3], 2, 2), /non-negative integer/);
  assert.throws(() => validateCounts([1.5, 2.5], 2, 2), /non-negative integer/);
  assert.throws(() => validateCounts(['2', 2], 2, 2), /non-negative integer/);
  assert.throws(() => validateCounts([Infinity], 2, 2), /non-negative integer/);
  assert.throws(() => validateCounts([], 2, 2), /non-empty array/);
  assert.throws(() => validateCounts([4], 0, 4), /positive integers/);
  validateCounts([4], 2, 2); // the valid case still passes
});

check('decodeMask refuses to repair a bad mask', () => {
  assert.throws(() => decodeMask([0, 100], 2, 2), /sum to 100/);
});

check('readMask separates unreadable from empty', () => {
  assert.deepEqual(readMask({ size: [2, 2], counts: [4] }, 2, 2), { size: [2, 2], counts: [4] });
  // Wrong dimensions for the image: reported, not silently started blank.
  assert.match(readMask({ size: [2, 2], counts: [4] }, 3, 3).error, /but the image is 3x3/);
  assert.match(readMask({ size: [2, 2], counts: [9] }, 2, 2).error, /sum to 9/);
  assert.match(readMask({ counts: [4] }, 2, 2).error, /missing a valid/);
  assert.match(readMask({ size: [2, 2], counts: 'PQR' }, 2, 2).error, /compressed string form/);
  assert.match(readMask(null, 2, 2).error, /no usable counts/);
});

console.log(`${passed} passed, ${failures.length} failed (${cases.length} golden vectors)`);
for (const f of failures) console.error(`FAIL ${f}`);
process.exit(failures.length === 0 ? 0 : 1);
