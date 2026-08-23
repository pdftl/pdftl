# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/fonts/test_cmap_utils.py

from pdftl.fonts.cmap_utils import (
    _parse_hex,
    _to_hex_str,
    _try_parse_bfchar_item,
    compile_to_unicode_cmap,
    parse_to_unicode_cmap,
)


def test_parse_hex_helpers():
    # Valid
    assert _parse_hex("0041") == "A"
    assert _parse_hex("00660069") == "fi"

    # Invalid
    assert _parse_hex("004") == ""  # Not a multiple of 4

    # To hex
    assert _to_hex_str("A") == "0041"
    assert _to_hex_str("fi") == "00660069"


def test_parse_hex_handles_invalid_surrogate_pairs():
    """Ensure _parse_hex handles invalid surrogate pairs gracefully without raising exceptions."""
    # D800 is an unpaired high surrogate in UTF-16BE and is invalid under strict decoding
    assert _parse_hex("D800") == ""


def test_try_parse_bfchar_item_guards():
    """Ensure parsing of single bfchar items exits gracefully when provided with malformed tokens."""
    mappings = {}

    # Test with an invalid source token format (missing enclosing angle brackets)
    _try_parse_bfchar_item("invalid_src", "<0041>", mappings)
    assert not mappings

    # Test with a valid source but an invalid destination token format
    _try_parse_bfchar_item("<01>", "invalid_dst", mappings)
    assert not mappings


def test_parse_cmap_blocks():
    raw_cmap = b"""
    1 begincodespacerange
      <00> <FF>
    endcodespacerange
    2 beginbfchar
      <01> <0041> % Comment
      <02> <0042>
    endbfchar
    1 beginbfrange
      <03> <04> <0043>
    endbfrange
    1 beginbfrange
      <05> <06> [ <0058> <0059> ]
    endbfrange
    """
    mappings = parse_to_unicode_cmap(raw_cmap)
    assert mappings["01"] == "A"
    assert mappings["02"] == "B"
    assert mappings["03"] == "C"
    assert mappings["04"] == "D"
    assert mappings["05"] == "X"
    assert mappings["06"] == "Y"


def test_parse_cmap_edge_cases():
    raw_cmap_missing_end = b"1 beginbfchar <01> <0041>"
    mappings = parse_to_unicode_cmap(raw_cmap_missing_end)
    assert mappings["01"] == "A"  # It parses up to EOF safely

    # Empty mappings should produce empty bytes
    assert compile_to_unicode_cmap({}) == b""

    # Invalid CMap formats should return empty dictionaries gracefully without crashing
    assert parse_to_unicode_cmap(b"invalid data") == {}
    assert parse_to_unicode_cmap(b"beginbfchar <XYZ> <ABC> endbfchar") == {}

    # Bad range triggers ValueError in start/end code parsing
    raw_cmap_bad_range = b"1 beginbfrange <XX> <YY> <0041> endbfrange"
    assert parse_to_unicode_cmap(raw_cmap_bad_range) == {}

    # Bad sequential range destination triggers ValueError in dst_start_code parsing
    raw_cmap_bad_seq = b"1 beginbfrange <01> <02> <ZZZZ> endbfrange"
    assert parse_to_unicode_cmap(raw_cmap_bad_seq) == {}


def test_compile_cmap_logic():
    # Empty
    assert compile_to_unicode_cmap({}) == b""

    # Sequential Runs (>=3 triggers bfrange)
    mappings = {
        "01": "A",
        "02": "B",
        "03": "C",
        "0A": "X",
        "0B": "ffi",  # Ligature forces bfchar
    }
    compiled = compile_to_unicode_cmap(mappings)

    # Verify both blocks are present
    assert b"1 beginbfrange\n  <01> <03> <0041>\nendbfrange" in compiled
    assert b"beginbfchar" in compiled
    assert b"<0A> <0058>" in compiled
    assert b"<0B> <006600660069>" in compiled


"""Edge case tests for CMap stream parsing resiliency."""


def test_bfchar_block_skips_unexpected_non_hex_tokens():
    """Ensure bfchar parsing safely skips unexpected non-hex tokens inside blocks."""
    raw_cmap = b"1 beginbfchar garbage <01> <0041> endbfchar"
    mappings = parse_to_unicode_cmap(raw_cmap)
    assert mappings == {"01": "A"}


def test_bfrange_block_skips_unrecognized_destination_tokens():
    """Ensure bfrange parsing skips entries with unrecognizable destination token formats."""
    raw_cmap = b"1 beginbfrange <01> <02> garbage endbfrange"
    mappings = parse_to_unicode_cmap(raw_cmap)
    assert mappings == {}


