# tests/fonts/test_type1_to_cff.py

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
Real (non-mocked) tests for pdftl.fonts.type1_to_cff, driven against
genuine Type 1 font programs built via
tests/fonts/fixtures/type1_fixture_builder.py's real eexec-encrypting
writer.

Written test-first: at the time this file was added, subset_fonts's Type
1 handling parsed the eexec-decrypted section as `str` under
encoding="ascii", which reliably raises UnicodeDecodeError against any
real font (eexec-encrypted bytes are essentially uniformly distributed
over 0-255, so a byte >= 0x80 appears in virtually every real charstring
section). test_regression_ascii_decode_bug below pins that failure mode
directly; every other test exercises the module's actual intended
behavior.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from type1_fixture_builder import build_type1_bytes  # noqa: E402

from pdftl.fonts.type1_to_cff import (
    build_cff_from_glyph_names,
    open_type1_font_bytes,
    type1_to_cff,
)


def _glyph_names_in_cff(cff_bytes: bytes) -> set[str]:
    """Round-trips subsetted CFF bytes back through the same bare-CFF
    sfnt-shell mechanism font_subsetting.py itself uses, to read back
    which glyph names actually survived."""
    from pdftl.fonts.font_subsetting import wrap_bare_cff_in_sfnt

    tt = wrap_bare_cff_in_sfnt(cff_bytes)
    return set(tt.getGlyphOrder())


@pytest.fixture
def three_glyph_type1_bytes() -> bytes:
    """A genuine Type 1 program with three glyphs, each with a charstring
    complex enough that its eexec-encrypted bytes are all but certain to
    contain a byte >= 0x80 (see module docstring)."""
    return build_type1_bytes(
        {
            ".notdef": (0, ["endchar"]),
            "A": (500, [0, 0, "rmoveto", 500, 0, "rlineto", 500, 500, "rlineto"]),
            "B": (300, [0, 0, "rmoveto", 300, 0, "rlineto"]),
            "C": (600, [0, 0, "rmoveto", 600, 600, "rlineto"]),
        }
    )


class TestType1ToCffRealFont:
    def test_regression_ascii_decode_bug(self, three_glyph_type1_bytes):
        """Pins the original bug: converting a real Type 1 program must
        not raise UnicodeDecodeError (or anything else) -- it must return
        subsetted bytes."""
        result = type1_to_cff(three_glyph_type1_bytes, codes={65})  # 'A' in StandardEncoding
        assert result is not None
        assert isinstance(result, bytes)

    def test_keeps_only_requested_glyph_plus_notdef(self, three_glyph_type1_bytes):
        # StandardEncoding code 65 = 'A', 66 = 'B'
        result = type1_to_cff(three_glyph_type1_bytes, codes={65})
        assert result is not None
        names = _glyph_names_in_cff(result)
        assert "A" in names
        assert ".notdef" in names
        assert "B" not in names
        assert "C" not in names

    def test_keeps_union_of_multiple_codes(self, three_glyph_type1_bytes):
        result = type1_to_cff(three_glyph_type1_bytes, codes={65, 66})
        assert result is not None
        names = _glyph_names_in_cff(result)
        assert {"A", "B", ".notdef"} <= names
        assert "C" not in names

    def test_result_is_valid_parseable_cff(self, three_glyph_type1_bytes):
        result = type1_to_cff(three_glyph_type1_bytes, codes={65})
        assert result is not None
        from pdftl.fonts.font_subsetting import wrap_bare_cff_in_sfnt

        tt = wrap_bare_cff_in_sfnt(result)
        assert isinstance(tt, TTFont)
        assert "CFF " in tt

    def test_no_matching_codes_still_keeps_notdef(self, three_glyph_type1_bytes):
        result = type1_to_cff(three_glyph_type1_bytes, codes={999})
        # Nothing beyond .notdef survived; module treats this as "not
        # worth writing back" rather than emitting a near-empty font.
        assert result is None

    def test_malformed_bytes_returns_none(self):
        result = type1_to_cff(b"this is not a Type 1 font at all", codes={65})
        assert result is None

    def test_empty_codes_returns_none(self, three_glyph_type1_bytes):
        result = type1_to_cff(three_glyph_type1_bytes, codes=set())
        assert result is None

    def test_resolves_via_differences_override(self, three_glyph_type1_bytes):
        """A /Differences entry mapping some code to glyph 'C' should be
        honored over StandardEncoding, matching the Simple-font
        convention used elsewhere (font_subsetting.gids_for_simple_font_via_encoding)."""
        result = type1_to_cff(
            three_glyph_type1_bytes,
            codes={200},
            differences=[200, "/C"],
        )
        assert result is not None
        names = _glyph_names_in_cff(result)
        assert "C" in names
        assert "A" not in names
        assert "B" not in names


