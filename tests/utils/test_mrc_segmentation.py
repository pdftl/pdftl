# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/utils/test_mrc_segmentation.py

import numpy as np
import pytest

from pdftl.utils.mrc import segmentation


def _brute_force_box_sums(raw: np.ndarray, radius: int) -> tuple[np.ndarray, np.ndarray]:
    """Independent, obviously-correct (if slow) reference for `_box_sums`:
    a direct clamped-window sum over the raw array, no integral image."""
    h, w = raw.shape
    total = np.zeros((h, w), dtype=np.float64)
    counts = np.zeros((h, w), dtype=np.float64)
    for r in range(h):
        r0, r1 = max(r - radius, 0), min(r + radius + 1, h)
        for c in range(w):
            c0, c1 = max(c - radius, 0), min(c + radius + 1, w)
            total[r, c] = raw[r0:r1, c0:c1].sum()
            counts[r, c] = (r1 - r0) * (c1 - c0)
    return total, counts


@pytest.mark.parametrize("shape", [(1, 1), (5, 5), (9, 13), (13, 9)])
@pytest.mark.parametrize("radius", [0, 1, 2, 5, 20])
def test_box_sums_matches_brute_force_reference(shape, radius):
    rng = np.random.default_rng(shape[0] * 100 + radius)
    raw = rng.random(shape) * 1000
    ii = segmentation._integral(raw)
    total = segmentation._box_sums(ii, radius)
    counts = segmentation._window_counts(*shape, radius)
    ref_total, ref_counts = _brute_force_box_sums(raw, radius)
    assert np.allclose(total, ref_total)
    assert np.array_equal(counts, ref_counts)


def test_sauvola_ink_flags_dark_region_near_light_background():
    gray = np.full((60, 60), 250, dtype=np.uint8)
    gray[25:35, 25:35] = 10  # small dark patch, window sees the light surround
    ink = segmentation.sauvola_ink(gray, window=25, k=0.2)
    assert ink[30, 30]
    assert not ink[5, 5]


def test_components_counts_and_areas():
    ink = np.zeros((20, 20), dtype=bool)
    ink[2:5, 2:5] = True  # 3x3 = 9
    ink[10:13, 10:14] = True  # 3x4 = 12
    comp = segmentation.components(ink)
    assert comp.count == 2
    assert sorted(comp.area.tolist()) == [9, 12]


def test_components_resolves_a_long_diagonal_chain_to_one_component():
    """Regression test for the pointer-doubling root resolution in
    _label_runs: a long staircase of diagonally-touching 1px runs builds a
    deep union-find chain before it's resolved -- this must still collapse
    every run to the same root, not just short chains."""
    n = 200
    ink = np.zeros((n, n), dtype=bool)
    for i in range(n):
        ink[i, i] = True  # single diagonal chain, 8-connected corner-to-corner
    comp = segmentation.components(ink)
    assert comp.count == 1
    assert comp.area[0] == n


def test_components_empty_input():
    comp = segmentation.components(np.zeros((10, 10), dtype=bool))
    assert comp.count == 0


def test_masked_mean_excludes_masked_pixels_from_average():
    rgb = np.full((10, 10, 3), 200, dtype=np.uint8)  # paper
    rgb[3:7, 3:7] = 0  # an "ink" patch that must not pollute the average
    keep = np.ones((10, 10), dtype=bool)
    keep[3:7, 3:7] = False
    result = segmentation.masked_mean(rgb, keep, (1, 1))
    assert np.allclose(result, 200.0, atol=1e-2)


def test_masked_mean_falls_back_to_global_mean_when_nothing_kept():
    rgb = np.full((10, 10, 3), 100, dtype=np.uint8)
    keep = np.zeros((10, 10), dtype=bool)
    result = segmentation.masked_mean(rgb, keep, (2, 2))
    assert np.allclose(result, 100.0, atol=1e-2)


