# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/mrc/segmentation.py

"""Numpy/PIL core of MRC segmentation: arrays and images in and out, no PDF.

Two constraints shape it:

1. Layers are averaged over paper-only or ink-only pixels (`masked_mean`);
   a plain blur smears ink into paper and leaves a text ghost.
2. The Sauvola window comes from the page's own measured glyph height, not a
   fixed size: a window sized for 10pt text erodes 24pt text.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image

#: Sauvola's normalizer R -- half of an 8-bit range, per the published form.
SAUVOLA_R = 128.0

#: Chroma variance (see `flat_ink_chroma_variance`) below which the
#: foreground collapses to a single pixel -- a page with one ink.
FLAT_INK_CHROMA_VARIANCE = 8.0

#: Default darkest-fraction cutoff for `core_ink_rgb`; 100 averages all of it.
DEFAULT_INK_PERCENTILE = 100.0

_EPS = 1e-4


# --------------------------------------------------------------------------
# Sauvola thresholding via integral images
# --------------------------------------------------------------------------
def _integral(a: np.ndarray) -> np.ndarray:
    """Summed-area table with a zero row/column, in float64.

    float64 matters: the squares table over a 300-dpi page reaches ~5e11,
    which float32 cannot hold to integer precision, and a variance computed
    from a lossy sum of squares drifts across the page.
    """
    out = np.zeros((a.shape[0] + 1, a.shape[1] + 1), dtype=np.float64)
    body = out[1:, 1:]
    np.cumsum(a, axis=0, dtype=np.float64, out=body)
    np.cumsum(body, axis=1, out=body)
    return out


def _gather(ii: np.ndarray, y0, y1, x0, x1) -> np.ndarray:
    """Rectangle sums from a summed-area table, for index vectors."""
    return ii[np.ix_(y1, x1)] - ii[np.ix_(y0, x1)] - ii[np.ix_(y1, x0)] + ii[np.ix_(y0, x0)]


def _clamped_bounds(n: int, radius: int) -> tuple[np.ndarray, np.ndarray]:
    at = np.arange(n)
    return np.clip(at - radius, 0, n), np.clip(at + radius + 1, 0, n)


def _window_counts(h: int, w: int, radius: int) -> np.ndarray:
    """Pixels in each clamped window of radius `radius`, in float64."""
    y0, y1 = _clamped_bounds(h, radius)
    x0, x1 = _clamped_bounds(w, radius)
    return ((y1 - y0)[:, None] * (x1 - x0)[None, :]).astype(np.float64)


def _box_sums(ii: np.ndarray, radius: int) -> np.ndarray:
    """Window sums of radius `radius`, edges clamped.

    The unclipped interior is computed with plain slices and only the border
    strips with gathered indices, which is several times faster on a page.
    """
    h, w = ii.shape[0] - 1, ii.shape[1] - 1
    y0, y1 = _clamped_bounds(h, radius)
    x0, x1 = _clamped_bounds(w, radius)

    r_lo, r_hi = radius, h - radius - 1  # inclusive interior row range
    c_lo, c_hi = radius, w - radius - 1  # inclusive interior col range
    if r_lo > r_hi or c_lo > c_hi:
        # The window spans the image on some axis: there is no interior.
        return _gather(ii, y0, y1, x0, x1)

    total = np.empty((h, w), dtype=np.float64)
    if r_lo > 0:
        total[:r_lo] = _gather(ii, y0[:r_lo], y1[:r_lo], x0, x1)
    if r_hi + 1 < h:
        total[r_hi + 1 :] = _gather(ii, y0[r_hi + 1 :], y1[r_hi + 1 :], x0, x1)

    mid = slice(r_lo, r_hi + 1)
    ry0, ry1 = y0[mid], y1[mid]
    if c_lo > 0:
        total[mid, :c_lo] = _gather(ii, ry0, ry1, x0[:c_lo], x1[:c_lo])
    if c_hi + 1 < w:
        total[mid, c_hi + 1 :] = _gather(ii, ry0, ry1, x0[c_hi + 1 :], x1[c_hi + 1 :])

    y1_start, y1_end = r_lo + radius + 1, r_hi + radius + 2
    y0_start, y0_end = r_lo - radius, r_hi - radius + 1
    x1_start, x1_end = c_lo + radius + 1, c_hi + radius + 2
    x0_start, x0_end = c_lo - radius, c_hi - radius + 1
    total[mid, c_lo : c_hi + 1] = (
        ii[y1_start:y1_end, x1_start:x1_end]
        - ii[y0_start:y0_end, x1_start:x1_end]
        - ii[y1_start:y1_end, x0_start:x0_end]
        + ii[y0_start:y0_end, x0_start:x0_end]
    )
    return total


def sauvola_ink(gray: np.ndarray, window: int, k: float, R: float = SAUVOLA_R) -> np.ndarray:
    """Exact (not block-approximated) Sauvola local thresholding.

    `thr = m*(1 + k*(sigma/R - 1))`, computed via integral images so the
    sliding window is exact -- an approximation shows up as a dashed edge
    along a long rule.
    """
    radius = max(int(window) // 2, 1)

    def strip(rows: np.ndarray) -> np.ndarray:
        # In place, as each temporary is a float64 copy of the strip.
        g = rows.astype(np.float64, copy=False)
        mean = _box_sums(_integral(g), radius)
        thr = _box_sums(_integral(g * g), radius)
        counts = _window_counts(g.shape[0], g.shape[1], radius)
        mean /= counts
        thr /= counts
        thr -= np.multiply(mean, mean, out=counts)
        del counts
        np.maximum(thr, 0.0, out=thr)
        np.sqrt(thr, out=thr)
        thr /= R
        thr -= 1.0
        thr *= k
        thr += 1.0
        thr *= mean
        return g < thr

    return _by_strips(gray, radius, strip)


#: Rows per strip in `_by_strips`: bounds window-sum memory by strip, not page.
STRIP_ROWS = 256


def _by_strips(arr: np.ndarray, radius: int, fn) -> np.ndarray:
    """Apply a windowed per-pixel `fn` in horizontal strips, with the same result.

    Each strip carries `radius` rows of context on both sides, so every kept
    row's window lies inside it (or is clamped at the true image edge).
    """
    h = arr.shape[0]
    out = np.empty(arr.shape, dtype=bool)
    for top in range(0, h, STRIP_ROWS):
        bottom = min(top + STRIP_ROWS, h)
        lo, hi = max(top - radius, 0), min(bottom + radius, h)
        out[top:bottom] = fn(arr[lo:hi])[top - lo : bottom - lo]
    return out


# --------------------------------------------------------------------------
# Connected components, via union-find over row-runs (not per-pixel)
# --------------------------------------------------------------------------
def _runs(ink: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Row-runs of ink: `(row, x0, x1)` with x1 exclusive, in raster order."""
    h, w = ink.shape
    pad = np.zeros((h, w + 2), dtype=np.int8)
    pad[:, 1:-1] = ink
    d = np.diff(pad, axis=1)
    starts = np.argwhere(d == 1)
    ends = np.argwhere(d == -1)
    return starts[:, 0], starts[:, 1], ends[:, 1]