class TestType1ToCffMissingTrailer:
    """
    Regression tests for a second, independent bug found only once the
    module was run against a real-world PDF: many real PDF producers
    omit the optional 512-zeros-plus-cleartomark trailer a Type 1
    program's eexec section conventionally ends with (PDF 32000-1 Table
    111 makes /Length3 -- and therefore this trailer -- optional).
    fontTools.t1Lib requires that trailer to recognize the end of the
    eexec section at all, so without the retry-with-synthesized-trailer
    fallback, every font missing it fails to parse outright.
    """

    def test_converts_font_with_no_trailer_at_all(self, three_glyph_type1_bytes):
        # Simulates a PDF /FontFile stream with /Length3 0: the trailing
        # zero-padding-plus-cleartomark trailer is entirely absent, but
        # the eexec-encrypted payload itself is intact (matching the
        # real-world PDF this was found against, where /Length1 +
        # /Length2 always covers the real content and only the optional
        # /Length3 trailer is dropped).
        cleartomark_idx = three_glyph_type1_bytes.find(b"cleartomark")
        assert cleartomark_idx != -1
        no_trailer = three_glyph_type1_bytes[:cleartomark_idx]

        result = type1_to_cff(no_trailer, codes={65})
        assert result is not None
        names = _glyph_names_in_cff(result)
        assert "A" in names

    def test_still_fails_cleanly_on_genuinely_corrupt_data(self):
        # A missing trailer should be recovered from, but genuinely
        # corrupt/non-Type-1 data must still return None rather than
        # loop or raise.
        result = type1_to_cff(b"not a font, missing trailer or otherwise", codes={65})
        assert result is None

    def test_non_ascii_byte_in_cleartext_header(self, three_glyph_type1_bytes):
        """Regression: fontTools.t1Lib.T1Font defaults to encoding="ascii"
        for its cleartext-header parsing, which raises UnicodeDecodeError
        on any font whose header comments contain a byte >= 0x80 -- a
        copyright symbol ('\\xa9' in latin-1) in a comment being the most
        common real-world trigger. Found running against a real
        ArialNarrow font in production, independent of (and downstream
        of) the earlier eexec-body ascii bug this module was first
        written to fix."""
        header_end = three_glyph_type1_bytes.index(b"currentfile eexec")
        with_copyright = (
            three_glyph_type1_bytes[:header_end]
            + b"% Copyright \xa9 Example Foundry\n"
            + three_glyph_type1_bytes[header_end:]
        )

        result = type1_to_cff(with_copyright, codes={65})
        assert result is not None
        names = _glyph_names_in_cff(result)
        assert "A" in names


