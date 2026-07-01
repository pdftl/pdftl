# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/fonts/test_font_binary_sfnt_cid_cff.py

"""
Real (non-mocked) tests for the CID-keyed branch of
pdftl.fonts.font_binary_sfnt._patch_cff_table_in_sfnt, against a genuine
OpenType-wrapped, CID-keyed CFF program built via fontTools.cffLib -- the
sfnt-wrapped counterpart to tests/fonts/test_cff_binary_utils.py's bare
CID-keyed CFF fixtures, since the CFF table's own internal CID-keying is
identical whether or not it's sfnt-wrapped.
"""

from __future__ import annotations

import sys
from pathlib import Path
from io import BytesIO

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from font_fixture_builder import (  # noqa: E402
    SQUARE_500,
    TRIANGLE_300,
    build_cid_keyed_cff_bytes,
)


def _wrap_cff_in_minimal_sfnt(cff_bytes: bytes) -> bytes:
    """
    Wraps a bare CID-keyed CFF byte stream in a minimal OpenType/CFF sfnt
    container via fontTools, mirroring how a real /FontFile3 /Subtype
    /OpenType descendant of a CIDFontType0 would be structured -- a real
    sfnt wrapper is needed here since TTFont() (used by the code under
    test) cannot open a bare CFF table directly.
    """
    from fontTools.ttLib import TTFont, newTable
    from fontTools.cffLib import CFFFontSet

    cff_font_set = CFFFontSet()
    cff_font_set.decompile(BytesIO(cff_bytes), otFont=None)
    topdict = cff_font_set[cff_font_set.fontNames[0]]

    tt = TTFont(sfntVersion="OTTO")
    tt.setGlyphOrder(list(topdict.charset))
    cmap = newTable("cmap")
    cmap.tableVersion = 0
    cmap.tables = []
    tt["cmap"] = cmap
    cff_table = newTable("CFF ")
    cff_table.cff = cff_font_set
    tt["CFF "] = cff_table

    buf = BytesIO()
    tt.save(buf)
    return buf.getvalue()


@pytest.fixture
def cid_keyed_sfnt_cff_path(tmp_path) -> Path:
    """A genuine OpenType/CFF sfnt file wrapping a CID-keyed CFF program:
    CID 1 -> square (width 500), CID 2 -> triangle (width 300)."""
    cff_bytes = build_cid_keyed_cff_bytes({1: SQUARE_500, 2: TRIANGLE_300})
    sfnt_bytes = _wrap_cff_in_minimal_sfnt(cff_bytes)
    path = tmp_path / "cid_keyed.otf"
    path.write_bytes(sfnt_bytes)
    return path


def test_patch_cff_table_in_sfnt_patches_cid_keyed_charstring_widths(cid_keyed_sfnt_cff_path):
    """The CFF table's own charstring width (not just hmtx) is rewritten
    for a CID resolved via the CFF's own ROS/charset mechanism."""
    from fontTools.ttLib import TTFont
    from pdftl.fonts.font_binary_sfnt import _patch_cff_table_in_sfnt

    tt = TTFont(cid_keyed_sfnt_cff_path)
    patched = _patch_cff_table_in_sfnt(
        tt, {"0001": 650.0}, differences=None, base_encoding=None, cid_to_gid_map="Identity"
    )
    assert patched is True

    cff = tt["CFF "].cff
    topdict = cff[cff.fontNames[0]]
    charstring = topdict.CharStrings["cid00001"]
    charstring.decompile()
    charstring.draw(__import__("fontTools.pens.basePen", fromlist=["NullPen"]).NullPen())
    assert charstring.width == 650.0


def test_patch_cff_table_in_sfnt_cid_keyed_no_match_returns_false(cid_keyed_sfnt_cff_path):
    """A CID with no corresponding pdf_widths entry patches nothing."""
    from fontTools.ttLib import TTFont
    from pdftl.fonts.font_binary_sfnt import _patch_cff_table_in_sfnt

    tt = TTFont(cid_keyed_sfnt_cff_path)
    patched = _patch_cff_table_in_sfnt(
        tt, {"FFFF": 1.0}, differences=None, base_encoding=None, cid_to_gid_map="Identity"
    )
    assert patched is False