def test_segment_detects_text_scale_mark_and_ignores_background():
    gray = np.full((120, 120), 250, dtype=np.uint8)
    gray[50:62, 40:80] = 10  # a text-line-scale mark
    ink, pictorial, stats = segmentation.segment(gray, dpi=100, k=0.2)
    assert ink[56, 60]
    assert not ink[5, 5]
    assert stats["marks"] >= 1
    assert not pictorial.any()


def test_segment_empty_page_has_no_marks():
    gray = np.full((100, 100), 250, dtype=np.uint8)
    ink, pictorial, stats = segmentation.segment(gray, dpi=100, k=0.2)
    assert not ink.any()
    assert stats["marks"] == 0


def test_flat_ink_chroma_variance_flags_single_ink_as_flat():
    fg = np.zeros((4, 4, 3), dtype=np.float32)
    fg[..., 0] = 10.0
    fg[..., 1] = 10.0
    fg[..., 2] = 10.0
    assert segmentation.flat_ink_chroma_variance(fg) < segmentation.FLAT_INK_CHROMA_VARIANCE


def test_flat_ink_chroma_variance_flags_mixed_colour_ink_as_not_flat():
    fg = np.zeros((4, 4, 3), dtype=np.float32)
    fg[0, 0] = [255, 0, 0]
    fg[1, 1] = [0, 255, 0]
    fg[2, 2] = [0, 0, 255]
    fg[3, 3] = [10, 10, 10]
    assert segmentation.flat_ink_chroma_variance(fg) >= segmentation.FLAT_INK_CHROMA_VARIANCE


def test_mask_image_convention_is_0_ink_1_paper():
    ink = np.zeros((4, 4), dtype=bool)
    ink[1, 1] = True
    mask = segmentation.mask_image(ink)
    assert mask.mode == "1"
    arr = np.array(mask)
    assert arr[1, 1] == 0  # ink pixel encodes as 0
    assert arr[0, 0] == 1  # paper pixel encodes as 1


def test_dilate_bool_grows_by_radius_in_every_direction():
    mask = np.zeros((11, 11), dtype=bool)
    mask[5, 5] = True
    grown = segmentation.dilate_bool(mask, radius=2)
    assert grown[3:8, 3:8].all()  # every pixel within Chebyshev distance 2
    assert not grown[2, 5]
    assert not grown[5, 2]


def test_dilate_bool_radius_zero_is_a_noop():
    mask = np.zeros((5, 5), dtype=bool)
    mask[2, 2] = True
    assert np.array_equal(segmentation.dilate_bool(mask, radius=0), mask)


def test_dilate_bool_clips_at_array_edges():
    mask = np.zeros((5, 5), dtype=bool)
    mask[0, 0] = True
    grown = segmentation.dilate_bool(mask, radius=1)  # must not raise/wrap around
    assert grown[0:2, 0:2].all()
    assert not grown[2, 2]


def test_text_like_mask_keeps_a_compact_glyph_sized_component():
    ink = np.zeros((100, 100), dtype=bool)
    ink[40:52, 40:48] = True  # a 12x8 blob, close to a typical glyph
    like_text = segmentation.text_like_mask(ink, text_height=14)
    assert like_text[45, 44]


def test_text_like_mask_drops_a_tall_boundary_tracing_component():
    """Regression test: the border-ring artifact this exists to filter out
    of the background-ghost dilation was, in the real document that found
    it, one connected component spanning almost the whole photo box --
    bbox_h=1035 against a page text_height of 26. A component many times
    taller than any glyph must not be treated as text."""
    ink = np.zeros((200, 200), dtype=bool)
    ink[10:190, 50] = True  # a thin, very tall vertical run
    like_text = segmentation.text_like_mask(ink, text_height=14)
    assert not like_text.any()