class TestType1ToCffWidths:
    """
    Regression tests for a serious bug found visually inspecting the
    rendered output of a converted PDF: every glyph's advance width came
    out as 0. T2CharStringPen only bakes a width into a charstring's
    compiled program if it's passed to the pen's *constructor* before
    getCharString() is called; setting `.width` on the T2CharString
    object returned by getCharString() afterwards has no effect on the
    already-finalized program bytes. A zero advance width makes every
    glyph on a line stack on top of the next one -- exactly the
    "characters on top of each other" symptom this was found from.
    """

    def test_glyph_widths_are_preserved(self):
        """Each surviving glyph's width in the converted CFF must match
        its original Type 1 width, not silently come out as 0."""
        source = build_type1_bytes(
            {
                ".notdef": (0, ["endchar"]),
                "A": (691, [0, 0, "rmoveto", 691, 0, "rlineto"]),
                "B": (588, [0, 0, "rmoveto", 588, 0, "rlineto"]),
            }
        )
        result = type1_to_cff(source, codes={65, 66})
        assert result is not None

        from pdftl.fonts.font_subsetting import wrap_bare_cff_in_sfnt

        tt = wrap_bare_cff_in_sfnt(result)
        cff = tt["CFF "].cff
        charstrings = cff[cff.fontNames[0]].CharStrings

        for name, expected_width in (("A", 691), ("B", 588)):
            cs = charstrings[name]
            cs.decompile()
            assert cs.program and isinstance(cs.program[0], (int, float)), (
                f"glyph {name!r} has no leading width value in its charstring program"
            )
            assert cs.program[0] == expected_width, (
                f"glyph {name!r}: expected width {expected_width}, got {cs.program[0]} "
                "(0 means the width silently failed to embed)"
            )

    def test_widths_differ_per_glyph_not_all_zero(self, three_glyph_type1_bytes):
        """A broader guard against the same bug re-appearing: glyphs with
        genuinely different source widths (500/300/600 in the fixture)
        must come out with genuinely different, non-zero widths -- not
        all collapsed to the same value (e.g. all 0)."""
        result = type1_to_cff(three_glyph_type1_bytes, codes={65, 66, 67})
        assert result is not None

        from pdftl.fonts.font_subsetting import wrap_bare_cff_in_sfnt

        tt = wrap_bare_cff_in_sfnt(result)
        cff = tt["CFF "].cff
        charstrings = cff[cff.fontNames[0]].CharStrings

        widths = {}
        for name in ("A", "B", "C"):
            cs = charstrings[name]
            cs.decompile()
            widths[name] = cs.program[0]

        assert 0 not in widths.values()
        assert len(set(widths.values())) == 3


class TestType1ToCffBuiltinEncodingPreservation:
    """
    Regression tests for a real bug found rendering a converted PDF: a
    math/symbol glyph reachable only via the Type 1 font's own built-in
    /Encoding (no /Differences override, matching a real STIXMath-Italic
    font in production where the /Font dict has no /Encoding entry at
    all) came out unreachable at its original code in the converted CFF.
    fontTools.fontBuilder.FontBuilder.setupCFF never sets an explicit
    Encoding, so the resulting CFF silently fell back to plain
    StandardEncoding -- which has no slot at all for a symbol glyph like
    a relational operator -- even though the glyph's charstring was
    still present and just unreachable by code.
    """

    def test_symbol_glyph_reachable_at_original_code_via_type1_to_cff(self):
        # Code 0xD7 (215) is unassigned (.notdef) in StandardEncoding,
        # exactly like the real STIXMath-Italic font this was found
        # against, whose built-in encoding assigns "subsetneq" there.
        source = build_type1_bytes(
            {
                ".notdef": (0, ["endchar"]),
                "subsetneq": (685, [0, 0, "rmoveto", 685, 0, "rlineto"]),
            },
            encoding_overrides={0xD7: "subsetneq"},
        )
        result = type1_to_cff(source, codes={0xD7})
        assert result is not None

        from pdftl.fonts.font_subsetting import wrap_bare_cff_in_sfnt

        tt: TTFont = wrap_bare_cff_in_sfnt(result)
        cff = tt["CFF "].cff
        top_dict = cff[cff.fontNames[0]]

        assert "subsetneq" in tt.getGlyphOrder()
        # The regression: the glyph must be reachable at its ORIGINAL
        # code via the CFF's own built-in Encoding table, not merely
        # present somewhere in the glyph order/charset.
        assert hasattr(top_dict, "Encoding"), "no custom Encoding was set on the converted CFF"
        assert top_dict.Encoding[0xD7] == "subsetneq"

    def test_font_dict_own_differences_does_not_need_builtin_encoding(self):
        """A /Font dict with its own /Differences resolves purely by
        glyph name (via the CFF charset), so the absence of a builtin
        Encoding entry for its code is not itself a bug -- this just
        guards that resolve_code_to_glyph_names still honors a
        /Differences override when one is given."""
        source = build_type1_bytes(
            {
                ".notdef": (0, ["endchar"]),
                "subsetneq": (685, [0, 0, "rmoveto", 685, 0, "rlineto"]),
            },
        )
        result = type1_to_cff(source, codes={0x50}, differences=[0x50, "/subsetneq"])
        assert result is not None
        from pdftl.fonts.font_subsetting import wrap_bare_cff_in_sfnt

        tt = wrap_bare_cff_in_sfnt(result)
        assert "subsetneq" in tt.getGlyphOrder()