_jit_mod = None


def _get_jit():
    """Import the numba kernel on first use, so importing this module does not."""
    global _jit_mod
    if _jit_mod is None:
        from pdftl.utils.mrc import _label_runs_jit

        _jit_mod = _label_runs_jit
    return _jit_mod


def _label_runs(rows: np.ndarray, x0: np.ndarray, x1: np.ndarray, height: int) -> np.ndarray:
    """8-connected component root per run (a union-find over runs, compiled)."""
    row_start = np.searchsorted(rows, np.arange(height + 1)).astype(np.int64)
    return _get_jit().label_runs(
        rows.astype(np.int64), x0.astype(np.int64), x1.astype(np.int64), row_start, height
    )


@dataclass(frozen=True)
class Components:
    labels: np.ndarray  # per-run root id, compacted to 0..n-1
    rows: np.ndarray
    x0: np.ndarray
    x1: np.ndarray
    area: np.ndarray
    top: np.ndarray
    bottom: np.ndarray
    left: np.ndarray
    right: np.ndarray

    @property
    def count(self) -> int:
        return int(self.area.shape[0])

    @property
    def bbox_h(self) -> np.ndarray:
        return self.bottom - self.top + 1

    @property
    def bbox_w(self) -> np.ndarray:
        return self.right - self.left + 1

    @property
    def fill(self) -> np.ndarray:
        return self.area / np.maximum(self.bbox_h * self.bbox_w, 1)


