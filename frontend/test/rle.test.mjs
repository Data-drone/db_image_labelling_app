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
import { encodeMask, decodeMask, maskArea, maskIsEmpty, countsOf, sizeOf } from '../src/utils/rle.js';

const here = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(
  readFileSync(join(here, '../../backend/tests/fixtures/coco_rle_golden.json'), 'utf8'),
);
const cases = fixture.cases || fixture;

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

check('countsOf and sizeOf tolerate both wire shapes', () => {
  assert.deepEqual(countsOf({ size: [2, 2], counts: [1, 3] }), [1, 3]);
  assert.deepEqual(countsOf([1, 3]), [1, 3]);
  assert.equal(countsOf(null), null);
  assert.equal(countsOf({ size: [2, 2], counts: [] }), null);
  // A compressed string is refused rather than misread as counts.
  assert.equal(countsOf({ size: [2, 2], counts: 'PQR' }), null);
  assert.deepEqual(sizeOf({ size: [7, 9], counts: [1] }, 1, 1), [7, 9]);
  assert.deepEqual(sizeOf(null, 4, 5), [4, 5]);
});

console.log(`${passed} passed, ${failures.length} failed (${cases.length} golden vectors)`);
for (const f of failures) console.error(`FAIL ${f}`);
process.exit(failures.length === 0 ? 0 : 1);