class TestType1ToCffFontMatrixPreservation:
    """
    Regression test for a real bug found rendering a converted PDF: a
    synthetic-italic/oblique font (a "...-Slant_NNN" style font, common
    output of PDF authoring tools that fake an italic by shearing an
    upright font's own outlines via /FontMatrix rather than drawing
    separate italic charstrings) rendered upright after conversion.
    FontBuilder.setupCFF only supplies its own FontMatrix (a plain
    unitsPerEm scale, no shear) when fontInfo has no "FontMatrix" key,
    so the original font's sheared matrix was silently being dropped.
    """

    def test_sheared_font_matrix_is_preserved(self):
        sheared_matrix = [0.001, 0, 0.0002, 0.001, 0, 0]
        source = build_type1_bytes(
            {
                ".notdef": (0, ["endchar"]),
                "k": (500, [0, 0, "rmoveto", 500, 0, "rlineto"]),
            },
            font_matrix=sheared_matrix,
        )
        result = type1_to_cff(source, codes={ord("k")})
        assert result is not None

        from pdftl.fonts.font_subsetting import wrap_bare_cff_in_sfnt

        tt = wrap_bare_cff_in_sfnt(result)
        cff = tt["CFF "].cff
        top_dict = cff[cff.fontNames[0]]

        assert list(top_dict.FontMatrix) == sheared_matrix


class TestType1ToCffPerformance:
    def test_completes_quickly_on_a_realistic_font(self, three_glyph_type1_bytes):
        """Regression guard for the reported slowness: conversion of a
        small real font should take well under a second, not hang."""
        import time

        start = time.monotonic()
        for _ in range(20):
            type1_to_cff(three_glyph_type1_bytes, codes={65, 66})
        elapsed = time.monotonic() - start
        assert elapsed < 5.0