def test_text_like_mask_drops_a_short_wide_low_fill_fragment():
    """Regression test: a border-trace fragment can be short enough
    (height-wise) to pass a height-only check while still being a jagged,
    sparse trace rather than text -- measured directly on a real document:
    bbox_h=29 bbox_w=468 fill=0.686, next to a page text_height of 26.
    Width must also gate unless the component is solid enough to be a
    deliberate rule."""
    ink = np.zeros((40, 400), dtype=bool)
    rng = np.random.default_rng(0)
    # ~65% fill, matching the measured fragment -- not the near-total fill
    # of a real straight rule. At this density the whole band is one
    # connected component (bbox_h=20 bbox_w=400 fill=0.646, confirmed) --
    # a few incidental isolated single pixels elsewhere in the array are
    # expected to still read as "text-like" (harmless: dilating a lone
    # stray pixel by a few px does no damage), so check the fragment
    # itself, not the whole array.
    ink[10:30, :] = rng.random((20, 400)) < 0.65
    like_text = segmentation.text_like_mask(ink, text_height=14)
    assert not like_text[15, 200]


def test_text_like_mask_keeps_a_wide_but_solid_rule():
    """A real horizontal rule/underline can legitimately be far wider than
    any glyph -- measured directly on a real document: a 0.6pt rule came
    out bbox_h=6 bbox_w=801 fill=0.981. It must still be protected by the
    background-ghost dilation despite its width."""
    ink = np.zeros((20, 400), dtype=bool)
    ink[8:12, 5:399] = True  # a solid, thin, wide bar
    like_text = segmentation.text_like_mask(ink, text_height=14)
    assert like_text[9, 200]


def test_busy_region_mask_flags_a_sustained_multi_block_run():
    """Regression test for the "photo edge gets dilated like text" bug: a
    smooth photo/gradient can leak scattered ink through Sauvola without
    ever tripping `_pictorial_blocks`' own 55%-coverage bar (measured on a
    real photo fixture: it topped out at 42%), so `text_like_mask`'s
    per-component shape check alone cannot tell a small leaked fragment
    from a real glyph. `busy_region_mask` instead looks for coverage
    sustained over many block-heights, which a single text line never is.
    """
    dpi = 32  # pictorial_block_size(32) == 8
    ink = np.zeros((64, 64), dtype=bool)
    rng = np.random.default_rng(0)
    # 4 block-rows x 4 block-cols at ~30% density -- above the 20% bar,
    # well below _pictorial_blocks' own 55% "solid photo" bar.
    ink[16:48, 16:48] = rng.random((32, 32)) < 0.30
    busy = segmentation.busy_region_mask(ink, dpi=dpi)
    assert busy[30, 30]


def test_busy_region_mask_ignores_a_single_dense_text_line():
    """A real text line can locally spike above `busy_region_mask`'s
    coverage bar too (measured on a real text fixture: individual blocks up
    to 26%) -- what protects it is height, not density: one line is at most
    one block tall before a low-coverage gap to the next line."""
    dpi = 32  # pictorial_block_size(32) == 8
    ink = np.zeros((64, 64), dtype=bool)
    rng = np.random.default_rng(1)
    ink[32:40, 8:56] = rng.random((8, 48)) < 0.30  # exactly one block-row tall
    busy = segmentation.busy_region_mask(ink, dpi=dpi)
    assert not busy.any()


def test_core_ink_rgb_prefers_dark_pixels_over_faint_edge_pixels():
    """Regression test for the "flat ink swatch is gray, not black" bug: a
    Sauvola threshold is a LOCAL comparison, so `ink` includes faint
    anti-aliased edge pixels alongside a stroke's solid dark core.
    Averaging the whole set washes the colour toward gray (measured on a
    real document: RGB(99,99,99) instead of anything close to black).
    """
    rgb = np.zeros((10, 10, 3), dtype=np.uint8)
    gray = np.zeros((10, 10), dtype=np.uint8)
    ink = np.ones((10, 10), dtype=bool)

    # Solid black "core" pixels (rows 0-3, 40% of the ink set)...
    rgb[0:4, :] = 0
    gray[0:4, :] = 0
    # ...outnumbered by faint gray "edge" pixels the local Sauvola
    # threshold also classified as ink (rows 4-9, 60%).
    rgb[4:10, :] = 180
    gray[4:10, :] = 180

    naive_mean = rgb[ink].astype(np.float64).mean(axis=0)
    assert naive_mean[0] > 100  # the bug: dragged toward gray by edge pixels

    core = segmentation.core_ink_rgb(rgb, ink, gray, percentile=10.0)
    assert core[0] < 10  # the fix: dominated by the solid dark core


