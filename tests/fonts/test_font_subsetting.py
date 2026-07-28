# tests/fonts/test_font_subsetting.py

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import struct
from unittest.mock import MagicMock, patch

import pytest
from fontTools.ttLib import TTLibError

from pdftl.fonts.font_subsetting import (
    _build_subsetter_options,
    capture_font_matrix,
    get_cff_topdict_if_present,
    restore_font_matrix_if_dropped,
    gids_for_cff_native_cid_font,
    gids_for_cid_font,
    gids_for_simple_font_via_cmap,
    gids_for_simple_font_via_encoding,
    open_font_for_subsetting,
    run_subsetter,
    subset_cff_native_cid_font,
    subset_cid_font,
    subset_simple_font,
    unwrap_bare_cff_from_sfnt,
    wrap_bare_cff_in_sfnt,
)


# ==============================================================================
# CFF SFNT Wrapping / Unwrapping Tests
# ==============================================================================


@patch("fontTools.ttLib.newTable")
@patch("fontTools.ttLib.TTFont")
def test_wrap_bare_cff_in_sfnt(mock_ttfont_cls, mock_new_table):
    mock_tt = MagicMock()
    mock_cff_table = MagicMock()
    mock_ttfont_cls.return_value = mock_tt
    mock_new_table.return_value = mock_cff_table
    mock_tt.__getitem__.return_value = mock_cff_table

    cff_bytes = b"fake_cff_bytes"
    result = wrap_bare_cff_in_sfnt(cff_bytes)

    mock_ttfont_cls.assert_called_once_with(sfntVersion="OTTO")
    mock_new_table.assert_called_once_with("CFF ")
    mock_cff_table.decompile.assert_called_once_with(cff_bytes, mock_tt)
    assert result == mock_tt


def test_unwrap_bare_cff_from_sfnt():
    mock_tt = MagicMock()
    mock_cff_table = MagicMock()
    mock_cff_table.compile.return_value = b"compiled_cff_bytes"
    mock_tt.__getitem__.side_effect = lambda key: mock_cff_table if key == "CFF " else None

    result = unwrap_bare_cff_from_sfnt(mock_tt)

    mock_cff_table.compile.assert_called_once_with(mock_tt)
    assert result == b"compiled_cff_bytes"


@patch("pdftl.fonts.cff_fontmatrix_splice.splice_top_font_matrix")
def test_unwrap_bare_cff_from_sfnt_splices_when_matrices_supplied(mock_splice):
    """When `original_matrices` carries a 'top' entry, the compiled bytes
    are run through splice_top_font_matrix before being returned."""
    mock_splice.return_value = b"spliced_bytes"
    mock_tt = MagicMock()
    mock_cff_table = MagicMock()
    mock_cff_table.compile.return_value = b"compiled_cff_bytes"
    mock_tt.__getitem__.side_effect = lambda key: mock_cff_table if key == "CFF " else None

    result = unwrap_bare_cff_from_sfnt(mock_tt, {"top": (0.001, 0, 0, 0.001, 0, 0)})

    mock_splice.assert_called_once_with(b"compiled_cff_bytes", (0.001, 0, 0, 0.001, 0, 0))
    assert result == b"spliced_bytes"


def test_unwrap_bare_cff_from_sfnt_skips_splice_when_no_top_matrix():
    """A non-empty original_matrices with no 'top' key (e.g. only FD-level
    entries) must NOT trigger the splice -- only the Top DICT case is
    handled by this module today."""
    mock_tt = MagicMock()
    mock_cff_table = MagicMock()
    mock_cff_table.compile.return_value = b"compiled_cff_bytes"
    mock_tt.__getitem__.side_effect = lambda key: mock_cff_table if key == "CFF " else None

    result = unwrap_bare_cff_from_sfnt(mock_tt, {"fd:0": (1, 0, 0, 1, 0, 0)})

    assert result == b"compiled_cff_bytes"


# ==============================================================================
# capture_font_matrix / restore_font_matrix_if_dropped (superseded)
# ==============================================================================


def test_get_cff_topdict_if_present_no_cff_table():
    mock_tt = MagicMock()
    mock_tt.__contains__.side_effect = lambda key: False
    assert get_cff_topdict_if_present(mock_tt) is None


def test_capture_font_matrix_no_cff_table():
    mock_tt = MagicMock()
    mock_tt.__contains__.side_effect = lambda key: False
    assert capture_font_matrix(mock_tt) is None


