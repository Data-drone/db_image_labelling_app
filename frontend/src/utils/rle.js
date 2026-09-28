/**
 * Uncompressed COCO RLE helpers.
 *
 * The wire format for masks is COCO's *uncompressed* run-length encoding: a
 * flat array of run lengths over the mask read in **column-major** (Fortran)
 * order, where the first run is always background. `sum(counts)` therefore
 * always equals width * height.
 *
 * The browser deliberately never touches COCO's *compressed* RLE string form.
 * That codec has non-obvious sign-extension and delta rules and a hand-rolled
 * JS version that round-trips against itself can still emit files pycocotools
 * misreads. `backend/masks.py` owns the compression (validated against golden
 * vectors generated from real pycocotools); the API compresses on write and
 * decompresses on read so this side only ever sees plain integers.
 *
 * Masks are held in memory as a row-major `Uint8Array(width * height)` because
 * that matches canvas pixel order; the column-major walk happens only here, at
 * encode time.
 */

/** Row-major Uint8Array -> uncompressed RLE counts (column-major runs). */
export function encodeMask(buf, width, height) {
  const counts = [];
  let cur = 0; // value of the run being accumulated; masks start on background
  let run = 0;
  for (let x = 0; x < width; x++) {
    for (let y = 0; y < height; y++) {
      const v = buf[y * width + x] ? 1 : 0;
      if (v === cur) {
        run++;
      } else {
        counts.push(run);
        cur = v;
        run = 1;
      }
    }
  }
  counts.push(run);
  return counts;
}

/**
 * Uncompressed RLE counts -> row-major Uint8Array.
 *
 * Throws on anything `validateCounts` rejects. Clamping a bad mask into
 * something renderable would be worse than failing: the repaired version gets
 * re-encoded on the first stroke and saved over the original.
 */
export function decodeMask(counts, width, height) {
  validateCounts(counts, height, width);
  const buf = new Uint8Array(width * height);
  // Walk columns with running row/col counters rather than `%` and `/` per
  // pixel; same order, no division in the inner loop.
  let row = 0;
  let col = 0;
  let val = 0;
  for (let i = 0; i < counts.length; i++) {
    let c = counts[i];
    if (val) {
      while (c-- > 0) {
        buf[row * width + col] = 1;
        if (++row === height) { row = 0; col++; }
      }
    } else {
      // Skipping background: advance the counters without touching the buffer.
      const total = row + c;
      col += (total / height) | 0;
      row = total % height;
    }
    val ^= 1;
  }
  return buf;
}

/**
 * Throw unless `counts` is a well-formed uncompressed RLE for `height`x`width`.
 *
 * Fails closed on the cases a tolerant decoder quietly absorbs: fractional or
 * negative runs, a total that is not exactly h*w (which clips or drops
 * foreground), and dimensions that are not positive integers.
 */
export function validateCounts(counts, height, width) {
  if (!Number.isSafeInteger(height) || !Number.isSafeInteger(width) || height <= 0 || width <= 0) {
    throw new Error(`mask size must be positive integers, got ${height}x${width}`);
  }
  if (!Array.isArray(counts) || counts.length === 0) {
    throw new Error('mask counts must be a non-empty array');
  }
  let total = 0;
  for (let i = 0; i < counts.length; i++) {
    const c = counts[i];
    if (!Number.isSafeInteger(c) || c < 0) {
      throw new Error(`mask counts[${i}] must be a non-negative integer, got ${c}`);
    }
    total += c;
  }
  if (total !== height * width) {
    throw new Error(`mask counts sum to ${total}, expected ${height * width} for ${height}x${width}`);
  }
}

/** Foreground pixel count. Odd-indexed runs are the foreground ones. */
export function maskArea(counts) {
  let area = 0;
  for (let i = 1; i < counts.length; i += 2) area += counts[i];
  return area;
}

/** True when nothing is painted. Cheaper than encoding to find out. */
export function maskIsEmpty(buf) {
  for (let i = 0; i < buf.length; i++) if (buf[i]) return false;
  return true;
}

/**
 * Accepts either wire form and returns counts, or null if unusable.
 *
 * A compressed string is refused rather than guessed at — the API is not
 * supposed to hand us one. Shape only: whether the counts are *valid* depends
 * on the size they are paired with, which is `readMask`'s job.
 */
export function countsOf(mask) {
  if (!mask) return null;
  const counts = Array.isArray(mask) ? mask : mask.counts;
  if (!Array.isArray(counts) || counts.length === 0) return null;
  return counts;
}

/** [height, width] from a wire mask, or null when it carries no usable size. */
export function sizeOf(mask) {
  const size = mask && !Array.isArray(mask) ? mask.size : null;
  if (!Array.isArray(size) || size.length !== 2) return null;
  const [h, w] = size;
  if (!Number.isSafeInteger(h) || !Number.isSafeInteger(w) || h <= 0 || w <= 0) return null;
  return [h, w];
}

/**
 * Validate a wire mask without decoding it.
 *
 * Returns `{size: [h, w], counts}` when the mask is well-formed and matches
 * `expected` dimensions (when given), otherwise `{error}` naming the problem.
 * Callers use this to tell three states apart that all look alike otherwise:
 * a new empty layer, a layer the user erased on purpose, and an existing
 * annotation this build cannot read. The third must be preserved untouched,
 * never silently replaced by whatever a lenient decode produced.
 */
export function readMask(mask, expectedHeight, expectedWidth) {
  const counts = countsOf(mask);
  if (!counts) {
    return { error: typeof mask?.counts === 'string' || typeof mask === 'string'
      ? 'mask is in the compressed string form; the API should have decompressed it'
      : 'mask has no usable counts array' };
  }
  const size = sizeOf(mask);
  if (!size) return { error: 'mask is missing a valid [height, width] size' };
  const [h, w] = size;
  if (expectedHeight && expectedWidth && (h !== expectedHeight || w !== expectedWidth)) {
    return { error: `mask is ${h}x${w} but the image is ${expectedHeight}x${expectedWidth}` };
  }
  try {
    validateCounts(counts, h, w);
  } catch (e) {
    return { error: e.message };
  }
  return { size, counts };
}