def test_patch_cff_table_in_sfnt_no_cff_table_returns_false():
    """Guards the pre-existing 'CFF ' not in tt branch still short-circuits
    before any CID/name resolution is attempted."""
    from pdftl.fonts.font_binary_sfnt import _patch_cff_table_in_sfnt

    class NoCFFFont:
        def __contains__(self, key):
            return False

    assert _patch_cff_table_in_sfnt(NoCFFFont(), {}, None, None, "Identity") is False


def test_patch_cff_table_in_sfnt_invalid_hex_cid_skips(cid_keyed_sfnt_cff_path):
    """A non-hexadecimal CID key raises ValueError internally and is gracefully skipped."""
    from fontTools.ttLib import TTFont
    from pdftl.fonts.font_binary_sfnt import _patch_cff_table_in_sfnt

    tt = TTFont(cid_keyed_sfnt_cff_path)
    patched = _patch_cff_table_in_sfnt(
        tt, {"INVALID": 650.0}, differences=None, base_encoding=None, cid_to_gid_map="Identity"
    )
    assert patched is False


def test_patch_cff_table_in_sfnt_glyph_name_none_skips(cid_keyed_sfnt_cff_path):
    """If a valid GID cannot be mapped to a glyph name, it is skipped."""
    from fontTools.ttLib import TTFont
    from pdftl.fonts.font_binary_sfnt import _patch_cff_table_in_sfnt
    from unittest.mock import patch

    tt = TTFont(cid_keyed_sfnt_cff_path)
    with patch("pdftl.fonts.cff_binary_utils._glyph_name_for_gid", return_value=None):
        patched = _patch_cff_table_in_sfnt(
            tt, {"0001": 650.0}, differences=None, base_encoding=None, cid_to_gid_map="Identity"
        )
    assert patched is False


def test_squash_font_file_vectors_patches_cid_keyed_cff_table_via_fallback(
    cid_keyed_sfnt_cff_with_hmtx_path,
):
    """
    End-to-end regression guard for the gap identified after the CID-keyed
    sfnt/CFF patch fix landed: squash_font_file_vectors's own "no glyf
    table" fallback (in font_binary_sfnt._squash_internal) patches hmtx via
    _patch_internal and then re-opens the result to patch the CFF table's
    own charstring widths via _patch_cff_table_in_sfnt. Prior to the fix
    this session, that second step silently did nothing at all for a
    CID-keyed sfnt-wrapped CFF -- this confirms it now actually rewrites
    the CFF table's own width, not just hmtx, when reached through the
    public squash entry point rather than by calling
    _patch_cff_table_in_sfnt directly.
    """
    from fontTools.ttLib import TTFont
    from fontTools.pens.basePen import NullPen
    from pdftl.fonts.font_binary_utils import squash_font_file_vectors

    squashed = squash_font_file_vectors(
        cid_keyed_sfnt_cff_with_hmtx_path, {"0001": 650.0}, cid_to_gid_map="Identity"
    )
    assert squashed is not None

    tt = TTFont(BytesIO(squashed))

    # hmtx side: the metrics-only patch always runs first.
    glyph_order = tt.getGlyphOrder()
    gname = glyph_order[1]  # Identity: CID 1 -> GID 1
    raw_w, _ = tt["hmtx"][gname]
    scale = 1000.0 / tt["head"].unitsPerEm
    assert raw_w * scale == pytest.approx(650.0)

    # CFF table side: the charstring's own width must also be rewritten,
    # not just hmtx -- this is exactly the gap that was previously open.
    cff = tt["CFF "].cff
    topdict = cff[cff.fontNames[0]]
    charstring = topdict.CharStrings["cid00001"]
    charstring.decompile()
    charstring.draw(NullPen())
    assert charstring.width == 650.0


def test_squash_font_file_vectors_cid_keyed_cff_no_match_returns_none(
    cid_keyed_sfnt_cff_with_hmtx_path,
):
    """A CID with no corresponding pdf_widths entry patches nothing at
    either layer, and the public entry point returns None."""
    from pdftl.fonts.font_binary_utils import squash_font_file_vectors

    result = squash_font_file_vectors(
        cid_keyed_sfnt_cff_with_hmtx_path, {"FFFF": 1.0}, cid_to_gid_map="Identity"
    )
    assert result is None


