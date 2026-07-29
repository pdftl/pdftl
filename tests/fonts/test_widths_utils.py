# tests/fonts/test_widths_utils.py

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

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


def test_composite_widths_str_next_val():
    """Exercises line 135: native Python str or bytes as next_val in raw dict /W array."""
    cid_font = {
        "/Subtype": "/CIDFontType2",
        "/W": [10, "string_val", 500],
    }
    font = {
        "/Subtype": "/Type0",
        "/DescendantFonts": [cid_font],
    }
    assert extract_font_widths(font) == {}


def test_update_composite_widths_empty_map_no_existing_w():
    """Exercises branch 235->237: empty widths_map when /W is absent in CIDFont."""
    cid_font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/CIDFontType2")})
    font = pikepdf.Dictionary(
        {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
    )
    update_font_widths(font, {}, pikepdf)
    assert "/W" not in cid_font


def test_update_composite_widths_invalid_keys_no_existing_w():
    """Exercises branch 248->250: normalized_map is empty when /W is absent in CIDFont."""
    cid_font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/CIDFontType2")})
    font = pikepdf.Dictionary(
        {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
    )
    update_font_widths(font, {"ZZZZ": 100.0}, pikepdf)
    assert "/W" not in cid_font


# --- append to tests/fonts/test_widths_utils.py ---

import pytest


def test_extract_cid_to_gid_map_read_bytes_pdferror_degrades_to_identity():
    """A genuine pikepdf.PdfError from read_bytes() (e.g. an unfilterable
    stream due to an invalid /Filter) must degrade to Identity, same as
    the AttributeError/TypeError cases already covered. This is distinct
    from test_extract_cid_to_gid_map_read_bytes_raises_degrades_to_identity,
    whose _RaisingStream fails the isinstance(c2g, pikepdf.Stream) check
    up front and never reaches read_bytes() at all -- it needs a real
    pikepdf.Stream to actually exercise the except branch."""
    pdf = pikepdf.new()
    stream = pdf.make_stream(b"\x00\x05\x00\x0a")
    stream.Filter = pikepdf.Name("/FakeFilterDoesNotExist")

    cid_font = pikepdf.Dictionary(
        {"/Subtype": pikepdf.Name("/CIDFontType2"), "/CIDToGIDMap": stream}
    )
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
        )
    )

    with pytest.raises(pikepdf.PdfError):
        stream.read_bytes()  # sanity check this really raises PdfError, not something else

    assert extract_cid_to_gid_map(font) == "Identity"


# ============================================================================
# Vertical writing mode: /DW2, /W2, VerticalMetricsLookup, is_vertical_writing_mode
# ============================================================================

from pdftl.fonts.widths_utils import (
    build_vertical_metrics_lookup,
    extract_vertical_widths,
    get_default_vertical_metrics,
    is_vertical_writing_mode,
    _SPEC_DEFAULT_DW2,
)


class TestGetDefaultVerticalMetrics:
    def test_missing_dw2_returns_spec_default(self):
        cid_font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/CIDFontType2")})
        assert get_default_vertical_metrics(cid_font) == _SPEC_DEFAULT_DW2

    def test_none_cid_font_returns_spec_default(self):
        assert get_default_vertical_metrics(None) == _SPEC_DEFAULT_DW2

    def test_explicit_dw2_parsed(self):
        cid_font = pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/CIDFontType2"), "/DW2": pikepdf.Array([900.0, -1050.0])}
        )
        assert get_default_vertical_metrics(cid_font) == (900.0, -1050.0)

    def test_malformed_dw2_too_short_falls_back(self):
        cid_font = pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/CIDFontType2"), "/DW2": pikepdf.Array([900.0])}
        )
        assert get_default_vertical_metrics(cid_font) == _SPEC_DEFAULT_DW2

    def test_malformed_dw2_non_numeric_falls_back(self):
        cid_font = pikepdf.Dictionary(
            {
                "/Subtype": pikepdf.Name("/CIDFontType2"),
                "/DW2": pikepdf.Array([pikepdf.Name("/Bad"), -1000.0]),
            }
        )
        assert get_default_vertical_metrics(cid_font) == _SPEC_DEFAULT_DW2


