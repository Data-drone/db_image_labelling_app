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

/** Uncompressed RLE counts -> row-major Uint8Array. */
export function decodeMask(counts, width, height) {
  const buf = new Uint8Array(width * height);
  let pos = 0;
  let val = 0;
  for (let i = 0; i < counts.length; i++) {
    const c = counts[i];
    if (val) {
      const end = Math.min(pos + c, width * height);
      for (let p = pos; p < end; p++) {
        // p counts down columns: column = p / height, row = p % height
        buf[(p % height) * width + ((p / height) | 0)] = 1;
      }
    }
    pos += c;
    val ^= 1;
  }
  return buf;
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
 * Tolerates the compressed string form by refusing it rather than guessing —
 * the API is not supposed to hand us one.
 */
export function countsOf(mask) {
  if (!mask) return null;
  const counts = Array.isArray(mask) ? mask : mask.counts;
  if (!Array.isArray(counts) || counts.length === 0) return null;
  return counts;
}

/** [height, width] from a wire mask, falling back to the given dimensions. */
export function sizeOf(mask, fallbackHeight, fallbackWidth) {
  const size = mask && !Array.isArray(mask) ? mask.size : null;
  if (Array.isArray(size) && size.length === 2) return [size[0], size[1]];
  return [fallbackHeight, fallbackWidth];
}
