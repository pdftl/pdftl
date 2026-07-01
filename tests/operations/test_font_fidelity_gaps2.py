# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/test_font_fidelity_gaps2.py

"""
Follow-up TDD tests for fidelity gaps identified in review after
test_font_fidelity_gaps.py / test_font_fidelity_matrix.py were closed, but
not yet covered by any test:

  1. `squash_font_vectors` mode on an sfnt-wrapped CFF font (PDF
     /FontFile3 /Subtype /OpenType containing a `CFF ` table, not
     `glyf`) has no metrics-only fallback. squash_font_file_vectors's own
     docstring documents this fallback for `bare_cff`/`type1` (formats
     detectable from `embedded_format` alone), but an sfnt-wrapped CFF is
     classified "sfnt" and dispatched straight to
     `_squash_font_file_vectors_via_ttfont`, whose `"glyf" not in tt`
     guard (in font_binary_sfnt.py) returns None with no equivalent
     fallback -- so the requested edit is silently dropped entirely,
     unlike the bare-CFF case. THIS TEST IS EXPECTED TO FAIL until that
     fallback is added; it documents the gap rather than a passing
     guarantee.

  2. Type 1 (`/FontFile`) width read/patch arithmetic
     (`_find_width_operator`, `_read_charstring_width`,
     `_patch_single_type1_width` in type1_binary_utils.py) has, until
     now, only been exercised via kwargs-capture/monkeypatch at the
     dispatch-threading level (test_font_fidelity_matrix.py's
     TestEmbeddedFormatThreadingAcrossNonSfntFormats) -- never against a
     real Type 1 charstring's actual bytecode. A full on-disk Type 1 file
     fixture is a known, documented blocker (see
     font_fixture_builder.py's module docstring); this closes the more
     important, narrower gap instead -- whether the `hsbw`/`sbw` operand
     offset arithmetic itself is correct -- by constructing genuine
     fontTools.misc.psCharStrings.T1CharString objects directly from
     hand-encoded Type 1 charstring bytecode (per the Adobe Type 1 Font
     Format spec, Chapter 8), bypassing only the outer eexec/PFB file
     layer, which is unrelated to the width-arithmetic risk this closes.

  3. A symbolic Simple TrueType font -- no /Differences, no
     /BaseEncoding, relying entirely on the font's own built-in cmap
     (common for icon/symbol fonts, ISO 32000-2 9.6.6.2's "no Encoding
     entry" case) -- has not been exercised by any fidelity test so far;
     every existing Simple-font fixture uses /Differences.

CAVEAT on (1): written without a live pdftl/fontTools environment to
execute against; syntax-checked only. The exact shape FontBuilder.setupCFF
expects for `charStringsDict` values is reconstructed from memory of the
fontTools API (see font_fixture_builder.py's build_opentype_cff_bytes
docstring) and not verified by running it -- if that call needs
adjustment, it's the most likely place, not the assertions built on it.
(2) and (3) construct fontTools objects directly (no file I/O, no
FontBuilder), which is a narrower and more mechanically verifiable
surface, but is likewise not confirmed by actually running it here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pikepdf
import pytest

from pdftl.operations.export_import_fonts import (
    export_fonts,
    export_fonts_cli_hook,
    import_fonts,
)

sys.path.insert(0, str(Path(__file__).parent.parent / "fonts" / "fixtures"))
from font_fixture_builder import (  # noqa: E402
    SQUARE_500,
    TRIANGLE_300,
    build_truetype_bytes,
)


def _export(pdf, export_dir) -> None:
    """See test_font_fidelity_gaps.py's _export() -- export_fonts() alone
    does not write manifest.json; only export_fonts_cli_hook does."""
    res = export_fonts(pdf, [str(export_dir)])
    assert res.success
    export_fonts_cli_hook(res, None, None)


# ---------------------------------------------------------------------------
# Gap 1: squash_font_vectors on an sfnt-wrapped CFF (OpenType/CFF) has no
# metrics-only fallback, unlike bare_cff/type1.
# ---------------------------------------------------------------------------


def _make_pdf_with_opentype_cff_font(otf_bytes: bytes):
    """A Simple font (/TrueType, per how PDF producers commonly wrap an
    OpenType/CFF program for a non-CID Simple font) whose /FontFile3
    /Subtype /OpenType program has glyphs 'A' (width 500) and 'B' (width
    300), reachable via /Differences at codes 0x41/0x42."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page()

    stream = pdf.make_stream(otf_bytes)
    stream.Subtype = pikepdf.Name("/OpenType")

    descriptor = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name("/TestOTFCFF"),
                "/Flags": 32,
                "/FontFile3": stream,
            }
        )
    )

    font_obj = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/TrueType"),
                "/BaseFont": pikepdf.Name("/TestOTFCFF"),
                "/FirstChar": 0x41,
                "/LastChar": 0x42,
                "/Widths": pikepdf.Array([500.0, 300.0]),
                "/FontDescriptor": descriptor,
                "/Encoding": pikepdf.Dictionary(
                    {"/Differences": pikepdf.Array([0x41, pikepdf.Name("/A"), pikepdf.Name("/B")])}
                ),
            }
        )
    )
    page.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font_obj})})
    return pdf, font_obj