class TestExtractVerticalWidths:
    def test_non_type0_returns_empty(self):
        font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
        assert extract_vertical_widths(font) == {}

    def test_no_descendant_returns_empty(self):
        font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type0")})
        assert extract_vertical_widths(font) == {}

    def test_no_w2_key_returns_empty(self):
        cid_font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/CIDFontType2")})
        font = pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
        )
        assert extract_vertical_widths(font) == {}

    def test_sequence_form_single_triple(self):
        cid_font = pikepdf.Dictionary(
            {
                "/Subtype": pikepdf.Name("/CIDFontType2"),
                "/W2": pikepdf.Array([10, pikepdf.Array([500.0, 250.0, 880.0])]),
            }
        )
        font = pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
        )
        assert extract_vertical_widths(font) == {"000A": (500.0, 250.0, 880.0)}

    def test_sequence_form_multiple_triples(self):
        cid_font = pikepdf.Dictionary(
            {
                "/Subtype": pikepdf.Name("/CIDFontType2"),
                "/W2": pikepdf.Array(
                    [10, pikepdf.Array([500.0, 250.0, 880.0, 600.0, 300.0, 900.0])]
                ),
            }
        )
        font = pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
        )
        result = extract_vertical_widths(font)
        assert result == {
            "000A": (500.0, 250.0, 880.0),
            "000B": (600.0, 300.0, 900.0),
        }

    def test_range_form(self):
        cid_font = pikepdf.Dictionary(
            {
                "/Subtype": pikepdf.Name("/CIDFontType2"),
                "/W2": pikepdf.Array([10, 12, 500.0, 250.0, 880.0]),
            }
        )
        font = pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
        )
        result = extract_vertical_widths(font)
        assert result == {
            "000A": (500.0, 250.0, 880.0),
            "000B": (500.0, 250.0, 880.0),
            "000C": (500.0, 250.0, 880.0),
        }

    def test_range_form_bad_end_cid_skips_range(self):
        cid_font = pikepdf.Dictionary(
            {
                "/Subtype": pikepdf.Name("/CIDFontType2"),
                "/W2": pikepdf.Array([10, pikepdf.Name("/Bad"), 500.0, 250.0, 880.0]),
            }
        )
        font = pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
        )
        assert extract_vertical_widths(font) == {}

    def test_sequence_form_malformed_triple_skipped(self):
        cid_font = pikepdf.Dictionary(
            {
                "/Subtype": pikepdf.Name("/CIDFontType2"),
                "/W2": pikepdf.Array([10, pikepdf.Array([500.0, 250.0, pikepdf.Name("/Bad")])]),
            }
        )
        font = pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
        )
        assert extract_vertical_widths(font) == {}

    def test_trailing_single_entry_breaks(self):
        cid_font = pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/CIDFontType2"), "/W2": pikepdf.Array([10])}
        )
        font = pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
        )
        assert extract_vertical_widths(font) == {}

    def test_range_form_truncated_missing_vy_advances_by_one(self):
        """A /W2 array long enough to look like the start of a range-form
        entry (numeric next_val) but too short to hold all of w1y/vx/vy
        must not IndexError -- it falls through to the `else: idx += 1`
        branch and that single malformed start_cid is simply skipped."""
        cid_font = pikepdf.Dictionary(
            {
                "/Subtype": pikepdf.Name("/CIDFontType2"),
                "/W2": pikepdf.Array([10, 500.0, 250.0]),  # only 3 elements total
            }
        )
        font = pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
        )
        assert extract_vertical_widths(font) == {}

    def test_bad_start_cid_skipped(self):
        cid_font = pikepdf.Dictionary(
            {
                "/Subtype": pikepdf.Name("/CIDFontType2"),
                "/W2": pikepdf.Array(
                    [pikepdf.Name("/Bad"), 10, pikepdf.Array([500.0, 250.0, 880.0])]
                ),
            }
        )
        font = pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
        )
        # First entry (bad start_cid) skipped via idx += 1; remaining
        # [10, [500, 250, 880]] parses as a valid sequence entry.
        assert extract_vertical_widths(font) == {"000A": (500.0, 250.0, 880.0)}

    def test_str_next_val_treated_as_non_sequence(self):
        cid_font = {
            "/Subtype": "/CIDFontType2",
            "/W2": [10, "not_an_array", 500.0, 250.0, 880.0],
        }
        font = {"/Subtype": "/Type0", "/DescendantFonts": [cid_font]}
        result = extract_vertical_widths(font)
        # "not_an_array" is a str, so is_sequence=False -> routes to the
        # range-form branch, where int("not_an_array") as end_cid raises
        # and the whole range is skipped -- mirrors
        # test_composite_widths_str_next_val's identical /W case.
        assert result == {}


