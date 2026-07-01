# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/fonts/test_widths_utils.py

import pikepdf
from pdftl.fonts.widths_utils import (
    _get_descendant_cid_font,
    compile_cid_to_gid_map,
    extract_cid_to_gid_map,
    extract_font_widths,
    parse_cid_to_gid_map,
    update_cid_to_gid_map,
    update_font_widths,
)


def test_simple_widths_edge_cases():
    # Missing arrays
    font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
    assert extract_font_widths(font) == {}
    update_font_widths(font, {}, pikepdf)
    assert "/Widths" not in font


def test_composite_widths_edge_cases():
    # Missing descendants
    font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type0")})
    assert extract_font_widths(font) == {}
    update_font_widths(font, {"001A": 500.0}, pikepdf)  # Should skip cleanly

    # Empty descendants
    font["/DescendantFonts"] = pikepdf.Array([])
    assert extract_font_widths(font) == {}
    update_font_widths(font, {"001A": 500.0}, pikepdf)

    # Missing /W in CIDFont
    cid_font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/CIDFontType2")})
    font["/DescendantFonts"] = pikepdf.Array([cid_font])
    assert extract_font_widths(font) == {}

    # Deleting /W if map is empty
    cid_font["/W"] = pikepdf.Array([10, 10, 500])
    update_font_widths(font, {}, pikepdf)
    assert "/W" not in cid_font


def test_composite_widths_bad_parse():
    cid_font = pikepdf.Dictionary(
        {
            "/Subtype": pikepdf.Name("/CIDFontType2"),
            "/W": pikepdf.Array([10, pikepdf.Name("/Invalid")]),
        }
    )
    font = pikepdf.Dictionary(
        {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
    )
    # Should safely skip the un-parseable sequence
    assert extract_font_widths(font) == {}


def test_simple_widths_round_trip():
    pdf = pikepdf.new()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/TrueType"),
                "/FirstChar": 65,
                "/LastChar": 67,
                "/Widths": pikepdf.Array([500.0, 600.0, 700.0]),
            }
        )
    )

    widths = extract_font_widths(font)
    assert widths == {"41": 500.0, "42": 600.0, "43": 700.0}

    # Modify and write back
    widths["42"] = 999.0
    update_font_widths(font, widths, pikepdf)

    assert int(font.FirstChar) == 65
    assert int(font.LastChar) == 67
    assert [float(x) for x in font.Widths] == [500.0, 999.0, 700.0]


def test_composite_w_array_round_trip():
    pdf = pikepdf.new()

    # Nested CIDFont dictionary
    cid_font = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/CIDFontType2"),
            "/W": pikepdf.Array(
                [
                    10,
                    12,
                    500.0,  # Range: 10-12 are 500 wide
                    20,
                    pikepdf.Array([250.0, 300.0]),  # Sequence: 20 is 250, 21 is 300 wide
                ]
            ),
        }
    )

    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type0"),
                "/DescendantFonts": pikepdf.Array([cid_font]),
            }
        )
    )

    widths = extract_font_widths(font)
    assert widths == {"000A": 500.0, "000B": 500.0, "000C": 500.0, "0014": 250.0, "0015": 300.0}

    # Modify and write back. Let's make 10-12 vary to force sequence compression.
    widths["000B"] = 999.0
    update_font_widths(font, widths, pikepdf)

    # Re-extract and verify round trip
    assert extract_font_widths(font) == widths


"""Additional tests closing coverage gaps in widths_utils.py."""


def test_simple_widths_bad_first_char():
    """Non-integer /FirstChar -> caught, returns {}."""
    font = pikepdf.Dictionary(
        {"/Widths": pikepdf.Array([100.0]), "/FirstChar": pikepdf.Name("/Bad")}
    )
    assert extract_font_widths(font) == {}


def test_simple_widths_bad_width_entry():
    """A non-numeric entry in /Widths is skipped, not fatal."""
    font = pikepdf.Dictionary({"/FirstChar": 65, "/Widths": pikepdf.Array([pikepdf.Name("/Bad")])})
    assert extract_font_widths(font) == {}