class TestOpenTypeCffSquashHasNoMetricsFallback:
    def test_squash_mode_at_least_patches_metrics_for_opentype_cff(self, tmp_path):
        """
        squash_font_vectors mode cannot rescale an OpenType/CFF outline
        (no `glyf` table), exactly like bare CFF -- but unlike bare CFF,
        there's currently no metrics-only fallback for this specific
        sfnt-classified-but-CFF-flavored case, so the requested width
        edit is silently dropped and the embedded font binary is left
        completely untouched.

        EXPECTED TO CURRENTLY FAIL: this documents the gap. See
        squash_font_file_vectors's docstring in font_binary_utils.py --
        the "sfnt" dispatch bucket funnels straight through to
        _squash_font_file_vectors_via_ttfont with no equivalent to the
        `bare_cff`/`type1` metrics-only degrade path.
        """
        from font_fixture_builder import build_opentype_cff_bytes

        otf_bytes = build_opentype_cff_bytes({"A": SQUARE_500, "B": TRIANGLE_300})
        pdf, font_obj = _make_pdf_with_opentype_cff_font(otf_bytes)

        export_dir = tmp_path / "fonts"
        _export(pdf, export_dir)

        sidecar_files = list(export_dir.glob("font_*.json"))
        assert len(sidecar_files) == 1
        sidecar = json.loads(sidecar_files[0].read_text())
        sidecar["width_sync_mode"] = "squash_font_vectors"
        assert "41" in sidecar["mappings"], sidecar["mappings"].keys()
        sidecar["mappings"]["41"]["width"]["pdf"] = 900.0
        sidecar_files[0].write_text(json.dumps(sidecar))

        res_import = import_fonts(pdf, [str(export_dir)])
        assert res_import.success
        assert float(font_obj.Widths[0]) == 900.0  # /Widths itself is fine either way

        from fontTools.ttLib import TTFont

        descriptor = font_obj.FontDescriptor
        patched_bytes = descriptor.FontFile3.read_bytes()
        reread_path = tmp_path / "reread.otf"
        reread_path.write_bytes(patched_bytes)

        tt = TTFont(reread_path)
        cff_table = tt["CFF "].cff
        top_dict = cff_table[cff_table.fontNames[0]]
        charstring = top_dict.CharStrings["A"]
        charstring.decompile()
        from fontTools.pens.basePen import NullPen

        charstring.draw(NullPen())

        assert charstring.width == 900.0, (
            "squash_font_vectors mode did not patch the embedded OpenType/CFF "
            "binary's own width at all -- unlike bare CFF and Type 1, this "
            "sfnt-classified-but-glyf-less font shape has no metrics-only "
            "fallback, so the requested edit was silently dropped entirely"
        )


# ---------------------------------------------------------------------------
# Gap 2: Type 1 hsbw/sbw width read/patch arithmetic, against genuine
# T1CharString bytecode built directly from the Adobe Type 1 spec's own
# number/operator encoding (Chapter 8) -- not file I/O, not a mock.
# ---------------------------------------------------------------------------


