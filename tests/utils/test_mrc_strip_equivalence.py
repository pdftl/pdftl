# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/utils/test_mrc_strip_equivalence.py

"""The strip-wise MRC functions give bit-identical results to whole-page ones.

Each reference below is the direct whole-page computation: one float copy of
the page, one Pillow resize, no strips, no in-place tricks.
"""

import numpy as np
import pytest
from PIL import Image

from pdftl.operations import mrc_compress
from pdftl.utils.mrc import segmentation

EPS = 1e-4


def _resize(arr, size, resample):
    img = Image.fromarray(np.ascontiguousarray(arr, dtype=np.float32), mode="F")
    return np.asarray(img.resize(size, resample), dtype=np.float32)


def _ref_reduce(arr, size):
    if arr.ndim == 2:
        return _resize(arr, size, Image.BOX)
    return np.stack([_resize(arr[..., c], size, Image.BOX) for c in range(arr.shape[2])], -1)


def _ref_expand(arr, size):
    return np.stack([_resize(arr[..., c], size, Image.BILINEAR) for c in range(arr.shape[2])], -1)


def _ref_masked_mean(rgb, keep, size):
    keep_f = keep.astype(np.float32)
    num = np.stack([_ref_reduce(rgb[..., c] * keep_f, size) for c in range(rgb.shape[2])], -1)
    den = _ref_reduce(keep_f, size)
    levels = [(num, den, size)]
    w, h = size
    while (den < EPS).any() and (w > 1 or h > 1):
        w, h = max(w // 2, 1), max(h // 2, 1)
        num, den = _ref_reduce(num, (w, h)), _ref_reduce(den, (w, h))
        levels.append((num, den, (w, h)))
    num, den, _ = levels[-1]
    fallback = rgb.reshape(-1, rgb.shape[-1]).mean(axis=0, dtype=np.float32)
    result = np.where(den[..., None] > EPS, num / np.maximum(den, EPS)[..., None], fallback)
    for num, den, lvl in reversed(levels[:-1]):
        up = _ref_expand(result, lvl)
        result = np.where(den[..., None] > EPS, num / np.maximum(den, EPS)[..., None], up)
    return np.clip(result, 0.0, 255.0)


def _ref_window_sums(a, radius):
    """Clamped window sums via a whole-page summed-area table."""
    h, w = a.shape
    ii = np.zeros((h + 1, w + 1))
    ii[1:, 1:] = a.astype(np.float64).cumsum(0).cumsum(1)
    y0, y1 = np.clip(np.arange(h) - radius, 0, h), np.clip(np.arange(h) + radius + 1, 0, h)
    x0, x1 = np.clip(np.arange(w) - radius, 0, w), np.clip(np.arange(w) + radius + 1, 0, w)
    total = ii[np.ix_(y1, x1)] - ii[np.ix_(y0, x1)] - ii[np.ix_(y1, x0)] + ii[np.ix_(y0, x0)]
    counts = ((y1 - y0)[:, None] * (x1 - x0)[None, :]).astype(np.float64)
    return total, counts


def _ref_sauvola(gray, window, k, R=128.0):
    radius = max(int(window) // 2, 1)
    g = gray.astype(np.float64)
    s1, counts = _ref_window_sums(g, radius)
    s2, _ = _ref_window_sums(g * g, radius)
    mean = s1 / counts
    var = np.maximum(s2 / counts - mean * mean, 0.0)
    return g < mean * (1.0 + k * (np.sqrt(var) / R - 1.0))


def _ref_dilate(mask, radius):
    if radius <= 0:
        return mask
    return _ref_window_sums(mask, radius)[0] > 0.5


def _ref_background(rgb, ink, stats, pictorial, size, bg_div, dpi, supersample):
    text_ink = segmentation.text_like_mask(ink, stats["text_height"])
    text_ink &= ~segmentation.busy_region_mask(ink, dpi=dpi)
    non_text_ink = ink & ~text_ink
    halo = _ref_dilate(text_ink, mrc_compress.HALO_RADIUS * supersample)
    bg_keep = ~(non_text_ink | halo)
    div = min(bg_div, 2) if pictorial.any() else bg_div
    bg_size = (max(size[0] // div, 1), max(size[1] // div, 1))
    return segmentation.to_image(_ref_masked_mean(rgb, bg_keep, bg_size))


def _ref_foreground(rgb, ink, gray, size, fg_div, ink_percentile, supersample):
    if not ink.any():
        return Image.new("RGB", (1, 1), (0, 0, 0))
    fg_size = (max(size[0] // fg_div, 1), max(size[1] // fg_div, 1))
    core = ~_ref_dilate(~ink, supersample)
    colour_ink = core if core.any() else ink
    fg_arr = _ref_masked_mean(rgb, colour_ink, fg_size)
    fg_rim = _ref_masked_mean(rgb, ink, fg_size)
    dilated = _ref_dilate(ink, mrc_compress.HALO_RADIUS * supersample)
    fg_dilated = _ref_masked_mean(rgb, dilated, fg_size)

    def covers(mask):
        return _ref_reduce(mask.astype(np.float32), fg_size) > EPS

    fg_arr = np.where(
        covers(colour_ink)[..., None],
        fg_arr,
        np.where(covers(ink)[..., None], fg_rim, fg_dilated),
    )
    if segmentation.flat_ink_chroma_variance(fg_arr) < segmentation.FLAT_INK_CHROMA_VARIANCE:
        fg_arr = segmentation.core_ink_rgb(rgb, colour_ink, gray, percentile=ink_percentile)
        fg_arr = fg_arr.reshape(1, 1, 3)
    return segmentation.to_image(fg_arr)


def _ref_looks_gray(rgb):
    spread = rgb.max(axis=2).astype(np.int16) - rgb.min(axis=2)
    return float(np.percentile(spread, 99.9)) <= mrc_compress.GRAY_SPREAD


def _page(h, w, seed, ink_rate=0.08):
    """A noisy page: paper, dark glyph-ish blocks, and colour noise."""
    rng = np.random.default_rng(seed)
    rgb = (200 + rng.integers(0, 56, (h, w, 3))).astype(np.uint8)
    ink = rng.random((h, w)) < ink_rate
    for _ in range(max(h * w // 400, 1)):
        y, x = rng.integers(0, h), rng.integers(0, w)
        ink[y : y + rng.integers(1, 9), x : x + rng.integers(1, 6)] = True
    rgb[ink] = rng.integers(0, 90, (int(ink.sum()), 3))
    gray = np.asarray(Image.fromarray(rgb).convert("L"))
    return rgb, ink, gray


# Heights straddle the patched strip height (7): below, equal, multiples, and ragged.
SHAPES = [(1, 1), (1, 9), (9, 1), (3, 5), (7, 12), (14, 10), (15, 23), (50, 37), (61, 8)]


@pytest.fixture
def small_strips(monkeypatch):
    monkeypatch.setattr(segmentation, "STRIP_ROWS", 7)


@pytest.mark.parametrize("h,w", SHAPES)
@pytest.mark.parametrize("div", [1, 2, 3, 5])
def test_coverage_matches_whole_page_resize(small_strips, h, w, div):
    mask = np.random.default_rng(h * 31 + w + div).random((h, w)) < 0.3
    size = (max(w // div, 1), max(h // div, 1))
    got = segmentation.coverage(mask, size)
    assert got.dtype == np.float32
    assert np.array_equal(got, _ref_reduce(mask.astype(np.float32), size))


@pytest.mark.parametrize("h,w", SHAPES)
@pytest.mark.parametrize("div", [1, 3, 4])
@pytest.mark.parametrize("keep_rate", [0.0, 0.002, 0.5, 1.0])
def test_masked_mean_matches_whole_page_reference(small_strips, h, w, div, keep_rate):
    rng = np.random.default_rng(h * 1000 + w * 10 + div)
    rgb = rng.integers(0, 256, (h, w, 3)).astype(np.uint8)
    keep = rng.random((h, w)) < keep_rate
    size = (max(w // div, 1), max(h // div, 1))
    got = segmentation.masked_mean(rgb, keep, size)
    assert np.array_equal(got, _ref_masked_mean(rgb, keep, size))


def test_masked_mean_hole_filling_goes_through_several_levels(small_strips):
    """A single kept pixel leaves most blocks empty down to the coarsest level."""
    rgb = np.random.default_rng(5).integers(0, 256, (64, 48, 3)).astype(np.uint8)
    keep = np.zeros((64, 48), dtype=bool)
    keep[40, 3] = True
    got = segmentation.masked_mean(rgb, keep, (24, 32))
    assert np.array_equal(got, _ref_masked_mean(rgb, keep, (24, 32)))


@pytest.mark.parametrize("h,w", SHAPES)
@pytest.mark.parametrize("window", [3, 5, 15, 31])
def test_sauvola_matches_whole_page_reference(small_strips, h, w, window):
    rng = np.random.default_rng(h * 7 + w + window)
    gray = rng.integers(0, 256, (h, w)).astype(np.uint8)
    gray[: h // 2] //= 3  # a dark band, so the threshold varies across strips
    got = segmentation.sauvola_ink(gray, window, 0.2)
    assert np.array_equal(got, _ref_sauvola(gray, window, 0.2))


def test_sauvola_on_a_page_sized_strip_set_matches_reference():
    """Default strip height, a page tall enough for several strips and a wide window."""
    gray = _page(700, 90, seed=3)[2]
    for window in (25, 151):
        assert np.array_equal(
            segmentation.sauvola_ink(gray, window, 0.2), _ref_sauvola(gray, window, 0.2)
        )


@pytest.mark.parametrize("h,w", [(1, 1), (9, 1), (15, 23), (61, 40)])
@pytest.mark.parametrize("radius", [1, 3, 9])
def test_dilate_bool_matches_whole_page_reference(small_strips, h, w, radius):
    mask = np.random.default_rng(h + w * radius).random((h, w)) < 0.05
    assert np.array_equal(segmentation.dilate_bool(mask, radius), _ref_dilate(mask, radius))


@pytest.mark.parametrize("h,w,seed", [(1, 1, 0), (5, 3, 1), (40, 29, 2), (90, 130, 3)])
@pytest.mark.parametrize("supersample", [1, 2])
@pytest.mark.parametrize("pictorial_any", [False, True])
def test_build_background_matches_whole_page_reference(
    small_strips, h, w, seed, supersample, pictorial_any
):
    rgb, ink, _ = _page(h, w, seed)
    stats = {"text_height": 6}
    pictorial = np.zeros((h, w), dtype=bool)
    pictorial[0, 0] = pictorial_any
    args = (rgb, ink, stats)
    got = mrc_compress._build_background(*args, pictorial_any, (w, h), 3, 32, supersample)
    want = _ref_background(*args, pictorial, (w, h), 3, 32, supersample)
    assert np.array_equal(np.asarray(got), np.asarray(want))


def test_build_background_on_a_busy_region_matches_reference(small_strips):
    """Dense ink over several blocks is busy: its halo is dropped."""
    rgb, ink, _ = _page(96, 96, seed=9, ink_rate=0.02)
    ink[16:64, 16:64] = np.random.default_rng(1).random((48, 48)) < 0.3
    stats = {"text_height": 5}
    got = mrc_compress._build_background(rgb, ink, stats, False, (96, 96), 2, 32, 1)
    want = _ref_background(rgb, ink, stats, np.zeros_like(ink), (96, 96), 2, 32, 1)
    assert segmentation.busy_region_mask(ink, dpi=32).any()
    assert np.array_equal(np.asarray(got), np.asarray(want))


@pytest.mark.parametrize("h,w,seed", [(1, 1, 0), (6, 4, 1), (40, 29, 2), (90, 130, 3)])
@pytest.mark.parametrize("supersample", [1, 2])
@pytest.mark.parametrize("ink_rate", [0.0, 0.08])
def test_build_foreground_matches_whole_page_reference(
    small_strips, h, w, seed, supersample, ink_rate
):
    rgb, ink, gray = _page(h, w, seed, ink_rate)
    if ink_rate == 0.0:
        ink[:] = False
    got = mrc_compress._build_foreground(rgb, ink, gray, (w, h), 4, 100, supersample=supersample)
    want = _ref_foreground(rgb, ink, gray, (w, h), 4, 100, supersample)
    assert np.array_equal(np.asarray(got), np.asarray(want))


def test_build_foreground_with_many_ink_colours_matches_reference(small_strips):
    """Hue varying across the page keeps the full foreground (no single swatch)."""
    h, w = 48, 80
    rgb = np.full((h, w, 3), 250, dtype=np.uint8)
    ink = np.zeros((h, w), dtype=bool)
    ink[4:44, 2:78:3] = True
    ramp = np.linspace(0, 200, w).astype(np.uint8)
    rgb[..., 0] = np.where(ink, ramp[None, :], rgb[..., 0])
    rgb[..., 2] = np.where(ink, 200 - ramp[None, :], rgb[..., 2])
    rgb[..., 1] = np.where(ink, 40, rgb[..., 1])
    gray = np.asarray(Image.fromarray(rgb).convert("L"))
    got = mrc_compress._build_foreground(rgb, ink, gray, (w, h), 2, 100)
    want = _ref_foreground(rgb, ink, gray, (w, h), 2, 100, 1)
    assert got.size != (1, 1)
    assert np.array_equal(np.asarray(got), np.asarray(want))


@pytest.mark.parametrize("h,w", SHAPES)
@pytest.mark.parametrize("max_spread", [0, 5, 6, 7, 40])
def test_looks_gray_matches_whole_page_reference(small_strips, h, w, max_spread):
    rng = np.random.default_rng(h * w + max_spread)
    base = rng.integers(0, 256 - max_spread, (h, w, 1))
    rgb = (base + rng.integers(0, max_spread + 1, (h, w, 3))).astype(np.uint8)
    assert mrc_compress._looks_gray(rgb) == _ref_looks_gray(rgb)


def test_looks_gray_decides_at_the_exact_spread_limit(small_strips):
    """Spread GRAY_SPREAD is gray, one more is colour: checked against the reference."""
    limit = mrc_compress.GRAY_SPREAD
    for spread, want in ((limit, True), (limit + 1, False)):
        rgb = np.zeros((30, 20, 3), dtype=np.uint8)
        rgb[..., 0] = spread
        assert _ref_looks_gray(rgb) is want
        assert mrc_compress._looks_gray(rgb) is want
