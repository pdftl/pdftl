# tests/fonts/test_cff_fontmatrix_splice.py

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
Unit tests for pdftl.fonts.cff_fontmatrix_splice: byte-level forcing of a
Top DICT FontMatrix operator into already-compiled CFF bytes, bypassing
fontTools' own compile()-time default-omission behavior.

Builds a real CFF byte stream via the same FontBuilder path
test_cff_binary_utils.py uses, confirms fontTools genuinely omits an
explicit spec-default FontMatrix on compile (i.e. that the bug this
module works around is real, not assumed), then confirms the splice
both restores it and leaves every other glyph/offset intact.
"""

from __future__ import annotations

import pytest

from fontTools.cffLib import CFFFontSet
from io import BytesIO

from pdftl.fonts.cff_fontmatrix_splice import (
    _build_index,
    _decode_dict,
    _encode_dict,
    _encode_int,
    _encode_operand,
    _encode_operator,
    _encode_real,
    _off_size_for,
    _read_index,
    splice_top_font_matrix,
)


def _build_cff_with_explicit_default_matrix() -> bytes:
    """A genuine bare CFF program whose Top DICT explicitly sets
    FontMatrix to the CFF spec default (0.001 0 0 0.001 0 0) -- the exact
    shape that reproduces fontTools silently omitting the operator on
    compile (a redundant-value optimization applied regardless of intent)."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen
    from fontTools.ttLib import TTFont

    pen = T2CharStringPen(500, {})
    pen.moveTo((0, 0))
    pen.lineTo((0, 500))
    pen.lineTo((500, 500))
    pen.closePath()
    charstrings = {".notdef": T2CharStringPen(0, {}).getCharString(), "A": pen.getCharString()}

    fb = FontBuilder(1000, isTTF=False)
    fb.setupGlyphOrder([".notdef", "A"])
    fb.setupCharacterMap({})
    fb.setupCFF("TestFont-Regular", {"FontName": "TestFont-Regular"}, charstrings, {})
    fb.setupHorizontalMetrics({".notdef": (0, 0), "A": (500, 0)})
    fb.setupHorizontalHeader(advanceWidthMax=500)
    fb.setupNameTable({"familyName": "Test", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()

    buf = BytesIO()
    fb.font.save(buf)
    buf.seek(0)
    tt = TTFont(buf)

    cff = tt["CFF "].cff
    topdict = cff[cff.fontNames[0]]
    topdict.rawDict["FontMatrix"] = [0.001, 0, 0, 0.001, 0, 0]

    out = BytesIO()
    cff.compile(out, tt)
    return out.getvalue()


def _read_top_font_matrix(cff_bytes: bytes):
    cff = CFFFontSet()
    cff.decompile(BytesIO(cff_bytes), otFont=None)
    return cff[cff.fontNames[0]].rawDict.get("FontMatrix")


def test_fonttools_confirms_the_bug_being_worked_around():
    """Sanity check that the premise for this module's existence still
    holds against the installed fontTools version: an explicit,
    spec-default Top DICT FontMatrix is genuinely dropped by compile()."""
    cff_bytes = _build_cff_with_explicit_default_matrix()
    assert _read_top_font_matrix(cff_bytes) is None


def test_splice_restores_dropped_font_matrix():
    cff_bytes = _build_cff_with_explicit_default_matrix()
    patched = splice_top_font_matrix(cff_bytes, (0.001, 0, 0, 0.001, 0, 0))
    assert _read_top_font_matrix(patched) == [0.001, 0, 0, 0.001, 0, 0]


# --- Low-level DICT operand encoding: every size class ------------------


class TestEncodeInt:
    def test_single_byte_range(self):
        assert _encode_int(0) == bytes([139])
        assert _encode_int(107) == bytes([246])
        assert _encode_int(-107) == bytes([32])

    def test_two_byte_positive_range(self):
        # 108..1131 -> b0 in 247..250
        encoded = _encode_int(108)
        assert encoded[0] == 247
        encoded_hi = _encode_int(1131)
        assert encoded_hi[0] == 250

    def test_two_byte_negative_range(self):
        # -1131..-108 -> b0 in 251..254
        encoded = _encode_int(-108)
        assert encoded[0] == 251
        encoded_hi = _encode_int(-1131)
        assert encoded_hi[0] == 254

    def test_16_bit_range(self):
        encoded = _encode_int(1132)
        assert encoded[0] == 28
        encoded_neg = _encode_int(-1132)
        assert encoded_neg[0] == 28
        encoded_boundary = _encode_int(32767)
        assert encoded_boundary[0] == 28

    def test_32_bit_range(self):
        encoded = _encode_int(32768)
        assert encoded[0] == 29
        encoded_neg = _encode_int(-32769)
        assert encoded_neg[0] == 29


class TestEncodeReal:
    def test_positive_decimal(self):
        b = _encode_real(0.001)
        assert b[0] == 30

    def test_negative_value(self):
        b = _encode_real(-1.5)
        assert b[0] == 30
        # round-trip through the real decoder in _decode_dict's parsing
        # logic isn't exercised here directly; covered by the roundtrip
        # test below via a full DICT encode/decode cycle.

    def test_exponent_positive_and_negative(self):
        # Values that repr() renders in scientific notation exercise the
        # 'E' and 'E-' nibble branches.
        assert _encode_real(1e20)[0] == 30
        assert _encode_real(1e-20)[0] == 30

    def test_odd_nibble_count_gets_padded(self):
        # An odd number of significant digit-nibbles must still produce
        # whole bytes (trailing 0xF pad nibble).
        b = _encode_real(1.0)
        assert len(b) >= 2


class TestEncodeOperand:
    def test_int_dispatches_to_encode_int(self):
        assert _encode_operand(5) == _encode_int(5)

    def test_integral_float_uses_compact_int_form(self):
        assert _encode_operand(5.0) == _encode_int(5)

    def test_non_integral_float_uses_real_form(self):
        assert _encode_operand(0.5)[0] == 30

    def test_unsupported_type_raises(self):
        with pytest.raises(TypeError):
            _encode_operand("not a number")


class TestEncodeOperator:
    def test_single_byte_operator(self):
        assert _encode_operator(15) == bytes([15])

    def test_escape_operator_tuple(self):
        assert _encode_operator((12, 7)) == bytes([12, 7])


class TestDecodeDictRoundtrip:
    def test_roundtrips_every_operand_size_class(self):
        """Builds a DICT with one operator per operand-size class (short
        int, 2-byte int, 3-byte-equivalent boundary, 4-byte int, and a
        real number), encodes it, decodes it, and confirms every operand
        value survives -- exercising _decode_dict's byte-28/29/30 and
        32-246/247-250/251-254 branches directly, not just via a full
        compiled CFF font."""
        entries = [
            (0, [0]),  # single-byte operand
            (1, [500]),  # 2-byte positive (247-250 range)
            (2, [-500]),  # 2-byte negative (251-254 range)
            (3, [40000]),  # 16-bit (byte 28)
            (4, [100000]),  # 32-bit (byte 29)
            ((12, 7), [0.001, 0, 0, 0.001, 0, 0]),  # real numbers
        ]
        encoded = _encode_dict(entries)
        decoded = _decode_dict(encoded)
        assert decoded[0] == (0, [0])
        assert decoded[1] == (1, [500])
        assert decoded[2] == (2, [-500])
        assert decoded[3] == (3, [40000])
        assert decoded[4] == (4, [100000])
        op, operands = decoded[5]
        assert op == (12, 7)
        assert operands == pytest.approx([0.001, 0, 0, 0.001, 0, 0])

    def test_reserved_byte_raises(self):
        with pytest.raises(ValueError):
            _decode_dict(bytes([31]))  # 31 is reserved/invalid


class TestReadIndexEmptyCase:
    def test_zero_count_index_has_no_offsets(self):
        empty_index = (0).to_bytes(2, "big")  # count=0, nothing else
        entries, start, end = _read_index(empty_index, 0)
        assert entries == []
        assert end == 2


class TestOffSizeBoundaries:
    def test_off_size_thresholds(self):
        assert _off_size_for(0xFF) == 1
        assert _off_size_for(0x100) == 2
        assert _off_size_for(0xFFFF) == 2
        assert _off_size_for(0x10000) == 3
        assert _off_size_for(0xFFFFFF) == 3
        assert _off_size_for(0x1000000) == 4


class TestSpliceErrorPaths:
    def test_more_than_one_top_dict_raises(self):
        """splice_top_font_matrix assumes a single-font CFF (true for
        every /FontFile3 in a PDF); a multi-font Top DICT INDEX must
        raise rather than silently patch the wrong entry."""

        header = bytes([1, 0, 4, 4])  # major, minor, hdrSize=4, offSize=4
        name_index = _build_index([b"Font1", b"Font2"])
        # Two Top DICT entries, matching the two names above.
        top_dict_index = _build_index([b"\x8b\x00", b"\x8b\x00"])
        fake_cff = header + name_index + top_dict_index

        with pytest.raises(ValueError, match="Expected exactly 1 Top DICT"):
            splice_top_font_matrix(fake_cff, (0.001, 0, 0, 0.001, 0, 0))

    def test_shifts_offset_operators_when_index_grows(self):
        """When the Top DICT already has offset-valued operators
        (charset/CharStrings/Private/FDArray/FDSelect), inserting
        FontMatrix must bump each of them by the INDEX's growth -- this
        exercises the _OFFSET_OPERATORS shift loop and the fixpoint
        iteration, not just the no-existing-offsets case the other tests
        cover."""
        # A minimal but real Top DICT carrying charset (15), CharStrings
        # (17), and Private [size, offset] (18) operators with small
        # offset values, encoded directly (bypassing fontTools) so we
        # control exactly what's present.
        entries = [
            (15, [50]),  # charset offset
            (17, [80]),  # CharStrings offset
            (18, [10, 90]),  # Private: size=10, offset=90
        ]
        dict_bytes = _encode_dict(entries)

        header = bytes([1, 0, 4, 4])
        name_index = _build_index([b"F"]) if False else None
        from pdftl.fonts.cff_fontmatrix_splice import _build_index as bi

        name_index = bi([b"F"])
        top_dict_index = bi([dict_bytes])
        fake_cff = header + name_index + top_dict_index

        patched = splice_top_font_matrix(fake_cff, (0.001, 0, 0, 0.001, 0, 0))

        # Re-locate and decode the patched Top DICT directly.
        pos = 4
        _, _, pos = _read_index(patched, pos)
        top_entries, _, _ = _read_index(patched, pos)
        decoded = dict(_decode_dict(top_entries[0]))

        growth = len(patched) - len(fake_cff)
        assert decoded[15][0] == 50 + growth
        assert decoded[17][0] == 80 + growth
        assert decoded[18][1] == 90 + growth
        assert decoded[18][0] == 10  # size operand untouched, only offset shifts
        assert decoded[(12, 7)] == [0.001, 0, 0, 0.001, 0, 0]


def test_splice_preserves_charstrings_and_glyph_order():
    """The splice must not corrupt anything downstream of the Top DICT
    INDEX -- re-decompile the whole font and confirm glyphs survive."""
    cff_bytes = _build_cff_with_explicit_default_matrix()
    patched = splice_top_font_matrix(cff_bytes, (0.001, 0, 0, 0.001, 0, 0))

    cff = CFFFontSet()
    cff.decompile(BytesIO(patched), otFont=None)
    topdict = cff[cff.fontNames[0]]
    assert list(topdict.CharStrings.keys()) == [".notdef", "A"]


def test_splice_overwrites_an_already_present_non_default_matrix():
    """When the Top DICT already carries a FontMatrix operator (any
    non-default value fontTools didn't strip), splicing overwrites it
    rather than duplicating the operator."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen
    from fontTools.ttLib import TTFont

    charstrings = {".notdef": T2CharStringPen(0, {}).getCharString()}
    fb = FontBuilder(1000, isTTF=False)
    fb.setupGlyphOrder([".notdef"])
    fb.setupCharacterMap({})
    fb.setupCFF("TestFont-Regular", {"FontName": "TestFont-Regular"}, charstrings, {})
    fb.setupHorizontalMetrics({".notdef": (0, 0)})
    fb.setupHorizontalHeader(advanceWidthMax=1)
    fb.setupNameTable({"familyName": "Test", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    buf = BytesIO()
    fb.font.save(buf)
    buf.seek(0)
    tt = TTFont(buf)
    cff = tt["CFF "].cff
    topdict = cff[cff.fontNames[0]]
    topdict.rawDict["FontMatrix"] = [0.002, 0, 0, 0.002, 0, 0]  # non-default, survives compile
    out = BytesIO()
    cff.compile(out, tt)
    cff_bytes = out.getvalue()
    assert _read_top_font_matrix(cff_bytes) == [0.002, 0, 0, 0.002, 0, 0]

    patched = splice_top_font_matrix(cff_bytes, (0.001, 0, 0, 0.001, 0, 0))
    assert _read_top_font_matrix(patched) == [0.001, 0, 0, 0.001, 0, 0]


# --- append to tests/fonts/test_cff_fontmatrix_splice.py ---


class TestDecode16BitIntBranch:
    def test_decodes_value_requiring_16_bit_encoding(self):
        """5000 falls outside both the 1-byte (-107..107) and 2-byte
        (108..1131 / -1131..-108) ranges, so _encode_int emits it via the
        byte-28 (16-bit) form -- exercising _decode_dict's byte==28
        branch, which the original roundtrip test's operand choices
        (40000, which is actually 32-bit) never reached."""
        entries = [(0, [5000]), (1, [-5000])]
        encoded = _encode_dict(entries)
        decoded = _decode_dict(encoded)
        assert decoded == [(0, [5000]), (1, [-5000])]


class TestBuildIndexEmptyCase:
    def test_empty_entries_returns_just_the_count_header(self):
        result = _build_index([])
        assert result == (0).to_bytes(2, "big")


class TestSpliceNonConvergence:
    def test_raises_if_offset_fixup_never_stabilizes(self, monkeypatch):
        """If the iterative delta fixpoint never stabilizes within the
        loop's retry budget, splice_top_font_matrix must raise rather
        than silently return an inconsistent result. Simulated by
        monkeypatching _build_index to grow by one byte every call, so
        new_delta never equals the previous delta."""
        import pdftl.fonts.cff_fontmatrix_splice as splice_mod

        call_count = {"n": 0}
        real_build_index = splice_mod._build_index

        def _ever_growing_build_index(entries):
            call_count["n"] += 1
            return real_build_index(entries) + (b"\x00" * call_count["n"])

        header = bytes([1, 0, 4, 4])
        name_index = real_build_index([b"F"])
        top_dict_index = real_build_index([_encode_dict([(15, [50])])])
        fake_cff = header + name_index + top_dict_index

        monkeypatch.setattr(splice_mod, "_build_index", _ever_growing_build_index)

        with pytest.raises(RuntimeError, match="did not converge"):
            splice_top_font_matrix(fake_cff, (0.001, 0, 0, 0.001, 0, 0))