def _encode_t1_number(value: int) -> bytes:
    """
    Encodes an integer using the Adobe Type 1 Font Format's charstring
    number encoding (Chapter 8, "Charstring Number Encoding") -- the same
    3-range scheme CFF/Type 2 charstrings use for their small-integer
    cases, minus Type 2's extra 32-bit-fixed variant. Only the ranges
    actually needed by the hsbw/sbw test glyphs below are implemented.
    """
    if -107 <= value <= 107:
        return bytes([value + 139])
    if 108 <= value <= 1131:
        value -= 108
        return bytes([(value >> 8) + 247, value & 0xFF])
    if -1131 <= value <= -108:
        value = -value - 108
        return bytes([(value >> 8) + 251, value & 0xFF])
    raise ValueError(f"value {value} outside the ranges this test helper implements")


# Type 1 charstring operator numbers (Adobe Type 1 Font Format, Appendix B).
_OP_HSBW = 13
_OP_ENDCHAR = 14
_OP_SBW = 12, 7  # two-byte operator: escape (12) followed by 7


def _build_hsbw_charstring_bytes(sbx: int, wx: int) -> bytes:
    """`sbx wx hsbw endchar` -- the single-sidebearing width form."""
    return (
        _encode_t1_number(sbx) + _encode_t1_number(wx) + bytes([_OP_HSBW]) + bytes([_OP_ENDCHAR])
    )


def _build_sbw_charstring_bytes(sbx: int, sby: int, wx: int, wy: int) -> bytes:
    """`sbx sby wx wy sbw endchar` -- the two-sidebearing width form."""
    return (
        _encode_t1_number(sbx)
        + _encode_t1_number(sby)
        + _encode_t1_number(wx)
        + _encode_t1_number(wy)
        + bytes(_OP_SBW)
        + bytes([_OP_ENDCHAR])
    )


def _make_t1_charstring(program_bytes: bytes):
    """
    Builds a genuine, real fontTools.misc.psCharStrings.T1CharString
    directly from raw (unencrypted -- eexec is a file-layer concern
    orthogonal to charstring operand/operator structure) Type 1 charstring
    bytecode, bypassing T1Font/file parsing entirely. This is the same
    class type1_binary_utils.py's `_read_charstring_width` and
    `_patch_single_type1_width` operate on in production; only the outer
    "read this from an eexec-encrypted, PFB-or-plain on-disk font" layer
    is skipped, which is unrelated to the width-arithmetic correctness
    these tests check.
    """
    from fontTools.misc.psCharStrings import T1CharString

    return T1CharString(program_bytes)