def test_composite_widths_trailing_single_entry_breaks():
    """A /W array ending on a lone start_cid with nothing after it -> break."""
    cid_font = pikepdf.Dictionary(
        {"/Subtype": pikepdf.Name("/CIDFontType2"), "/W": pikepdf.Array([10])}
    )
    font = pikepdf.Dictionary(
        {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
    )
    assert extract_font_widths(font) == {}


def test_composite_widths_sequence_array_bad_value():
    """A bad value inside a sequence array [w1 w2 ...] is skipped, not fatal."""
    cid_font = pikepdf.Dictionary(
        {
            "/Subtype": pikepdf.Name("/CIDFontType2"),
            "/W": pikepdf.Array([10, pikepdf.Array([250.0, pikepdf.Name("/Bad")])]),
        }
    )
    font = pikepdf.Dictionary(
        {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
    )
    assert extract_font_widths(font) == {"000A": 250.0}


def test_composite_widths_range_bad_width():
    """A bad width value in the c_first c_last w form is caught, not fatal."""
    cid_font = pikepdf.Dictionary(
        {
            "/Subtype": pikepdf.Name("/CIDFontType2"),
            "/W": pikepdf.Array([10, 12, pikepdf.Name("/Bad")]),
        }
    )
    font = pikepdf.Dictionary(
        {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
    )
    assert extract_font_widths(font) == {}


def test_update_simple_widths_all_invalid_keys():
    """Every key fails int(k, 16) -> normalized_map stays empty -> early return."""
    font = pikepdf.Dictionary({})
    update_font_widths(font, {"ZZ": 100.0}, pikepdf)
    assert "/Widths" not in font


def test_update_composite_widths_all_invalid_keys_deletes_w():
    """Every key fails int(k, 16) for a composite font -> deletes existing /W."""
    cid_font = pikepdf.Dictionary(
        {"/Subtype": pikepdf.Name("/CIDFontType2"), "/W": pikepdf.Array([1, 2, 3])}
    )
    font = pikepdf.Dictionary(
        {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
    )
    update_font_widths(font, {"ZZZZ": 1.0}, pikepdf)
    assert "/W" not in cid_font


def test_update_composite_widths_full_compression_round_trip():
    """
    Exercises: range_len>=3 compression, sequence-array (varying width) compression,
    an isolated single entry, and an invalid key filtered out during normalization.
    """
    pdf = pikepdf.new()
    cid_font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/CIDFontType2")})
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Subtype": pikepdf.Name("/Type0"),
                "/DescendantFonts": pikepdf.Array([cid_font]),
            }
        )
    )

    widths_map = {
        "000A": 500.0,  # \
        "000B": 500.0,  #  } contiguous run of 3, same width -> range compression
        "000C": 500.0,  # /
        "0014": 250.0,  # \  contiguous run of 2, different widths -> sequence array
        "0015": 300.0,  # /
        "0020": 999.0,  # isolated entry -> 1-element sequence array
        "ZZZZ": 1.0,  # invalid hex -> dropped during normalization
    }

    update_font_widths(font, widths_map, pikepdf)

    expected = {k: v for k, v in widths_map.items() if k != "ZZZZ"}
    assert extract_font_widths(font) == expected


def test_get_descendant_cid_font_no_descendants_key():
    font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type0")})
    assert _get_descendant_cid_font(font) is None


def test_get_descendant_cid_font_empty_array():
    font = pikepdf.Dictionary(
        {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([])}
    )
    assert _get_descendant_cid_font(font) is None


def test_parse_cid_to_gid_map_basic():
    # CID 0 -> GID 5, CID 1 -> GID 0 (.notdef, omitted), CID 2 -> GID 300
    raw = b"\x00\x05\x00\x00\x01\x2c"
    assert parse_cid_to_gid_map(raw) == {0: 5, 2: 300}


def test_parse_cid_to_gid_map_odd_trailing_byte_ignored():
    raw = b"\x00\x05\xff"
    assert parse_cid_to_gid_map(raw) == {0: 5}


def test_compile_cid_to_gid_map_empty():
    assert compile_cid_to_gid_map({}) == b""


def test_compile_cid_to_gid_map_round_trip_with_gaps():
    mapping = {0: 5, 2: 300}
    compiled = compile_cid_to_gid_map(mapping)
    assert len(compiled) == 6  # covers CIDs 0,1,2
    assert parse_cid_to_gid_map(compiled) == mapping


def test_extract_cid_to_gid_map_non_type0_returns_none():
    font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
    assert extract_cid_to_gid_map(font) is None


def test_extract_cid_to_gid_map_no_descendant_returns_identity():
    font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type0")})
    assert extract_cid_to_gid_map(font) == "Identity"


def test_extract_cid_to_gid_map_bare_identity_name():
    cid_font = pikepdf.Dictionary(
        {"/Subtype": pikepdf.Name("/CIDFontType2"), "/CIDToGIDMap": pikepdf.Name("/Identity")}
    )
    font = pikepdf.Dictionary(
        {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
    )
    assert extract_cid_to_gid_map(font) == "Identity"


def test_extract_cid_to_gid_map_explicit_stream():
    pdf = pikepdf.new()
    stream = pdf.make_stream(b"\x00\x05\x00\x0a")
    cid_font = pikepdf.Dictionary(
        {"/Subtype": pikepdf.Name("/CIDFontType2"), "/CIDToGIDMap": stream}
    )
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
        )
    )
    assert extract_cid_to_gid_map(font) == {0: 5, 1: 10}