def components(ink: np.ndarray) -> Components:
    rows, x0, x1 = _runs(ink)
    if rows.size == 0:
        z = np.zeros(0, dtype=np.int64)
        return Components(z, z, z, z, z, z, z, z, z)
    roots = _label_runs(rows, x0, x1, ink.shape[0])
    labels, compact = np.unique(roots, return_inverse=True)
    n = labels.shape[0]
    widths = (x1 - x0).astype(np.int64)
    area = np.bincount(compact, weights=widths, minlength=n).astype(np.int64)
    top = np.full(n, ink.shape[0], dtype=np.int64)
    bottom = np.full(n, -1, dtype=np.int64)
    left = np.full(n, ink.shape[1], dtype=np.int64)
    right = np.full(n, -1, dtype=np.int64)
    np.minimum.at(top, compact, rows)
    np.maximum.at(bottom, compact, rows)
    np.minimum.at(left, compact, x0)
    np.maximum.at(right, compact, x1 - 1)
    return Components(compact, rows, x0, x1, area, top, bottom, left, right)


def _clear_runs(ink: np.ndarray, comp: Components, drop: np.ndarray) -> None:
    """Erase every run belonging to a dropped component, in place."""
    if not drop.any():
        return
    hit = np.flatnonzero(drop[comp.labels])
    rows = comp.rows[hit].tolist()
    lo = comp.x0[hit].tolist()
    hi = comp.x1[hit].tolist()
    for r, a, b in zip(rows, lo, hi):
        ink[r, a:b] = False


def _mark_runs(target: np.ndarray, comp: Components, keep: np.ndarray) -> None:
    """Set every run of a selected component -- the inverse of `_clear_runs`."""
    hit = np.flatnonzero(keep[comp.labels])
    rows = comp.rows[hit].tolist()
    lo = comp.x0[hit].tolist()
    hi = comp.x1[hit].tolist()
    for r, a, b in zip(rows, lo, hi):
        target[r, a:b] = True


def estimate_text_height(comp: Components, keep: np.ndarray, fallback: int) -> int:
    """The page's own median glyph height, weighted by ink area.

    `keep` must already exclude pictorial/speckle components -- measuring
    before excluding a halftone's tens of thousands of tiny dots returns the
    dot height instead of the glyph height.
    """
    if comp.count == 0 or not keep.any():
        return fallback
    h = comp.bbox_h[keep]
    w = comp.bbox_w[keep]
    a = comp.area[keep]
    plausible = (h >= 3) & (h <= fallback * 8) & (w <= fallback * 16) & (a >= 4)
    if not plausible.any():
        return fallback
    # Weighted by ink AREA, not one vote per component -- a page has far more
    # dots/commas/specks than letters, so a plain median returns the height
    # of the smallest thing on the page.
    order = np.argsort(h[plausible], kind="stable")
    heights = h[plausible][order]
    weights = a[plausible][order].astype(np.float64)
    cumulative = np.cumsum(weights)
    at = np.searchsorted(cumulative, cumulative[-1] / 2.0)
    return int(max(3, heights[min(at, heights.shape[0] - 1)]))