def test_core_ink_rgb_falls_back_to_whole_set_when_nothing_survives_cutoff():
    rgb = np.full((4, 4, 3), 50, dtype=np.uint8)
    gray = np.full((4, 4), 50, dtype=np.uint8)  # every ink pixel identical
    ink = np.ones((4, 4), dtype=bool)
    color = segmentation.core_ink_rgb(rgb, ink, gray, percentile=10.0)
    assert np.allclose(color, 50.0)


def test_excluding_dilated_ink_removes_antialiasing_halo_from_background():
    """Regression test for the "background is a faint ghost of the text"
    bug: a Sauvola threshold only catches a glyph's darkest core pixels, so
    the anti-aliased ring around it is left classified as paper and
    contaminates `masked_mean`'s background average unless that ring is
    excluded too.
    """
    rgb = np.full((30, 30, 3), 255, dtype=np.uint8)
    ink = np.zeros((30, 30), dtype=bool)
    ink[15, 10:20] = True  # a one-pixel-tall "stroke"
    # a mid-gray anti-aliasing ring around the stroke, just below whatever
    # threshold decided `ink` -- still classified as paper.
    rgb[14, 10:20] = 150
    rgb[16, 10:20] = 150

    bare_paper = np.logical_not(ink)
    contaminated = segmentation.masked_mean(rgb, bare_paper, (1, 1))
    assert contaminated.mean() < 254.5  # the bug: ghost measurably darkens the bg

    dilated_exclude = np.logical_not(segmentation.dilate_bool(ink, radius=1))
    fixed = segmentation.masked_mean(rgb, dilated_exclude, (1, 1))
    assert np.allclose(fixed, 255.0, atol=1e-2)  # the fix: halo excluded too


# --- remaining paths ---


def test_clear_runs_erases_only_the_dropped_component():
    ink = np.zeros((6, 10), dtype=bool)
    ink[1:3, 1:4] = True  # component A
    ink[3:5, 6:9] = True  # component B (not touching A)
    comp = segmentation.components(ink)
    drop = comp.left > 4  # B only
    segmentation._clear_runs(ink, comp, drop)
    expected = np.zeros((6, 10), dtype=bool)
    expected[1:3, 1:4] = True
    assert np.array_equal(ink, expected)


def test_text_height_falls_back_when_nothing_is_kept():
    ink = np.zeros((20, 20), dtype=bool)
    ink[5:15, 5:8] = True
    comp = segmentation.components(ink)
    assert segmentation.estimate_text_height(comp, np.zeros(comp.count, bool), 7) == 7
    empty = segmentation.components(np.zeros((5, 5), dtype=bool))
    assert segmentation.estimate_text_height(empty, np.zeros(0, bool), 9) == 9


def test_block_grids_need_two_blocks_each_way():
    ink = np.ones((40, 400), dtype=bool)  # at 300 dpi a block is 75 px: one block tall
    comp = segmentation.components(ink)
    blocks, stats = segmentation._pictorial_blocks(ink, comp, np.zeros(comp.count, bool), dpi=300)
    assert blocks.shape == (1, 6) and not blocks.any()
    assert stats == {"halftone_blocks": 0, "picture_blocks": 0}
    assert not segmentation.busy_region_mask(ink, dpi=300).any()