def _wrap_cff_in_minimal_sfnt_with_hmtx(cff_bytes: bytes, widths: dict[str, int]) -> bytes:
    """
    Wraps a bare CID-keyed CFF byte stream in a minimal OpenType/CFF sfnt
    container that additionally carries real `maxp` and `hmtx` tables,
    unlike this test file's existing `_wrap_cff_in_minimal_sfnt` helper.

    The existing helper is sufficient for tests that call
    `_patch_cff_table_in_sfnt` directly, but `squash_font_file_vectors`'s
    public entry point routes through `_patch_internal` first (to patch
    `hmtx`) before ever reaching the CFF-table patch -- and `_get_initial_data`
    unconditionally reads `tt["hmtx"]`, which raises `KeyError` without a
    real hmtx table present. `hhea` is deliberately omitted: verified
    directly against the installed fontTools source
    (`_h_m_t_x.table__h_m_t_x.compile`), an absent `hhea`/header table
    degrades to `ttFont["maxp"].numGlyphs` for `numberOfMetrics` rather
    than raising, so a CFF-style `maxp` (tableVersion 0x00005000, which
    only requires `numGlyphs` -- auto-computed in
    `_m_a_x_p.table__m_a_x_p.compile` from the glyph order) is the only
    other table hmtx's own compile step actually depends on.

    `widths` maps glyph name -> integer advance width (side bearing fixed
    at 0, which is unused by anything under test here).
    """
    from fontTools.ttLib import TTFont, newTable
    from fontTools.cffLib import CFFFontSet

    cff_font_set = CFFFontSet()
    cff_font_set.decompile(BytesIO(cff_bytes), otFont=None)
    topdict = cff_font_set[cff_font_set.fontNames[0]]
    glyph_order = list(topdict.charset)

    tt = TTFont(sfntVersion="OTTO")
    tt.setGlyphOrder(glyph_order)
    # Avoids _h_e_a_d.compile()'s recalc branch, which otherwise reads
    # xMin/yMin/xMax/yMax off the CFF Top DICT's own FontBBox -- a field
    # this minimal fixture's Top DICT never sets. This is a fixture
    # simplification (a real font's bbox is meaningful), not something
    # anything under test here reads.
    tt.recalcBBoxes = False

    head = newTable("head")
    head.tableVersion = 1.0
    head.fontRevision = 1.0
    head.checkSumAdjustment = 0
    head.magicNumber = 0x5F0F3CF5
    head.flags = 0
    head.unitsPerEm = 1000
    head.created = 0
    head.modified = 0
    head.xMin = 0
    head.yMin = 0
    head.xMax = 0
    head.yMax = 0
    head.macStyle = 0
    head.lowestRecPPEM = 0
    head.fontDirectionHint = 0
    head.indexToLocFormat = 0
    head.glyphDataFormat = 0
    tt["head"] = head

    cmap = newTable("cmap")
    cmap.tableVersion = 0
    cmap.tables = []
    tt["cmap"] = cmap

    cff_table = newTable("CFF ")
    cff_table.cff = cff_font_set
    tt["CFF "] = cff_table

    maxp = newTable("maxp")
    maxp.tableVersion = 0x00005000
    tt["maxp"] = maxp

    hmtx = newTable("hmtx")
    hmtx.metrics = {name: (widths.get(name, 0), 0) for name in glyph_order}
    tt["hmtx"] = hmtx

    buf = BytesIO()
    tt.save(buf)
    return buf.getvalue()


@pytest.fixture
def cid_keyed_sfnt_cff_with_hmtx_path(tmp_path) -> Path:
    """
    A genuine OpenType/CFF sfnt file wrapping a CID-keyed CFF program,
    complete with a real hmtx table (unlike `cid_keyed_sfnt_cff_path`
    above) so the full `squash_font_file_vectors` pipeline -- which
    patches hmtx before ever touching the CFF table -- can actually run:
    CID 1 -> square (width 500), CID 2 -> triangle (width 300).
    """
    cff_bytes = build_cid_keyed_cff_bytes({1: SQUARE_500, 2: TRIANGLE_300})
    sfnt_bytes = _wrap_cff_in_minimal_sfnt_with_hmtx(cff_bytes, {"cid00001": 500, "cid00002": 300})
    path = tmp_path / "cid_keyed_hmtx.otf"
    path.write_bytes(sfnt_bytes)
    return path