def test_capture_font_matrix_top_only_no_fdarray():
    mock_topdict = MagicMock(spec=["rawDict"])
    mock_topdict.rawDict = {"FontMatrix": [0.001, 0, 0, 0.001, 0, 0]}
    mock_cff = MagicMock()
    mock_cff.fontNames = ["F"]
    mock_cff.__getitem__.side_effect = lambda name: mock_topdict
    mock_cff_table = MagicMock()
    mock_cff_table.cff = mock_cff
    mock_tt = MagicMock()
    mock_tt.__contains__.side_effect = lambda key: key == "CFF "
    mock_tt.__getitem__.side_effect = lambda key: mock_cff_table if key == "CFF " else None

    result = capture_font_matrix(mock_tt)
    assert result == {"top": (0.001, 0, 0, 0.001, 0, 0)}


def test_capture_font_matrix_with_fdarray_entries():
    """Exercises the FDArray-walking branch: per-FD FontMatrix values are
    captured under 'fd:<index>' keys alongside (or instead of) 'top'."""
    mock_fd0 = MagicMock()
    mock_fd0.rawDict = {"FontMatrix": [1, 0, 0, 1, 0, 0]}
    mock_fd1 = MagicMock()
    mock_fd1.rawDict = {}  # no explicit FontMatrix -- must be omitted, not None

    mock_topdict = MagicMock()
    mock_topdict.rawDict = {}
    mock_topdict.FDArray = [mock_fd0, mock_fd1]
    mock_cff = MagicMock()
    mock_cff.fontNames = ["F"]
    mock_cff.__getitem__.side_effect = lambda name: mock_topdict
    mock_cff_table = MagicMock()
    mock_cff_table.cff = mock_cff
    mock_tt = MagicMock()
    mock_tt.__contains__.side_effect = lambda key: key == "CFF "
    mock_tt.__getitem__.side_effect = lambda key: mock_cff_table if key == "CFF " else None

    result = capture_font_matrix(mock_tt)
    assert result == {"fd:0": (1, 0, 0, 1, 0, 0)}


def test_capture_font_matrix_returns_none_when_nothing_explicit():
    """Neither the Top DICT nor any FD has an explicit FontMatrix ->
    the whole snapshot is None (not an empty dict), per the documented
    'omitted, not stored as None' contract."""
    mock_topdict = MagicMock()
    mock_topdict.rawDict = {}
    mock_topdict.FDArray = [MagicMock(rawDict={})]
    mock_cff = MagicMock()
    mock_cff.fontNames = ["F"]
    mock_cff.__getitem__.side_effect = lambda name: mock_topdict
    mock_cff_table = MagicMock()
    mock_cff_table.cff = mock_cff
    mock_tt = MagicMock()
    mock_tt.__contains__.side_effect = lambda key: key == "CFF "
    mock_tt.__getitem__.side_effect = lambda key: mock_cff_table if key == "CFF " else None

    assert capture_font_matrix(mock_tt) is None


def test_restore_font_matrix_if_dropped_is_superseded():
    """Pins the deliberate dead-end: any remaining call site must fail
    loudly, not silently do nothing, so a reintroduced call is caught
    immediately rather than reproducing the original silent-drop bug."""
    with pytest.raises(NotImplementedError, match="splice_top_font_matrix"):
        restore_font_matrix_if_dropped(MagicMock(), {"top": (0.001, 0, 0, 0.001, 0, 0)})


# ==============================================================================
# open_font_for_subsetting Tests
# ==============================================================================


@patch("pdftl.fonts.font_subsetting.wrap_bare_cff_in_sfnt")
def test_open_font_for_subsetting_bare_cff_success(mock_wrap):
    mock_tt = MagicMock()
    mock_wrap.return_value = mock_tt

    result = open_font_for_subsetting(b"raw_bytes", is_bare_cff=True)

    mock_wrap.assert_called_once_with(b"raw_bytes")
    assert result == mock_tt


@patch("fontTools.ttLib.TTFont")
def test_open_font_for_subsetting_sfnt_success(mock_ttfont_cls):
    mock_tt = MagicMock()
    mock_ttfont_cls.return_value = mock_tt

    result = open_font_for_subsetting(b"raw_bytes", is_bare_cff=False)

    assert result == mock_tt