def test_dense_dots_are_a_halftone():
    ink = np.zeros((300, 300), dtype=bool)
    ink[::6, ::6] = True  # isolated dots, 13 x 13 = 169 per 75 px block
    ink = ink | np.roll(ink, 1, axis=1)  # 2 px dots, still isolated
    comp = segmentation.components(ink)
    blocks, stats = segmentation._pictorial_blocks(ink, comp, np.zeros(comp.count, bool), dpi=300)
    assert stats["halftone_blocks"] == 16  # every block of the 4 x 4 grid
    assert blocks.all()


def test_segment_moves_a_solid_region_out_of_the_stencil():
    gray = np.full((600, 600), 250, dtype=np.uint8)
    i, j = np.indices((200, 200))
    check = (i // 4 + j // 4) % 2 == 0  # 4 px checkerboard: one 8-connected component
    gray[100:300, 100:300][check] = 20
    for y in range(400, 560, 40):  # a few text-like bars elsewhere
        gray[y : y + 12, 60:540] = 20
    ink, pictorial, _ = segmentation.segment(gray, dpi=300, k=0.2)
    assert pictorial[100:300, 100:300].any()
    assert not ink[100:300, 100:300].any()
    assert ink[400:412, 60:540].mean() > 0.9


def test_segment_when_the_second_pass_finds_nothing(monkeypatch):
    gray = np.full((100, 100), 250, dtype=np.uint8)
    gray[40:52, 10:90] = 20
    real = segmentation.sauvola_ink
    passes = []

    def first_only(g, window, k, R=segmentation.SAUVOLA_R):
        passes.append(window)
        return real(g, window, k, R) if len(passes) == 1 else np.zeros_like(g, dtype=bool)

    monkeypatch.setattr(segmentation, "sauvola_ink", first_only)
    ink, pictorial, stats = segmentation.segment(gray, dpi=300, k=0.2)
    assert len(passes) == 2
    assert not ink.any() and not pictorial.any() and stats["components"] == 0


def test_core_ink_rgb_without_ink_is_black():
    rgb = np.full((3, 3, 3), 200, dtype=np.uint8)
    none = np.zeros((3, 3), dtype=bool)
    assert np.array_equal(segmentation.core_ink_rgb(rgb, none, rgb[..., 0]), np.zeros(3))


# --- strip-wise windowed operations ---


def _naive_window(arr, radius, reduce):
    h, w = arr.shape
    out = np.empty((h, w), dtype=bool)
    for y in range(h):
        for x in range(w):
            win = arr[max(y - radius, 0) : y + radius + 1, max(x - radius, 0) : x + radius + 1]
            out[y, x] = reduce(win, arr[y, x])
    return out


def _naive_sauvola(gray, window, k, R=128.0):
    def below(win, g):
        win = win.astype(np.float64)
        m = win.mean()
        return g < m * (1 + k * (win.std() / R - 1))

    return _naive_window(gray, max(window // 2, 1), below)


@pytest.mark.parametrize("h,w,window", [(23, 17, 5), (40, 9, 21), (7, 30, 31), (1, 12, 3)])
def test_sauvola_in_strips_matches_the_direct_definition(monkeypatch, h, w, window):
    monkeypatch.setattr(segmentation, "STRIP_ROWS", 5)  # many strip boundaries
    gray = (np.random.default_rng(h * w + window).random((h, w)) * 255).astype(np.uint8)
    expected = _naive_sauvola(gray, window, 0.2)
    got = segmentation.sauvola_ink(gray, window, 0.2)
    # Ties at the threshold may round either way in float; there should be none here.
    assert np.array_equal(got, expected)


@pytest.mark.parametrize("h,w,radius", [(23, 17, 2), (31, 8, 6), (5, 40, 9)])
def test_dilation_in_strips_matches_the_direct_definition(monkeypatch, h, w, radius):
    monkeypatch.setattr(segmentation, "STRIP_ROWS", 5)
    mask = np.random.default_rng(radius * h).random((h, w)) < 0.04
    expected = _naive_window(mask, radius, lambda win, _: win.any())
    assert np.array_equal(segmentation.dilate_bool(mask, radius), expected)