def test_extract_cid_to_gid_map_all_zero_stream_degrades_to_identity():
    pdf = pikepdf.new()
    stream = pdf.make_stream(b"\x00\x00\x00\x00")
    cid_font = pikepdf.Dictionary(
        {"/Subtype": pikepdf.Name("/CIDFontType2"), "/CIDToGIDMap": stream}
    )
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
        )
    )
    assert extract_cid_to_gid_map(font) == "Identity"


def test_extract_cid_to_gid_map_no_read_bytes_attr():
    """Hits the hasattr(c2g, 'read_bytes') == False branch natively using pure dicts."""
    cid_font = {"/Subtype": "/CIDFontType2", "/CIDToGIDMap": "PlainString"}
    font = {"/Subtype": "/Type0", "/DescendantFonts": [cid_font]}
    assert extract_cid_to_gid_map(font) == "Identity"


def test_update_cid_to_gid_map_no_descendant_is_noop():
    font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type0")})
    update_cid_to_gid_map(font, {0: 5}, pikepdf)  # must not raise


def test_update_cid_to_gid_map_identity_writes_name():
    cid_font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/CIDFontType2")})
    font = pikepdf.Dictionary(
        {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
    )
    update_cid_to_gid_map(font, "Identity", pikepdf)
    assert cid_font["/CIDToGIDMap"] == pikepdf.Name("/Identity")


def test_update_cid_to_gid_map_empty_dict_writes_identity_name():
    cid_font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/CIDFontType2")})
    font = pikepdf.Dictionary(
        {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
    )
    update_cid_to_gid_map(font, {}, pikepdf)
    assert cid_font["/CIDToGIDMap"] == pikepdf.Name("/Identity")


def test_update_cid_to_gid_map_writes_new_stream():
    pdf = pikepdf.new()
    cid_font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/CIDFontType2")})
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
        )
    )
    update_cid_to_gid_map(font, {0: 5, 2: 300}, pikepdf, pdf)
    resolved_cid_font = font.DescendantFonts[0]
    assert parse_cid_to_gid_map(resolved_cid_font["/CIDToGIDMap"].read_bytes()) == {0: 5, 2: 300}


def test_update_cid_to_gid_map_reuses_existing_stream_object():
    pdf = pikepdf.new()
    original_stream = pdf.make_stream(b"\x00\x00")
    cid_font = pikepdf.Dictionary(
        {"/Subtype": pikepdf.Name("/CIDFontType2"), "/CIDToGIDMap": original_stream}
    )
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
        )
    )
    update_cid_to_gid_map(font, {0: 9}, pikepdf)
    resolved_cid_font = font.DescendantFonts[0]
    # Same underlying stream object rewritten in place, not replaced
    assert resolved_cid_font["/CIDToGIDMap"].objgen == original_stream.objgen


def test_update_cid_to_gid_map_missing_pdf_context_raises_value_error():
    import pytest

    cid_font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/CIDFontType2")})
    font = pikepdf.Dictionary(
        {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
    )
    with pytest.raises(ValueError, match="A valid pikepdf.Pdf context must be provided"):
        update_cid_to_gid_map(font, {0: 5}, pikepdf, pdf=None)


def test_extract_cid_to_gid_map_read_bytes_raises_degrades_to_identity():
    """A /CIDToGIDMap stream that raises on read degrades to Identity rather
    than aborting the whole font's extraction."""

    class _RaisingStream:
        def read_bytes(self):
            raise TypeError("simulated corrupt stream")

    cid_font = {"/Subtype": "/CIDFontType2", "/CIDToGIDMap": _RaisingStream()}
    font = {"/Subtype": "/Type0", "/DescendantFonts": [cid_font]}
    assert extract_cid_to_gid_map(font) == "Identity"