class TestType1WidthArithmeticAgainstRealCharstringBytecode:
    def test_hsbw_width_read_round_trips(self):
        """hsbw (single-sidebearing) form: width operand sits 1 slot
        before the operator -- _WIDTH_OPERAND_OFFSET["hsbw"] == 1."""
        from pdftl.fonts.type1_binary_utils import _read_charstring_width

        charstring = _make_t1_charstring(_build_hsbw_charstring_bytes(sbx=0, wx=500))
        assert _read_charstring_width(charstring) == 500

    def test_sbw_width_read_round_trips(self):
        """sbw (two-sidebearing) form: width operand (wx) sits 2 slots
        before the operator -- _WIDTH_OPERAND_OFFSET["sbw"] == 2. This is
        the case cff_binary_utils-style pen/draw-based width extraction
        cannot reach at all (see type1_binary_utils.py's module
        docstring on fontTools' own op_sbw discarding operands), so this
        test is the only coverage of the sbw arithmetic path existing
        anywhere in this codebase."""
        from pdftl.fonts.type1_binary_utils import _read_charstring_width

        charstring = _make_t1_charstring(_build_sbw_charstring_bytes(sbx=0, sby=0, wx=300, wy=0))
        assert _read_charstring_width(charstring) == 300

    def test_hsbw_width_patch_round_trips(self):
        """
        Patches a charstring's width in place and confirms BOTH that
        `.width` reflects the new value AND that the underlying
        `.program` operand was actually rewritten (not just the
        convenience attribute) -- re-decompiling a freshly recompiled
        charstring and reading it back via _read_charstring_width closes
        the loop through actual bytecode, not just in-memory state.
        """
        from pdftl.fonts.type1_binary_utils import (
            _patch_single_type1_width,
            _read_charstring_width,
        )

        charstring = _make_t1_charstring(_build_hsbw_charstring_bytes(sbx=0, wx=500))
        assert _patch_single_type1_width(charstring, 650)

        recompiled_bytes = charstring.bytecode
        reread = _make_t1_charstring(recompiled_bytes)
        assert _read_charstring_width(reread) == 650, (
            "patched hsbw width did not survive a recompile/redecompile "
            "round trip through real charstring bytecode"
        )

    def test_sbw_width_patch_round_trips(self):
        """Same as above, for the sbw (two-sidebearing) operand-offset
        arithmetic path specifically."""
        from pdftl.fonts.type1_binary_utils import (
            _patch_single_type1_width,
            _read_charstring_width,
        )

        charstring = _make_t1_charstring(_build_sbw_charstring_bytes(sbx=0, sby=0, wx=300, wy=0))
        assert _patch_single_type1_width(charstring, 950)

        recompiled_bytes = charstring.bytecode
        reread = _make_t1_charstring(recompiled_bytes)
        assert _read_charstring_width(reread) == 950, (
            "patched sbw width did not survive a recompile/redecompile "
            "round trip through real charstring bytecode"
        )

    def test_patch_does_not_disturb_sidebearing_operand(self):
        """
        A regression guard specifically for the offset arithmetic: patching
        wx must not accidentally overwrite sbx (hsbw's other operand, one
        slot further back) -- an off-by-one in
        `_WIDTH_OPERAND_OFFSET`/`width_index` would silently corrupt the
        left sidebearing instead of (or as well as) the width.
        """
        from pdftl.fonts.type1_binary_utils import _patch_single_type1_width

        charstring = _make_t1_charstring(_build_hsbw_charstring_bytes(sbx=42, wx=500))
        assert _patch_single_type1_width(charstring, 777)

        # _patch_single_type1_width's own charstring.compile() clears
        # `.program` back to None once `.bytecode` is populated (the same
        # decompile/compile toggle test_hsbw_width_patch_round_trips
        # already routes around) -- so inspecting the operand stream
        # means re-decompiling from the freshly recompiled bytecode
        # rather than reading `.program` off the same object.
        reread = _make_t1_charstring(charstring.bytecode)
        reread.decompile()

        index, op = None, None
        for i, token in enumerate(reread.program):
            if token == "hsbw":
                index, op = i, token
        assert op == "hsbw"
        # sbx is 2 slots before the operator now (sbx, wx, hsbw); confirm
        # it's untouched.
        assert reread.program[index - 2] == 42, (
            "patching wx corrupted the sbx operand -- off-by-one in the "
            "hsbw operand-offset arithmetic"
        )


# ---------------------------------------------------------------------------
# Gap 3: symbolic Simple TrueType font, no /Differences, no /BaseEncoding
# -- resolution relies entirely on the font's own built-in cmap.
# ---------------------------------------------------------------------------