def test_bfrange_block_terminates_gracefully_on_missing_end_marker():
    """Ensure bfrange parsing gracefully terminates when encountering end-of-stream before a block closure."""
    raw_cmap = b"1 beginbfrange <01> <02> <0041>"
    mappings = parse_to_unicode_cmap(raw_cmap)
    assert mappings == {"01": "A", "02": "B"}


def test_bfrange_array_skips_unrecognized_tokens_in_array():
    """Ensure bfrange array parsing safely skips unrecognized tokens within destination arrays."""
    raw_cmap = b"1 beginbfrange <05> <06> [ garbage <0058> <0059> ] endbfrange"
    mappings = parse_to_unicode_cmap(raw_cmap)
    assert mappings == {"05": "X", "06": "Y"}


def test_bfrange_array_terminates_gracefully_on_missing_closing_bracket():
    """Ensure bfrange array parsing safely terminates at end-of-stream when missing a closing bracket."""
    raw_cmap = b"1 beginbfrange <05> <06> [ <0058>"
    mappings = parse_to_unicode_cmap(raw_cmap)
    assert mappings == {"05": "X"}


"""Tests for detect_predefined_identity_encoding (G-1 Identity-H/-V fallback)."""

from pdftl.fonts.cmap_utils import detect_predefined_identity_encoding


def test_detect_predefined_identity_encoding_identity_h():
    font = {"/Subtype": "/Type0", "/Encoding": "/Identity-H"}
    assert detect_predefined_identity_encoding(font) == "Identity-H"


def test_detect_predefined_identity_encoding_identity_v():
    font = {"/Subtype": "/Type0", "/Encoding": "/Identity-V"}
    assert detect_predefined_identity_encoding(font) == "Identity-V"


def test_detect_predefined_identity_encoding_other_predefined_cmap():
    """A named but non-identity predefined CMap (e.g. a CJK ordering) is not
    something this helper resolves; it returns None rather than guessing."""
    font = {"/Subtype": "/Type0", "/Encoding": "/UniGB-UCS2-H"}
    assert detect_predefined_identity_encoding(font) is None


def test_detect_predefined_identity_encoding_non_type0():
    font = {"/Subtype": "/TrueType", "/Encoding": "/Identity-H"}
    assert detect_predefined_identity_encoding(font) is None


def test_detect_predefined_identity_encoding_no_encoding_key():
    font = {"/Subtype": "/Type0"}
    assert detect_predefined_identity_encoding(font) is None


def test_detect_predefined_identity_encoding_embedded_cmap_stream():
    """An embedded CMap program (a Stream, not a Name) never matches a
    known identity-CMap name and safely falls through to None."""

    class _FakeCMapStream:
        def __str__(self):
            return "<CMap stream object>"

    font = {"/Subtype": "/Type0", "/Encoding": _FakeCMapStream()}
    assert detect_predefined_identity_encoding(font) is None


def test_detect_predefined_identity_encoding_pikepdf_dictionary():
    """Confirms real pikepdf.Name values behave the same as the plain-dict
    tests above, since /Subtype and /Encoding checks use the same
    str(...).lstrip('/') pattern as elsewhere in the codebase."""
    import pikepdf

    font = pikepdf.Dictionary(
        {"/Subtype": pikepdf.Name("/Type0"), "/Encoding": pikepdf.Name("/Identity-H")}
    )
    assert detect_predefined_identity_encoding(font) == "Identity-H"


# ---------------------------------------------------------------------------
# Branch Coverage Edge Cases
# ---------------------------------------------------------------------------


def test_parse_bfchar_invalid_dst_hex_falsey_val():
    """Handles single-character mapping entries where destination hex fails decoding,
    ensuring unparsable destination strings are skipped rather than stored."""
    cmap = b"""
    beginbfchar
    <01> <123>
    endbfchar
    """
    assert parse_to_unicode_cmap(cmap) == {}


def test_parse_bfrange_array_overflow_and_invalid_val():
    """Handles range array blocks containing invalid hex values and items exceeding
    the declared range capacity."""
    cmap = b"""
    beginbfrange
    <01> <02> [ <123> <0042> <0043> ]
    endbfrange
    """
    # - <123> is invalid hex, so _parse_hex returns an empty string and skips assignment.
    # - <0042> validly maps to code 02 ("B").
    # - <0043> is ignored because the array provides more items than the start/end range allows.
    res = parse_to_unicode_cmap(cmap)
    assert res == {"02": "B"}


def test_parse_bfrange_sequential_invalid_val():
    """Handles sequential range entries where destination hex evaluates to an un-decodable
    UTF-16 sequence (such as an unpaired surrogate)."""
    cmap = b"""
    beginbfrange
    <01> <01> <D800>
    endbfrange
    """
    assert parse_to_unicode_cmap(cmap) == {}