@pytest.mark.parametrize(
    "exc",
    [
        TTLibError("bad font"),
        ValueError("invalid value"),
        KeyError("missing key"),
        struct.error("unpack failed"),
    ],
)
@patch("fontTools.ttLib.TTFont")
def test_open_font_for_subsetting_exceptions(mock_ttfont_cls, exc):
    mock_ttfont_cls.side_effect = exc

    result = open_font_for_subsetting(b"bad_bytes", is_bare_cff=False)

    assert result is None


# ==============================================================================
# _build_subsetter_options & run_subsetter Tests
# ==============================================================================


def test_build_subsetter_options():
    opts_default = _build_subsetter_options()
    assert opts_default.glyph_names is False
    assert opts_default.retain_gids is False
    # desubroutinize=True is intentional, not a default fontTools would
    # give you -- it works around a subr-index-renumbering bug that
    # silently corrupts glyph outlines across the local/global subr bias
    # thresholds (see _build_subsetter_options' own docstring).
    assert opts_default.desubroutinize is True

    opts_custom = _build_subsetter_options(keep_names=True, retain_gids=True)
    assert opts_custom.glyph_names is True
    assert opts_custom.retain_gids is True


def test_run_subsetter_empty_inputs():
    mock_tt = MagicMock()
    result = run_subsetter(mock_tt, unicodes=set(), gids=set())
    assert result is False


@patch("fontTools.subset.Subsetter")
def test_run_subsetter_populates_gids_and_unicodes(mock_subsetter_cls):
    mock_subsetter = MagicMock()
    mock_subsetter_cls.return_value = mock_subsetter

    mock_tt = MagicMock()
    mock_tt.getGlyphOrder.return_value = [".notdef", "A", "B"]

    result = run_subsetter(mock_tt, unicodes={"A", "BC"}, gids={2, 1})

    mock_subsetter.populate.assert_any_call(gids=[1, 2])
    # ord('A')=65, ord('B')=66, ord('C')=67
    mock_subsetter.populate.assert_any_call(unicodes=[65, 66, 67])
    mock_subsetter.subset.assert_called_once_with(mock_tt)
    assert result is True


@pytest.mark.parametrize(
    "exc",
    [
        KeyError("missing"),
        ValueError("invalid"),
        TypeError("type mismatch"),
        struct.error("struct err"),
    ],
)
@patch("fontTools.subset.Subsetter")
def test_run_subsetter_handles_exceptions(mock_subsetter_cls, exc):
    mock_subsetter = MagicMock()
    mock_subsetter.subset.side_effect = exc
    mock_subsetter_cls.return_value = mock_subsetter

    mock_tt = MagicMock()
    result = run_subsetter(mock_tt, unicodes={"A"}, gids=set())

    assert result is False


@patch("fontTools.subset.Subsetter")
def test_run_subsetter_glyph_order_too_short(mock_subsetter_cls):
    mock_subsetter = MagicMock()
    mock_subsetter_cls.return_value = mock_subsetter

    mock_tt = MagicMock()
    mock_tt.getGlyphOrder.return_value = [".notdef"]  # Only 1 glyph retained

    result = run_subsetter(mock_tt, unicodes=set(), gids={1})

    assert result is False


# ==============================================================================
# Simple Font GID Resolution Tests
# ==============================================================================


def test_gids_for_simple_font_via_cmap_no_cmap_table():
    mock_tt = MagicMock()
    mock_tt.__contains__.side_effect = lambda key: False

    result = gids_for_simple_font_via_cmap(mock_tt, {65})
    assert result == set()


@patch("pdftl.fonts.font_binary_sfnt._get_best_cmap")
def test_gids_for_simple_font_via_cmap_exception(mock_get_best_cmap):
    mock_tt = MagicMock()
    mock_tt.__contains__.side_effect = lambda key: key == "cmap"
    mock_get_best_cmap.side_effect = TTLibError("cmap read failed")

    result = gids_for_simple_font_via_cmap(mock_tt, {65})
    assert result == set()