def _make_pdf_with_symbolic_truetype_font(ttf_bytes: bytes, cmap_table: dict[int, str]):
    """
    A Simple TrueType font with NO /Encoding entry at all (ISO 32000-2
    9.6.6.2: "if the font program's built-in encoding is to be used ...
    omit the Encoding entry"), Flags carrying the Symbolic bit (bit 3,
    value 4) set and Nonsymbolic (bit 6, value 32) clear -- matching a
    real icon/symbol font, and distinct from every other Simple-font
    fixture in this test suite so far, which all carry /Differences.

    `cmap_table` (code -> glyph name) is embedded directly into the TTF's
    own `cmap` table via fontTools, so PDF-code -> glyph-name resolution
    is exercised through the font's own built-in encoding.
    """
    from fontTools.ttLib import TTFont

    pdf = pikepdf.new()
    page = pdf.add_blank_page()

    tt = TTFont(__import__("io").BytesIO(ttf_bytes))
    tt.importXML  # no-op reference to keep TTFont import used defensively
    from fontTools.ttLib.tables._c_m_a_p import CmapSubtable

    cmap_subtable = CmapSubtable.getSubtableClass(4)(4)
    cmap_subtable.platformID = 3
    cmap_subtable.platEncID = 0  # symbol encoding, matching a Flags-symbolic font
    cmap_subtable.language = 0
    cmap_subtable.cmap = dict(cmap_table)

    from fontTools.ttLib.tables._c_m_a_p import table__c_m_a_p

    cmap = table__c_m_a_p()
    cmap.tableVersion = 0
    cmap.tables = [cmap_subtable]
    tt["cmap"] = cmap

    buf = __import__("io").BytesIO()
    tt.save(buf)
    patched_ttf_bytes = buf.getvalue()

    stream = pdf.make_stream(patched_ttf_bytes)

    descriptor = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name("/TestSymbolFont"),
                # Symbolic (bit 3, value 4) set; Nonsymbolic (bit 6, value
                # 32) clear -- ISO 32000-2 Table 123/9.6.6.2.
                "/Flags": 4,
                "/FontFile2": stream,
            }
        )
    )

    font_obj = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/TrueType"),
                "/BaseFont": pikepdf.Name("/TestSymbolFont"),
                "/FirstChar": 0x41,
                "/LastChar": 0x42,
                "/Widths": pikepdf.Array([500.0, 300.0]),
                "/FontDescriptor": descriptor,
                # Deliberately NO /Encoding entry at all.
            }
        )
    )
    page.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font_obj})})
    return pdf, font_obj, patched_ttf_bytes


class TestSymbolicTrueTypeFontNoDifferencesNoBaseEncoding:
    def test_auto_mode_picks_up_binary_only_edit_via_builtin_cmap(self, tmp_path):
        """
        With no /Differences and no /BaseEncoding at all, width sync must
        fall back entirely to the font's own built-in cmap to resolve PDF
        codes to glyphs -- exercising a code path every other fixture in
        this suite bypasses by always supplying /Differences. Simulates
        an external edit to the embedded binary changing glyph A's
        (code 0x41's, per the symbol cmap below) width, and confirms
        'auto' mode picks it up via the font's own cmap alone.
        """
        ttf_bytes = build_truetype_bytes({"A": SQUARE_500, "B": TRIANGLE_300})
        # Symbol-encoded cmap: PUA-style codes matching the (3, 0) symbol
        # convention (0xF000 + code) some real icon fonts use, resolved
        # here as a direct code->glyph mapping via the (3,0) subtable.
        cmap_table = {0xF041: "A", 0xF042: "B"}
        pdf, font_obj, _ = _make_pdf_with_symbolic_truetype_font(ttf_bytes, cmap_table)

        export_dir = tmp_path / "fonts"
        _export(pdf, export_dir)

        ttf_files = list(export_dir.glob("font_*.ttf"))
        assert len(ttf_files) == 1

        # Simulate an external editor changing glyph "A"'s width to 650,
        # keeping the same font-program shape (glyph order/cmap) intact.
        edited_bytes = build_truetype_bytes({"A": (650, SQUARE_500[1]), "B": TRIANGLE_300})
        _, _, patched_edited_bytes = _make_pdf_with_symbolic_truetype_font(
            edited_bytes, cmap_table
        )
        # _make_pdf_with_symbolic_truetype_font returns a *new* PDF too,
        # which we don't need here -- only the re-cmap'd TTF bytes it
        # produced as a side effect.
        ttf_files[0].write_bytes(patched_edited_bytes)

        res_import = import_fonts(pdf, [str(export_dir)])
        assert res_import.success

        assert float(font_obj.Widths[0]) == 650.0, (
            "auto width sync did not pick up the binary-only edit for a "
            "symbolic Simple TrueType font with no /Differences and no "
            "/BaseEncoding -- resolution via the font's own built-in cmap "
            "alone is a code path no other fixture in this suite exercises"
        )


# ---------------------------------------------------------------------------
# Gap 4: patch_font_metrics/squash_font_vectors modes don't un-offset a
# symbolic (3, 0) cmap's codes before matching against pdf_widths -- the
# auto-mode read path (_get_best_cmap + _effective_cmap_code) was fixed
# for this, but the write paths (_patch_internal/_squash_internal, which
# still call cmap-keyed helpers with the raw, offset code) were not.
# ---------------------------------------------------------------------------