# --- font_binary_sfnt.py gaps: _patch_cid_metrics's cff_native branch ---


class TestPatchCidMetricsCffNativeGuards:
    def test_no_cff_table_returns_false(self):
        from pdftl.fonts.font_binary_sfnt import _patch_cid_metrics

        class NoCFFFont:
            def __contains__(self, key):
                return False

            def getGlyphOrder(self):
                return []

        assert _patch_cid_metrics(NoCFFFont(), {}, {"0001": 500.0}, 1.0, "cff_native") is False

    def test_non_hex_cid_key_skipped(self, monkeypatch):
        from pdftl.fonts.font_binary_sfnt import _patch_cid_metrics
        import pdftl.fonts.cff_binary_utils as cbu

        topdict = type("TD", (), {})()
        cff = type("CFF", (), {"fontNames": ["F"], "__getitem__": lambda s, k: topdict})()

        class FakeTT:
            def __contains__(self, key):
                return key == "CFF "

            def __getitem__(self, key):
                return type("CFFTable", (), {"cff": cff})()

            def getGlyphOrder(self):
                return [".notdef", "A"]

        monkeypatch.setattr(cbu, "_resolve_cff_cid_to_gid", lambda td, cid: 1)
        result = _patch_cid_metrics(FakeTT(), {"A": (250, 0)}, {"ZZZZ": 500.0}, 1.0, "cff_native")
        assert result is False

    def test_gid_none_skipped(self, monkeypatch):
        from pdftl.fonts.font_binary_sfnt import _patch_cid_metrics
        import pdftl.fonts.cff_binary_utils as cbu

        topdict = type("TD", (), {})()
        cff = type("CFF", (), {"fontNames": ["F"], "__getitem__": lambda s, k: topdict})()

        class FakeTT:
            def __contains__(self, key):
                return key == "CFF "

            def __getitem__(self, key):
                return type("CFFTable", (), {"cff": cff})()

            def getGlyphOrder(self):
                return [".notdef", "A"]

        monkeypatch.setattr(cbu, "_resolve_cff_cid_to_gid", lambda td, cid: None)
        result = _patch_cid_metrics(FakeTT(), {"A": (250, 0)}, {"0001": 500.0}, 1.0, "cff_native")
        assert result is False

    def test_gname_none_skipped(self, monkeypatch):
        from pdftl.fonts.font_binary_sfnt import _patch_cid_metrics
        import pdftl.fonts.cff_binary_utils as cbu

        topdict = type("TD", (), {})()
        cff = type("CFF", (), {"fontNames": ["F"], "__getitem__": lambda s, k: topdict})()

        class FakeTT:
            def __contains__(self, key):
                return key == "CFF "

            def __getitem__(self, key):
                return type("CFFTable", (), {"cff": cff})()

            def getGlyphOrder(self):
                return [".notdef"]  # gid 5 resolves to nothing

        monkeypatch.setattr(cbu, "_resolve_cff_cid_to_gid", lambda td, cid: 5)
        result = _patch_cid_metrics(FakeTT(), {}, {"0001": 500.0}, 1.0, "cff_native")
        assert result is False

    def test_glyph_missing_from_hmtx_skipped(self, monkeypatch):
        from pdftl.fonts.font_binary_sfnt import _patch_cid_metrics
        import pdftl.fonts.cff_binary_utils as cbu

        topdict = type("TD", (), {})()
        cff = type("CFF", (), {"fontNames": ["F"], "__getitem__": lambda s, k: topdict})()

        class FakeTT:
            def __contains__(self, key):
                return key == "CFF "

            def __getitem__(self, key):
                return type("CFFTable", (), {"cff": cff})()

            def getGlyphOrder(self):
                return [".notdef", "A"]

        monkeypatch.setattr(cbu, "_resolve_cff_cid_to_gid", lambda td, cid: 1)
        result = _patch_cid_metrics(
            FakeTT(),
            {},
            {"0001": 500.0},
            1.0,
            "cff_native",  # hmtx empty -> KeyError
        )
        assert result is False