@patch("pdftl.fonts.font_binary_sfnt._effective_cmap_code", side_effect=lambda c: c)
@patch("pdftl.fonts.font_binary_sfnt._get_best_cmap")
def test_gids_for_simple_font_via_cmap_success(mock_get_best_cmap, mock_effective):
    mock_tt = MagicMock()
    mock_tt.__contains__.side_effect = lambda key: key == "cmap"
    mock_tt.getGlyphOrder.return_value = [".notdef", "A", "B"]

    # 65 -> 'A' (GID 1), 66 -> 'C' (not in font), 67 -> 'B' (GID 2)
    mock_get_best_cmap.return_value = {65: "A", 66: "C", 67: "B"}

    result = gids_for_simple_font_via_cmap(mock_tt, {65, 66, 68})
    assert result == {1}


@patch("pdftl.fonts.font_encoding_tables._resolve_glyph_name")
@patch("pdftl.fonts.font_encoding_tables._get_maps")
def test_gids_for_simple_font_via_encoding(mock_get_maps, mock_resolve_glyph):
    mock_get_maps.return_value = ({}, {})
    mock_tt = MagicMock()
    mock_tt.getGlyphOrder.return_value = [".notdef", "space", "A"]

    def resolve_side_effect(code, char, diffs, base):
        if code == 32:
            return "space"
        if code == 65:
            return "A"
        if code == 99:
            return "missing_glyph"
        return None

    mock_resolve_glyph.side_effect = resolve_side_effect

    result = gids_for_simple_font_via_encoding(
        mock_tt, codes={32, 65, 99, 100}, differences=None, base_encoding="WinAnsiEncoding"
    )
    assert result == {1, 2}


# ==============================================================================
# CID Font GID Resolution Tests
# ==============================================================================


def test_gids_for_cid_font():
    mock_tt = MagicMock()
    mock_tt.getGlyphOrder.return_value = [".notdef", "cid00001", "cid00002"]

    # 1. Identity map
    res_identity = gids_for_cid_font(mock_tt, cids={1, 5, -1}, cid_to_gid_map="Identity")
    assert res_identity == {1}

    # 2. Dictionary map
    dict_map = {10: 2, 20: 99}  # 99 out of bounds
    res_dict = gids_for_cid_font(mock_tt, cids={10, 20, 30}, cid_to_gid_map=dict_map)
    assert res_dict == {2}

    # 3. Invalid map type
    res_invalid = gids_for_cid_font(mock_tt, cids={1}, cid_to_gid_map=12345)
    assert res_invalid == set()


@patch("pdftl.fonts.cff_binary_utils._resolve_cff_cid_to_gid")
def test_gids_for_cff_native_cid_font(mock_resolve_cid):
    mock_topdict = MagicMock()
    mock_cff = MagicMock()
    mock_cff.fontNames = ["TestFont"]
    mock_cff.__getitem__.side_effect = lambda name: mock_topdict

    mock_cff_table = MagicMock()
    mock_cff_table.cff = mock_cff

    mock_tt = MagicMock()
    mock_tt.__getitem__.side_effect = lambda key: mock_cff_table if key == "CFF " else None

    mock_resolve_cid.side_effect = lambda topdict, cid: 5 if cid == 100 else None

    result = gids_for_cff_native_cid_font(mock_tt, cids={100, 200})
    assert result == {5}


# ==============================================================================
# High-Level Subsetting Dispatcher Tests
# ==============================================================================


@patch("pdftl.fonts.font_subsetting.run_subsetter")
@patch("pdftl.fonts.font_subsetting.gids_for_simple_font_via_encoding")
@patch("pdftl.fonts.font_subsetting.gids_for_simple_font_via_cmap")
def test_subset_simple_font_routing(mock_via_cmap, mock_via_encoding, mock_run_subsetter):
    mock_tt = MagicMock()
    mock_via_cmap.return_value = {1}
    mock_via_encoding.return_value = {2}
    mock_run_subsetter.return_value = True

    # 1. Standard sfnt route via cmap
    res1 = subset_simple_font(mock_tt, codes={65}, unicodes={"A"}, keep_names=True)
    mock_via_cmap.assert_called_once_with(mock_tt, {65})
    mock_run_subsetter.assert_called_with(mock_tt, {"A"}, {1}, keep_names=True, retain_gids=False)
    assert res1 is True

    # 2. Bare CFF route via encoding
    subset_simple_font(mock_tt, codes={65}, unicodes=set(), keep_names=False, is_bare_cff=True)
    mock_via_encoding.assert_called_with(mock_tt, {65}, None, None)

    # 3. /Differences present route via encoding
    subset_simple_font(
        mock_tt, codes={65}, unicodes=set(), keep_names=False, differences=["/diff"]
    )
    mock_via_encoding.assert_called_with(mock_tt, {65}, ["/diff"], None)