class TestType1ToCffCoverageExtensions:
    """Additional unit tests targeting 100% line and branch coverage in
    pdftl.fonts.type1_to_cff."""

    def test_open_type1_font_bytes_retry_synthesized_trailer(self, three_glyph_type1_bytes):
        """Exercises lines 110-111: retrying with synthesized trailer when
        the font is missing both trailing zeroes and cleartomark."""
        cleartomark_idx = three_glyph_type1_bytes.find(b"cleartomark")
        assert cleartomark_idx != -1
        truncated = three_glyph_type1_bytes[:cleartomark_idx].rstrip(b"0\r\n\t ")

        font = open_type1_font_bytes(truncated)
        assert font is not None

    def test_open_type1_font_bytes_other_t1_error(self, monkeypatch):
        """Exercises lines 105-107: T1Error without 'can't find end of eexec part'."""
        from fontTools.t1Lib import T1Error, T1Font

        monkeypatch.setattr(T1Font, "__init__", lambda self, *a, **kw: None)

        def mock_parse(self):
            raise T1Error("corrupt eexec header")

        monkeypatch.setattr(T1Font, "parse", mock_parse)
        font = open_type1_font_bytes(b"some bytes")
        assert font is None

    def test_open_type1_font_bytes_ps_error(self, monkeypatch):
        """Exercises lines 112-128: PSError / ValueError during parse."""
        from fontTools.misc.psLib import PSError
        from fontTools.t1Lib import T1Font

        monkeypatch.setattr(T1Font, "__init__", lambda self, *a, **kw: None)

        def mock_parse(self):
            raise PSError("bad postscript syntax")

        monkeypatch.setattr(T1Font, "parse", mock_parse)
        font = open_type1_font_bytes(b"some bytes")
        assert font is None

    def test_open_type1_font_bytes_all_attempts_fail_eexec_end(self, monkeypatch):
        """Exercises line 132: both candidate attempts raise 'can't find end of eexec part'."""
        from fontTools.t1Lib import T1Error, T1Font

        monkeypatch.setattr(T1Font, "__init__", lambda self, *a, **kw: None)

        def mock_parse(self):
            raise T1Error("can't find end of eexec part")

        monkeypatch.setattr(T1Font, "parse", mock_parse)
        font = open_type1_font_bytes(b"some bytes")
        assert font is None

    def test_draw_charstrings_glyph_not_in_set_and_draw_exception(self, three_glyph_type1_bytes):
        """Exercises line 236 (glyph not in glyph_set) and lines 246-248 (exception during draw)."""
        font = open_type1_font_bytes(three_glyph_type1_bytes)
        assert font is not None

        # Request a glyph name that doesn't exist in glyph_set ('NonExistentGlyph')
        result = build_cff_from_glyph_names(font, {"A", "NonExistentGlyph"})
        assert result is not None

        # Test draw exception (lines 246-248) by setting bad glyph
        glyph_set = font.getGlyphSet()

        class BadGlyph:
            def draw(self, pen):
                raise ValueError("corrupt glyph charstring")

        glyph_set["A"] = BadGlyph()
        result_bad = build_cff_from_glyph_names(font, {"A"})
        assert result_bad is None

    def test_build_cff_encoding_table_branches(self, three_glyph_type1_bytes):
        """Exercises branch 267->266 (invalid code or missing name) and line 271 (returns None)."""
        font = open_type1_font_bytes(three_glyph_type1_bytes)
        assert font is not None

        code_to_name = {300: "A", 65: "MissingGlyph"}
        result = build_cff_from_glyph_names(font, {"A"}, code_to_name=code_to_name)
        assert result is not None

    def test_assemble_cff_missing_font_matrix_and_no_code_to_name(self, three_glyph_type1_bytes):
        """Exercises branch 307->309 (source_matrix is None) and branch 315->319 (code_to_name is None)."""
        font = open_type1_font_bytes(three_glyph_type1_bytes)
        assert font is not None
        font.font["FontMatrix"] = None

        result = build_cff_from_glyph_names(font, {"A"}, code_to_name=None)
        assert result is not None

    def test_assemble_cff_compile_error(self, three_glyph_type1_bytes, monkeypatch):
        """Exercises lines 320-322: exception during CFF assembly/compilation."""
        from fontTools.fontBuilder import FontBuilder
        from fontTools.ttLib import TTLibError

        font = open_type1_font_bytes(three_glyph_type1_bytes)
        assert font is not None

        def mock_setup_cff(*args, **kwargs):
            raise TTLibError("compilation failed")

        monkeypatch.setattr(FontBuilder, "setupCFF", mock_setup_cff)
        result = build_cff_from_glyph_names(font, {"A"})
        assert result is None

    def test_missing_notdef_in_glyph_set_synthesizes_notdef(self):
        """Exercises branch 364->367 (.notdef not in glyph_set) and lines 370-371
        (synthesizing .notdef when missing from charstrings)."""
        source = build_type1_bytes(
            {
                "A": (500, [0, 0, "rmoveto", 500, 0, "rlineto"]),
            }
        )
        font = open_type1_font_bytes(source)
        assert font is not None

        glyph_set = font.getGlyphSet()
        if ".notdef" in glyph_set:
            del glyph_set[".notdef"]

        result = build_cff_from_glyph_names(font, {"A"})
        assert result is not None
        names = _glyph_names_in_cff(result)
        assert "A" in names
        assert ".notdef" in names


from unittest.mock import patch


class TestType1ToCffExceptionHandling:
    def test_open_type1_font_bytes_handles_index_error(self, three_glyph_type1_bytes):
        """Ensures open_type1_font_bytes catches IndexError raised during parsing

        and cleanly returns None.
        """
        with patch(
            "fontTools.t1Lib.T1Font.parse", side_effect=IndexError("simulated index error")
        ):
            result = open_type1_font_bytes(three_glyph_type1_bytes)
            assert result is None


import fontTools.misc.eexec as ft_eexec
from pdftl.fonts.type1_to_cff import _install_fast_eexec_decrypt


class TestFastEexecDecrypt:
    def test_fast_decrypt_accepts_str_cipherstring(self):
        """Exercises line 62 by passing a str cipherstring to the patched

        ft_eexec.decrypt function, verifying latin-1 string encoding.
        """
        _install_fast_eexec_decrypt()

        cipher_str = "abcd"
        R_initial = 55665

        # Call with str input to trigger `isinstance(cipherstring, str)`
        result_bytes, R_out = ft_eexec.decrypt(cipher_str, R_initial)

        # Compare against calling directly with bytes
        expected_bytes, expected_R = ft_eexec.decrypt(cipher_str.encode("latin-1"), R_initial)

        assert result_bytes == expected_bytes
        assert R_out == expected_R