#: Taller than this many glyph heights: not a glyph.
_TEXT_LIKE_MAX_HEIGHT_FACTOR = 4
#: Wider than this many glyph heights: not a glyph, unless dense enough to be a rule.
_TEXT_LIKE_MAX_WIDTH_FACTOR = 10
#: Fill at which a wide component is a rule (a real rule ~0.98; photo edge traces ~0.67).
_TEXT_LIKE_MIN_RULE_FILL = 0.9


def text_like_mask(ink: np.ndarray, text_height: int) -> np.ndarray:
    """Ink pixels belonging to a component shaped like a glyph or a rule.

    Decides where the background may exclude a halo around ink. Photos the
    pictorial detector misses leak ink that is not text-shaped: long, sparse
    traces along a region's edge, sometimes fragmented to glyph height but
    still far wider than a glyph.
    """
    comp = components(ink)
    if comp.count == 0:
        return np.zeros_like(ink)
    max_h = max(text_height * _TEXT_LIKE_MAX_HEIGHT_FACTOR, 8)
    max_w = max(text_height * _TEXT_LIKE_MAX_WIDTH_FACTOR, 8)
    narrow = comp.bbox_w <= max_w
    like_text = (comp.bbox_h <= max_h) & (narrow | (comp.fill >= _TEXT_LIKE_MIN_RULE_FILL))
    out = np.zeros_like(ink)
    _mark_runs(out, comp, like_text)
    return out