@patch("pdftl.fonts.font_subsetting.run_subsetter")
def test_subset_cid_font_failure(mock_run_subsetter):
    mock_tt = MagicMock()
    mock_tt.getGlyphOrder.return_value = [".notdef", "cid1"]
    mock_run_subsetter.return_value = False

    success, new_map = subset_cid_font(
        mock_tt, cids={1}, cid_to_gid_map="Identity", keep_names=True
    )

    assert success is False
    assert new_map == {}


@patch("pdftl.fonts.font_subsetting.run_subsetter")
def test_subset_cid_font_success_and_remapping(mock_run_subsetter):
    mock_tt = MagicMock()
    pre_subset = [".notdef", "cid00001", "cid00002", "cid00003"]
    post_subset = [".notdef", "cid00002", "cid00001"]

    mock_tt.getGlyphOrder.side_effect = [pre_subset, pre_subset, post_subset]
    mock_run_subsetter.return_value = True

    cid_map = {1: 1, 2: 2, 3: 3, 4: 99, 5: -1}  # 4 & 5 out of bounds
    success, new_map = subset_cid_font(
        mock_tt, cids={1, 2, 3, 4, 5}, cid_to_gid_map=cid_map, keep_names=False
    )

    assert success is True
    # In post-subset: 'cid00001' is at index 2, 'cid00002' is at index 1, 'cid00003' dropped
    assert new_map == {1: 2, 2: 1}


@patch("pdftl.fonts.font_subsetting.run_subsetter")
@patch("pdftl.fonts.font_subsetting.gids_for_cff_native_cid_font")
def test_subset_cff_native_cid_font(mock_gids_func, mock_run_subsetter):
    mock_tt = MagicMock()
    mock_gids_func.return_value = {10, 20}
    mock_run_subsetter.return_value = True

    result = subset_cff_native_cid_font(mock_tt, cids={1, 2}, keep_names=True)

    mock_gids_func.assert_called_once_with(mock_tt, {1, 2})
    mock_run_subsetter.assert_called_once_with(mock_tt, set(), {10, 20}, keep_names=True)
    assert result is True


@patch("pdftl.fonts.font_subsetting.run_subsetter")
def test_subset_cid_font_invalid_map_type(mock_run_subsetter):
    """Triggers line 278 (gid = None) when cid_to_gid_map is neither 'Identity' nor a dict."""
    mock_tt = MagicMock()
    mock_tt.getGlyphOrder.return_value = [".notdef", "cid00001"]
    mock_run_subsetter.return_value = False

    # Passing an invalid type like None or "UnsupportedString" triggers line 278
    success, new_map = subset_cid_font(mock_tt, cids={1}, cid_to_gid_map=None, keep_names=True)

    assert success is False
    assert new_map == {}