class TestVerticalMetricsLookup:
    def test_w2_hit_returned_directly(self):
        cid_font = pikepdf.Dictionary(
            {
                "/Subtype": pikepdf.Name("/CIDFontType2"),
                "/W2": pikepdf.Array([10, pikepdf.Array([500.0, 250.0, 880.0])]),
            }
        )
        font = pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
        )
        lookup = build_vertical_metrics_lookup(font)
        assert lookup.get("000A") == (500.0, 250.0, 880.0)

    def test_miss_falls_back_to_dw2_with_zero_horizontal_width(self):
        cid_font = pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/CIDFontType2"), "/DW2": pikepdf.Array([880.0, -1000.0])}
        )
        font = pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
        )
        lookup = build_vertical_metrics_lookup(font)
        # No /W at all -> horizontal width defaults to 0.0 -> vx = 0/2 = 0.0
        assert lookup.get("0099") == (-1000.0, 0.0, 880.0)

    def test_miss_uses_spec_exact_vx_from_horizontal_width(self):
        """The whole point of build_vertical_metrics_lookup over the naive
        approach: vx defaults to w0/2 using the REAL /W entry, not 0.0."""
        cid_font = pikepdf.Dictionary(
            {
                "/Subtype": pikepdf.Name("/CIDFontType2"),
                "/DW2": pikepdf.Array([880.0, -1000.0]),
                "/W": pikepdf.Array(
                    [20, pikepdf.Array([600.0])]
                ),  # sequence form: CID 0x14 -> width 600
            }
        )
        font = pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
        )
        lookup = build_vertical_metrics_lookup(font)
        # CID 20 = 0x0014, has no /W2 entry, so falls back to DW2 with
        # vx = horizontal_width / 2 = 600 / 2 = 300.0
        assert lookup.get("0014") == (-1000.0, 300.0, 880.0)

    def test_lookup_is_frozen_dataclass(self):
        import pytest
        import dataclasses

        font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
        lookup = build_vertical_metrics_lookup(font)
        with pytest.raises(dataclasses.FrozenInstanceError):
            lookup.w2_map = {}

    def test_build_on_non_type0_font_still_works_with_empty_maps(self):
        font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
        lookup = build_vertical_metrics_lookup(font)
        v_y, w1 = _SPEC_DEFAULT_DW2
        assert lookup.get("0041") == (w1, 0.0, v_y)


class TestIsVerticalWritingMode:
    def test_non_type0_returns_false(self):
        font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
        assert is_vertical_writing_mode(font) is False

    def test_no_encoding_returns_false(self):
        font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type0")})
        assert is_vertical_writing_mode(font) is False

    def test_identity_v_name_returns_true(self):
        font = pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/Type0"), "/Encoding": pikepdf.Name("/Identity-V")}
        )
        assert is_vertical_writing_mode(font) is True

    def test_identity_h_name_returns_false(self):
        font = pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/Type0"), "/Encoding": pikepdf.Name("/Identity-H")}
        )
        assert is_vertical_writing_mode(font) is False

    def test_other_predefined_v_suffix_name_returns_true(self):
        font = pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/Type0"), "/Encoding": pikepdf.Name("/UniGB-UCS2-V")}
        )
        assert is_vertical_writing_mode(font) is True

    def test_embedded_cmap_stream_wmode_1_returns_true(self):
        pdf = pikepdf.new()
        cmap_stream = pdf.make_stream(b"%CMap fake content")
        cmap_stream.WMode = 1
        font = pdf.make_indirect(
            pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type0"), "/Encoding": cmap_stream})
        )
        assert is_vertical_writing_mode(font) is True

    def test_embedded_cmap_stream_wmode_0_returns_false(self):
        pdf = pikepdf.new()
        cmap_stream = pdf.make_stream(b"%CMap fake content")
        cmap_stream.WMode = 0
        font = pdf.make_indirect(
            pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type0"), "/Encoding": cmap_stream})
        )
        assert is_vertical_writing_mode(font) is False

    def test_embedded_cmap_stream_no_wmode_defaults_false(self):
        pdf = pikepdf.new()
        cmap_stream = pdf.make_stream(b"%CMap fake content")
        font = pdf.make_indirect(
            pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type0"), "/Encoding": cmap_stream})
        )
        assert is_vertical_writing_mode(font) is False

    def test_embedded_cmap_stream_malformed_wmode_returns_false(self):
        pdf = pikepdf.new()
        cmap_stream = pdf.make_stream(b"%CMap fake content")
        cmap_stream.WMode = pikepdf.Name("/Bad")
        font = pdf.make_indirect(
            pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type0"), "/Encoding": cmap_stream})
        )
        assert is_vertical_writing_mode(font) is False

    def test_encoding_neither_name_nor_stream_returns_false(self):
        """Malformed PDF: /Encoding as e.g. a plain integer. Should degrade
        to False rather than raising."""
        font = {"/Subtype": "/Type0", "/Encoding": 42}
        assert is_vertical_writing_mode(font) is False