def pictorial_block_size(dpi: int) -> int:
    """The block-grid cell size `_pictorial_blocks` groups coverage over."""
    return max(max(dpi, 1) // 4, 8)


def _pictorial_blocks(
    ink: np.ndarray, comp: Components, speckle: np.ndarray, *, dpi: int
) -> tuple[np.ndarray, dict]:
    """Block-grid mask of regions that are photos/halftones, not type.

    Measured in inches (never in glyph heights, which this pass exists to
    make measurable). Two signals: dot density (a halftone) and coverage
    (a continuous-tone shadow). Flagged blocks are filled to their bounding
    box so a photograph's light passages aren't left in the stencil.
    """
    inch = max(dpi, 1)
    block = pictorial_block_size(dpi)
    bh = int(math.ceil(ink.shape[0] / block))
    bw = int(math.ceil(ink.shape[1] / block))
    stats = {"halftone_blocks": 0, "picture_blocks": 0}
    if bh < 2 or bw < 2:
        return np.zeros((bh, bw), dtype=bool), stats

    dot_side = max(int(round(0.035 * inch)), 3)
    tiny = (~speckle) & (comp.bbox_h <= dot_side) & (comp.bbox_w <= dot_side)
    counts = np.zeros((bh, bw), dtype=np.int64)
    if tiny.any():
        cy = np.clip(((comp.top + comp.bottom) // 2)[tiny] // block, 0, bh - 1)
        cx = np.clip(((comp.left + comp.right) // 2)[tiny] // block, 0, bw - 1)
        counts = np.bincount(cy * bw + cx, minlength=bh * bw).reshape(bh, bw)
    halftone = counts >= 20
    stats["halftone_blocks"] = int(halftone.sum())

    pad_h, pad_w = bh * block - ink.shape[0], bw * block - ink.shape[1]
    padded = np.pad(ink, ((0, pad_h), (0, pad_w)), constant_values=False)
    coverage = padded.reshape(bh, block, bw, block).mean(axis=(1, 3))
    flagged = halftone | (coverage >= 0.55)
    if not flagged.any():
        return flagged, stats

    out = _group_and_fill_blocks(flagged, inch=inch, block=block)
    stats["picture_blocks"] = int(out.sum())
    return out, stats


def _group_and_fill_blocks(flagged: np.ndarray, *, inch: int, block: int) -> np.ndarray:
    """Connected groups of `flagged` blocks, bbox-filled; groups under 0.75in
    on either side are dropped, so a stray block never counts as a region."""
    if not flagged.any():
        return flagged
    group = components(flagged)
    min_side = max(int(math.ceil(0.75 * inch / block)), 2)
    out = np.zeros_like(flagged)
    for i in range(group.count):
        if int(group.bbox_h[i]) < min_side or int(group.bbox_w[i]) < min_side:
            continue
        out[
            int(group.top[i]) : int(group.bottom[i]) + 1,
            int(group.left[i]) : int(group.right[i]) + 1,
        ] = True
    return out


#: Block coverage at which a sustained region counts as busy. A blurred photo
#: can leak ink at ~0.4 coverage, below the pictorial bar of 0.55; single text
#: lines reach ~0.26 in a block but never across the 0.75in a region needs.
_BUSY_REGION_COVERAGE = 0.20


def busy_region_mask(ink: np.ndarray, *, dpi: int) -> np.ndarray:
    """Full-resolution mask of regions too busy for a halo around their ink.

    Catches photos the pictorial detector misses, whose glyph-sized leaked ink
    would otherwise get a halo that biases the background at the photo's edge.
    It never changes the stencil.
    """
    block = pictorial_block_size(dpi)
    bh = int(math.ceil(ink.shape[0] / block))
    bw = int(math.ceil(ink.shape[1] / block))
    if bh < 2 or bw < 2:
        return np.zeros_like(ink)
    pad_h, pad_w = bh * block - ink.shape[0], bw * block - ink.shape[1]
    padded = np.pad(ink, ((0, pad_h), (0, pad_w)), constant_values=False)
    coverage = padded.reshape(bh, block, bw, block).mean(axis=(1, 3))
    busy = coverage >= _BUSY_REGION_COVERAGE
    out = _group_and_fill_blocks(busy, inch=max(dpi, 1), block=block)
    return _expand_blocks(out, ink.shape, block)


def _expand_blocks(blocks: np.ndarray, shape: tuple[int, int], block: int) -> np.ndarray:
    grown = np.repeat(np.repeat(blocks, block, axis=0), block, axis=1)
    return grown[: shape[0], : shape[1]]


def segment(gray: np.ndarray, *, dpi: int, k: float) -> tuple[np.ndarray, np.ndarray, dict]:
    """`(ink, pictorial, stats)` -- the stencil, and what was kept out of it.

    `pictorial` marks ink-dark pixels that belong to a photo/halftone
    (excluded from the stencil; the background carries them instead). The
    order -- pictorial detection, then measure glyph height, then
    re-threshold -- is load-bearing: see `estimate_text_height`.
    """
    scale = max(dpi, 1) / 300.0
    noise_floor = max(3, int(round(3 * scale * scale)))
    default_window = max(int(round(25 * scale)) | 1, 15)
    block = max(max(dpi, 1) // 4, 8)

    # Pass 1: default window, good enough to find pictures and measure text.
    first = sauvola_ink(gray, default_window, k)
    comp = components(first)
    stats: dict = {
        "window": default_window,
        "text_height": 0,
        "components": comp.count,
        "speckle_dropped": 0,
        "picture_components": 0,
        "halftone_blocks": 0,
        "picture_blocks": 0,
        "marks": 0,
        "mark_area_mean": 0.0,
    }
    if comp.count == 0:
        stats["text_height"] = max(int(round(12 * scale)), 4)
        return first, np.zeros_like(first), stats

    speckle = comp.area < noise_floor
    blocks, block_stats = _pictorial_blocks(first, comp, speckle, dpi=dpi)
    stats.update(block_stats)
    picture_area = _expand_blocks(blocks, first.shape, block)

    centres_y = np.clip((comp.top + comp.bottom) // 2, 0, first.shape[0] - 1)
    centres_x = np.clip((comp.left + comp.right) // 2, 0, first.shape[1] - 1)
    in_picture = picture_area[centres_y, centres_x]
    del first, picture_area  # not held through pass 2, the peak
    inch = max(dpi, 1)
    solid = (comp.bbox_h >= inch // 2) & (comp.bbox_w >= inch // 2) & (comp.fill >= 0.5)
    pictorial_comp = (in_picture | solid) & ~speckle
    stats["picture_components"] = int(pictorial_comp.sum())

    text_h = estimate_text_height(
        comp, ~(speckle | pictorial_comp), fallback=max(int(round(12 * scale)), 4)
    )
    window = int(min(max(text_h * 2 + 1, max(int(round(15 * scale)) | 1, 15)), 151)) | 1
    stats["window"] = window
    stats["text_height"] = text_h

    # Pass 2: the window the page actually asked for.
    ink = sauvola_ink(gray, window, k)
    picture_area = _expand_blocks(blocks, ink.shape, block)
    comp2 = components(ink)
    stats["components"] = comp2.count
    pictorial = np.zeros_like(ink)
    if comp2.count == 0:
        return ink, pictorial, stats

    speckle2 = comp2.area < noise_floor
    stats["speckle_dropped"] = int(speckle2.sum())
    _clear_runs(ink, comp2, speckle2)

    cy2 = np.clip((comp2.top + comp2.bottom) // 2, 0, ink.shape[0] - 1)
    cx2 = np.clip((comp2.left + comp2.right) // 2, 0, ink.shape[1] - 1)
    solid2 = (comp2.bbox_h >= inch // 2) & (comp2.bbox_w >= inch // 2) & (comp2.fill >= 0.5)
    drop = ((picture_area[cy2, cx2]) | solid2) & ~speckle2
    if drop.any():
        _mark_runs(pictorial, comp2, drop)
        _clear_runs(ink, comp2, drop)
    pictorial |= picture_area & ink
    ink &= ~picture_area

    # Recounted rather than derived from comp2: clearing a picture block can
    # split/erase components, so the survivors are not a subset of comp2.
    final = components(ink)
    stats["marks"] = final.count
    stats["mark_area_mean"] = float(final.area.sum()) / final.count if final.count else 0.0
    return ink, pictorial, stats


# --------------------------------------------------------------------------
# Continuous-tone layers
# --------------------------------------------------------------------------
def _resize_f(arr: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    img = Image.fromarray(arr.astype(np.float32, copy=False), mode="F")
    return np.asarray(img.resize(size, Image.BOX), dtype=np.float32)


def _reduce(arr: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Area-average `arr` (H x W or H x W x C) down to (w, h), in float32."""
    if arr.ndim == 2:
        return _resize_f(arr, size)
    return np.stack([_resize_f(arr[..., c], size) for c in range(arr.shape[2])], axis=-1)


def _expand(arr: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    w, h = size
    out = np.empty((h, w, arr.shape[2]), dtype=np.float32)
    for c in range(arr.shape[2]):
        img = Image.fromarray(arr[..., c].astype(np.float32), mode="F")
        out[..., c] = np.asarray(img.resize((w, h), Image.BILINEAR), dtype=np.float32)
    return out


def _reduce_strips(rows, height: int, size: tuple[int, int]) -> np.ndarray:
    """`_reduce` of a 2-D image supplied as float32 row strips by `rows(top, bottom)`.

    Pillow's BOX resize is a per-row horizontal pass then a vertical pass, so
    narrowing each strip first gives the same values without a full-page
    float copy.
    """
    narrow = np.empty((height, size[0]), dtype=np.float32)
    for top in range(0, height, STRIP_ROWS):
        bottom = min(top + STRIP_ROWS, height)
        narrow[top:bottom] = _resize_f(rows(top, bottom), (size[0], bottom - top))
    return _resize_f(narrow, size)


def coverage(mask: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Fraction of `mask` pixels set in each cell of a BOX reduction to `size`."""
    return _reduce_strips(lambda t, b: mask[t:b].astype(np.float32), mask.shape[0], size)


def masked_mean(rgb: np.ndarray, keep: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """`blur(I*keep) / blur(keep)` at `size`, holes filled from coarser levels.

    A plain blur of the whole image smears ink back into the paper (or vice
    versa for the foreground); averaging over `keep` pixels only preserves
    contrast. A block with no keep pixels at any scale falls back to the
    source's global mean rather than dividing by zero.
    """
    height = rgb.shape[0]

    def channel(c: int) -> np.ndarray:
        return _reduce_strips(
            lambda t, b: np.multiply(rgb[t:b, :, c], keep[t:b], dtype=np.float32), height, size
        )

    num = np.empty((size[1], size[0], rgb.shape[2]), dtype=np.float32)
    for c in range(rgb.shape[2]):
        num[..., c] = channel(c)
    den = coverage(keep, size)

    levels: list[tuple[np.ndarray, np.ndarray, tuple[int, int]]] = [(num, den, size)]
    w, h = size
    while (den < _EPS).any() and (w > 1 or h > 1):
        w, h = max(w // 2, 1), max(h // 2, 1)
        num = _reduce(num, (w, h))
        den = _reduce(den, (w, h))
        levels.append((num, den, (w, h)))

    num, den, _ = levels[-1]
    fallback = rgb.reshape(-1, rgb.shape[-1]).mean(axis=0, dtype=np.float32)
    safe = np.maximum(den, _EPS)[..., None]
    result = np.where(den[..., None] > _EPS, num / safe, fallback)
    for num, den, lvl in reversed(levels[:-1]):
        # In place: at the finest level these are layer-sized.
        result = _expand(result, lvl)
        np.divide(num, np.maximum(den, _EPS)[..., None], out=num)
        np.copyto(result, num, where=den[..., None] > _EPS)
    return np.clip(result, 0.0, 255.0, out=result)


def to_image(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode="RGB")


def mask_image(ink: np.ndarray) -> Image.Image:
    """The stencil, Pillow mode "1": 0 = ink, 1 = paper."""
    return Image.fromarray(np.logical_not(ink))


def dilate_bool(mask: np.ndarray, radius: int) -> np.ndarray:
    """Grow `True` regions by `radius` pixels (square window).

    Uses windowed sums, so the cost does not grow with the radius.
    """
    if radius <= 0:
        return mask

    def strip(rows: np.ndarray) -> np.ndarray:
        total = _box_sums(_integral(rows.astype(np.float64)), radius)
        return total > 0.5

    return _by_strips(mask, radius, strip)


def core_ink_rgb(
    rgb: np.ndarray, ink: np.ndarray, gray: np.ndarray, percentile: float = DEFAULT_INK_PERCENTILE
) -> np.ndarray:
    """Average colour of the darkest `percentile`% of `ink` pixels.

    100 averages them all; lower values give a darker, more saturated ink.
    """
    if not ink.any():
        return np.zeros(3, dtype=np.float64)
    values = gray[ink].astype(np.float64)
    cutoff = np.percentile(values, percentile)
    core = ink & (gray <= cutoff)  # never empty: the cutoff is at least the darkest value
    return rgb[core].astype(np.float64).mean(axis=0)


def flat_ink_chroma_variance(fg_arr: np.ndarray) -> float:
    """Chroma (not luminance) variance of a masked-mean foreground layer.

    Below `FLAT_INK_CHROMA_VARIANCE`, the page uses a single ink and the
    foreground can collapse to a 1x1 swatch. Chroma, not luminance: a single
    black ink still has luminance variance across stroke weights, but its
    chroma is flat.
    """
    chroma = fg_arr - fg_arr.mean(axis=-1, keepdims=True)
    return float(chroma.reshape(-1, 3).var(axis=0).mean())