def test_promote_legacy_format0_cmap_subtables():
    """
    _promote_legacy_format0_cmap_subtables converts a format-0 cmap
    subtable to an equivalent format-6 subtable in place (so it survives
    fontTools' unconditional format-0 drop during subsetting), but skips
    that conversion when a non-format-0 subtable already occupies the
    exact same (platform, encoding, language) slot -- exercises the
    no-cmap-table short-circuit, the promotion path, and the skip guard.
    """
    from pdftl.fonts.font_subsetting import _promote_legacy_format0_cmap_subtables

    # No 'cmap' table at all -- e.g. a bare-CFF sfnt shell.
    mock_tt_no_cmap = MagicMock()
    mock_tt_no_cmap.__contains__.side_effect = lambda key: False
    _promote_legacy_format0_cmap_subtables(mock_tt_no_cmap)  # must not raise

    def _make_subtable(fmt, platform_id, plat_enc_id, language, cmap):
        t = MagicMock()
        t.format = fmt
        t.platformID = platform_id
        t.platEncID = plat_enc_id
        t.language = language
        t.cmap = cmap
        return t

    # A lone format-0 (1, 0) subtable gets promoted to format 6, keeping
    # its platform/encoding/language and cmap data intact.
    legacy_table = _make_subtable(0, 1, 0, 0, {1: "glyph00001", 2: "glyph00002"})
    mock_cmap_table = MagicMock()
    mock_cmap_table.tables = [legacy_table]

    mock_tt = MagicMock()
    mock_tt.__contains__.side_effect = lambda key: key == "cmap"
    mock_tt.__getitem__.side_effect = lambda key: mock_cmap_table if key == "cmap" else None

    _promote_legacy_format0_cmap_subtables(mock_tt)

    assert len(mock_cmap_table.tables) == 1
    promoted = mock_cmap_table.tables[0]
    assert promoted.format == 6
    assert promoted.platformID == 1
    assert promoted.platEncID == 0
    assert promoted.language == 0
    assert promoted.cmap == {1: "glyph00001", 2: "glyph00002"}
    # The promoted subtable's cmap dict must be a copy, not the same
    # object -- mutating one shouldn't mutate the other.
    assert promoted.cmap is not legacy_table.cmap

    # A format-0 (1, 0) subtable is left untouched when a non-format-0
    # subtable already occupies that exact (platform, encoding,
    # language) triple -- promoting it too would create a duplicate.
    dup_legacy = _make_subtable(0, 1, 0, 0, {1: "glyph00001"})
    existing_non_format0 = _make_subtable(4, 1, 0, 0, {1: "glyph00001"})
    mock_cmap_table_dup = MagicMock()
    mock_cmap_table_dup.tables = [dup_legacy, existing_non_format0]

    mock_tt_dup = MagicMock()
    mock_tt_dup.__contains__.side_effect = lambda key: key == "cmap"
    mock_tt_dup.__getitem__.side_effect = (
        lambda key: mock_cmap_table_dup if key == "cmap" else None
    )

    _promote_legacy_format0_cmap_subtables(mock_tt_dup)

    assert mock_cmap_table_dup.tables[0] is dup_legacy
    assert mock_cmap_table_dup.tables[0].format == 0
    assert mock_cmap_table_dup.tables[1] is existing_non_format0


# --- append to tests/fonts/test_font_subsetting.py ---

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen


def _build_glyf_font(with_cmap: bool, with_os2: bool):
    """A minimal real TrueType (glyf-based) sfnt, with cmap/OS2 presence
    toggleable -- mirrors a CIDFontType2 font addressed purely via
    /CIDToGIDMap (Identity-H), which legitimately has no 'cmap' table at
    all since PDF text is shown by CID, never looked up by Unicode."""
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef", "A", "B"])
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((0, 500))
    pen.lineTo((500, 500))
    pen.closePath()
    glyph = pen.glyph()
    fb.setupGlyf({".notdef": glyph, "A": glyph, "B": glyph})
    fb.setupHorizontalMetrics({".notdef": (500, 0), "A": (500, 0), "B": (500, 0)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Test", "styleName": "Regular"})
    fb.setupPost()
    fb.setupCharacterMap({65: "A", 66: "B"})  # needed before setupOS2, if used
    if with_os2:
        fb.setupOS2()
    tt = fb.font
    if not with_cmap:
        del tt["cmap"]
    return tt


def test_cidfonttype2_style_font_with_os2_and_no_cmap_subsets_successfully():
    """Regression test: fontTools.subset's _prune_post_subset unconditionally
    calls OS/2.recalcUnicodeRanges(), which does `for table in
    ttFont["cmap"].tables` with no guard for a missing cmap table --
    raising KeyError('cmap') for any font that has OS/2 but no cmap, even
    though that's a legitimate, common shape for a CIDFontType2 font
    addressed purely via /CIDToGIDMap (Identity-H). Before the fix, this
    made run_subsetter fail (and log a warning) for every such font,
    silently subsetting nothing."""
    tt = _build_glyf_font(with_cmap=False, with_os2=True)
    assert "cmap" not in tt

    result = run_subsetter(tt, unicodes=set(), gids={1}, retain_gids=True)

    assert result is True
    assert len(tt.getGlyphOrder()) > 1


def test_synthetic_cmap_does_not_survive_subsetting():
    """The temporary empty 'cmap' table added to work around the OS/2 bug
    must never leak into the final subsetted font -- a font that had no
    cmap before subsetting must still have none afterward."""
    tt = _build_glyf_font(with_cmap=False, with_os2=True)

    run_subsetter(tt, unicodes=set(), gids={1}, retain_gids=True)

    assert "cmap" not in tt


def test_font_with_real_cmap_and_os2_keeps_its_cmap():
    """Regression guard the other direction: a font that DOES legitimately
    carry a cmap table must still have one after subsetting -- the fix
    must only add/remove a synthetic cmap when one didn't already exist,
    never touch a real one."""
    tt = _build_glyf_font(with_cmap=True, with_os2=True)
    assert "cmap" in tt

    result = run_subsetter(tt, unicodes=set(), gids={1}, retain_gids=True)

    assert result is True
    assert "cmap" in tt


def test_font_with_no_os2_and_no_cmap_subsets_successfully():
    """Without an OS/2 table, recalcUnicodeRanges is never called at all,
    so this case was never actually broken -- included as a control to
    confirm the fix's `"OS/2" in tt` guard doesn't change behavior for
    fonts that don't need the workaround."""
    tt = _build_glyf_font(with_cmap=False, with_os2=False)
    assert "cmap" not in tt
    assert "OS/2" not in tt

    result = run_subsetter(tt, unicodes=set(), gids={1}, retain_gids=True)

    assert result is True
    assert "cmap" not in tt


# --- append to tests/fonts/test_font_subsetting.py ---


class TestGetBestCmapSafe:
    """_get_best_cmap_safe is never called directly by any existing test
    -- only indirectly (and never in a way that hits all three of its
    branches) via gids_for_simple_font_via_encoding's own mocking."""

    def test_no_cmap_table_returns_none(self):
        from pdftl.fonts.font_subsetting import _get_best_cmap_safe

        mock_tt = MagicMock()
        mock_tt.__contains__.side_effect = lambda key: False
        assert _get_best_cmap_safe(mock_tt) is None

    def test_success_returns_cmap(self):
        from pdftl.fonts.font_subsetting import _get_best_cmap_safe

        mock_tt = MagicMock()
        mock_tt.__contains__.side_effect = lambda key: key == "cmap"
        mock_tt.getBestCmap.return_value = {65: "A"}
        assert _get_best_cmap_safe(mock_tt) == {65: "A"}

    def test_exception_returns_none(self):
        from pdftl.fonts.font_subsetting import _get_best_cmap_safe

        mock_tt = MagicMock()
        mock_tt.__contains__.side_effect = lambda key: key == "cmap"
        mock_tt.getBestCmap.side_effect = ValueError("corrupt cmap")
        assert _get_best_cmap_safe(mock_tt) is None


class TestResolveCodeToGidAglFallbackTail:
    """_resolve_code_to_gid falls back through the font's own best cmap
    once a direct name_to_gid lookup misses -- covers the remaining
    branches past that fallback: no Unicode value for the glyph name,
    and a resolved Unicode value whose cmap-derived name still isn't in
    name_to_gid."""

    def test_no_unicode_value_for_glyph_name_returns_none(self):
        from pdftl.fonts.font_subsetting import _resolve_code_to_gid

        with patch(
            "pdftl.fonts.font_encoding_tables._resolve_glyph_name",
            return_value="nonexistent_glyph_name_xyz",
        ):
            result = _resolve_code_to_gid(
                code=65,
                differences_map={},
                base_encoding_map={},
                name_to_gid={"A": 1},  # doesn't contain our resolved name
                best_cmap={65: "something"},
            )
        assert result is None

    def test_cmap_resolved_name_not_in_font_returns_none(self):
        from pdftl.fonts.font_subsetting import _resolve_code_to_gid

        with patch(
            "pdftl.fonts.font_encoding_tables._resolve_glyph_name",
            return_value="quotedblleft",  # has a real AGL Unicode value
        ):
            result = _resolve_code_to_gid(
                code=147,
                differences_map={},
                base_encoding_map={},
                name_to_gid={"A": 1},  # font doesn't have whatever the cmap calls it
                best_cmap={},  # cmap has no entry at all for that unicode value
            )
        assert result is None

    def test_cmap_fallback_succeeds(self):
        from pdftl.fonts.font_subsetting import _resolve_code_to_gid

        with patch(
            "pdftl.fonts.font_encoding_tables._resolve_glyph_name",
            return_value="quotedblleft",
        ):
            result = _resolve_code_to_gid(
                code=147,
                differences_map={},
                base_encoding_map={},
                name_to_gid={"uni201C": 5},  # font's own synthesized name for U+201C
                best_cmap={0x201C: "uni201C"},
            )
        assert result == 5