class TestSymbolicCmapOffsetInPatchMode:
    def test_patch_font_metrics_mode_resolves_symbol_cmap_offset(self, tmp_path):
        """
        A manual width.pdf edit (patch_font_metrics mode) for a symbolic
        Simple TrueType font with no /Differences and no /BaseEncoding
        must resolve PDF code 0x41 against the font's own (3, 0) symbol
        cmap, which stores it as 0xF041 (ISO 32000-2 9.6.6.4). If the raw,
        offset cmap code is used directly as the hex key to match against
        pdf_widths (keyed by plain PDF code, e.g. "41"), it never matches
        "F041", and the requested edit is silently dropped -- the same
        failure mode test_auto_mode_picks_up_binary_only_edit_via_builtin_cmap
        closed for the read path, but here for the write path instead.
        """
        ttf_bytes = build_truetype_bytes({"A": SQUARE_500, "B": TRIANGLE_300})
        cmap_table = {0xF041: "A", 0xF042: "B"}
        pdf, font_obj, _ = _make_pdf_with_symbolic_truetype_font(ttf_bytes, cmap_table)

        export_dir = tmp_path / "fonts"
        _export(pdf, export_dir)

        sidecar_files = list(export_dir.glob("font_*.json"))
        assert len(sidecar_files) == 1
        sidecar = json.loads(sidecar_files[0].read_text())
        sidecar["width_sync_mode"] = "patch_font_metrics"
        assert "41" in sidecar["mappings"], sidecar["mappings"].keys()
        sidecar["mappings"]["41"]["width"]["pdf"] = 777.0
        sidecar_files[0].write_text(json.dumps(sidecar))

        res_import = import_fonts(pdf, [str(export_dir)])
        assert res_import.success

        from fontTools.ttLib import TTFont

        patched_bytes = font_obj.FontDescriptor.FontFile2.read_bytes()
        reread_path = tmp_path / "reread.ttf"
        reread_path.write_bytes(patched_bytes)
        tt = TTFont(reread_path)
        raw_w, _ = tt["hmtx"]["A"]
        scale = 1000.0 / tt["head"].unitsPerEm

        assert raw_w * scale == pytest.approx(777.0), (
            "patch_font_metrics mode did not patch the embedded font's "
            "own hmtx width for a symbolic font's (3, 0) cmap-resolved "
            "glyph -- the raw, offset cmap code (0xF041) is not being "
            "un-offset before matching against pdf_widths' plain-code keys"
        )

    def test_squash_font_vectors_mode_resolves_symbol_cmap_offset(self, tmp_path):
        """Same gap, for squash_font_vectors mode's _process_glyph_squash."""
        ttf_bytes = build_truetype_bytes({"A": SQUARE_500, "B": TRIANGLE_300})
        cmap_table = {0xF041: "A", 0xF042: "B"}
        pdf, font_obj, _ = _make_pdf_with_symbolic_truetype_font(ttf_bytes, cmap_table)

        export_dir = tmp_path / "fonts"
        _export(pdf, export_dir)

        sidecar_files = list(export_dir.glob("font_*.json"))
        assert len(sidecar_files) == 1
        sidecar = json.loads(sidecar_files[0].read_text())
        sidecar["width_sync_mode"] = "squash_font_vectors"
        assert "42" in sidecar["mappings"], sidecar["mappings"].keys()
        sidecar["mappings"]["42"]["width"]["pdf"] = 950.0
        sidecar_files[0].write_text(json.dumps(sidecar))

        res_import = import_fonts(pdf, [str(export_dir)])
        assert res_import.success

        from fontTools.ttLib import TTFont

        patched_bytes = font_obj.FontDescriptor.FontFile2.read_bytes()
        reread_path = tmp_path / "reread_squash.ttf"
        reread_path.write_bytes(patched_bytes)
        tt = TTFont(reread_path)
        raw_w, _ = tt["hmtx"]["B"]
        scale = 1000.0 / tt["head"].unitsPerEm

        assert raw_w * scale == pytest.approx(950.0), (
            "squash_font_vectors mode did not patch the embedded font's "
            "own hmtx width for a symbolic font's (3, 0) cmap-resolved "
            "glyph -- same raw-offset-code matching gap as patch_font_metrics"
        )
