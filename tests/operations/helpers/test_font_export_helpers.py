# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/helpers/test_font_export_helpers.py

"""
Unit tests for pdftl.operations.helpers.font_export_helpers.
"""

from __future__ import annotations

from unittest.mock import MagicMock
import pikepdf
import pytest

from pdftl.fonts.font_extraction_utils import find_font_descriptor
from pdftl.operations.helpers.font_export_helpers import (
    _export_single_font_binary,
    _export_unified_sidecar,
    _extract_differences_list,
    _extract_base_encoding,
    _get_embedded_font_details,
    _get_font_suffix,
    _try_read_embedded_stream,
    _extract_tounicode_from_obj,
    _assemble_unified_mappings,
    _save_json_sidecar,
    _save_ps_sidecar,
    _sniff_is_cff2,
    build_manifest,
)


@pytest.fixture
def sample_pdf_with_type1_font():
    pdf = pikepdf.new()
    page = pdf.add_blank_page()

    font_descriptor = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name("/MyType1Font"),
                "/Flags": 32,
                "/FontFile": pdf.make_stream(b"Fake Type1 PFB Bytes"),
            }
        )
    )

    font_obj = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/MyType1Font"),
                "/FirstChar": 1,
                "/LastChar": 1,
                "/Widths": pikepdf.Array([250.0]),
                "/FontDescriptor": font_descriptor,
            }
        )
    )

    page.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font_obj})})

    return pdf, font_obj


@pytest.fixture
def sample_pdf_with_fonts():
    pdf = pikepdf.new()
    page = pdf.add_blank_page()

    # Create a simple TrueType font
    font_descriptor = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name("/MyFont"),
                "/Flags": 32,
                "/FontFile2": pdf.make_stream(b"Fake TrueType Font Bytes"),
            }
        )
    )

    to_unicode = pdf.make_stream(
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap 1 begincodespacerange <00> <FF> endcodespacerange 1 beginbfchar <01> <0041> endbfchar endcmap CMapName currentdict /CMap defineresource pop end end"
    )

    font_obj = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/TrueType"),
                "/BaseFont": pikepdf.Name("/MyFont"),
                "/FirstChar": 1,
                "/LastChar": 2,
                "/Widths": pikepdf.Array([250.0, 500.0]),
                "/FontDescriptor": font_descriptor,
                "/ToUnicode": to_unicode,
                "/Encoding": pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/Encoding"),
                        "/Differences": pikepdf.Array(
                            [1, pikepdf.Name("/A"), 2, pikepdf.Name("/B")]
                        ),
                    }
                ),
            }
        )
    )

    page.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font_obj})})

    return pdf, font_obj


# ---------------------------------------------------------------------------
# _get_embedded_font_details: CFF subtype detection + bad-stream except/continue
# ---------------------------------------------------------------------------


def test_get_embedded_font_details_cff_subtype():
    pdf = pikepdf.new()
    stream = pdf.make_stream(b"CFFDATA")
    stream.Subtype = pikepdf.Name("/Type1C")
    descriptor = pikepdf.Dictionary({"/FontFile3": stream})
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type0")})

    result = _get_embedded_font_details(font_obj, descriptor)
    assert result is not None
    ext, data, attr = result
    assert ext == "cff"
    assert attr == "FontFile3"
    assert data == b"CFFDATA"


def test_get_embedded_font_details_bad_stream_falls_through():
    # /FontFile is a Name, not a stream -> AttributeError on read_bytes() ->
    # caught -> continue -> loop exhausts -> returns None.
    descriptor = pikepdf.Dictionary({"/FontFile": pikepdf.Name("/NotAStream")})
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type1")})
    result = _get_embedded_font_details(font_obj, descriptor)
    assert result is None


def test_get_embedded_font_details_empty_descriptor():
    """Ensure _get_embedded_font_details returns None when structural descriptors are empty."""
    font_obj = pikepdf.Dictionary()
    descriptor = pikepdf.Dictionary({"/UnrecognizedKey": pikepdf.Name("/SomeValue")})
    assert _get_embedded_font_details(font_obj, descriptor) is None


def test_get_embedded_font_details_type3():
    """Ensure _get_embedded_font_details immediately returns None for Type 3 fonts."""
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type3")})
    descriptor = pikepdf.Dictionary()
    assert _get_embedded_font_details(font_obj, descriptor) is None


def test_get_embedded_font_details_falsy_descriptor():
    """Ensure _get_embedded_font_details returns None when descriptor is None (Line 461)."""
    font_obj = pikepdf.Dictionary()
    assert _get_embedded_font_details(font_obj, None) is None


def test_get_font_suffix():
    """Verify font file extension identification for special subtypes."""
    stream = MagicMock()
    stream.Subtype = "/Type1C"
    assert _get_font_suffix("/FontFile3", stream, b"", "otf") == "cff"

    stream.Subtype = "/CIDFontType0C"
    assert _get_font_suffix("/FontFile3", stream, b"", "otf") == "cff"

    assert _get_font_suffix("/FontFile2", stream, b"", "ttf") == "ttf"


def test_try_read_embedded_stream_exceptions():
    """Ensure _try_read_embedded_stream catches execution failures and returns None."""
    descriptor = pikepdf.Dictionary()
    assert _try_read_embedded_stream(descriptor, "/FontFile", "pfb", "FontFile", pikepdf) is None

    # Set a non-stream as the font file to trigger an exception
    descriptor["/FontFile"] = pikepdf.Name("/NotAStream")
    assert _try_read_embedded_stream(descriptor, "/FontFile", "pfb", "FontFile", pikepdf) is None


def test_get_embedded_font_details_success():
    """Successfully loops through and extracts embedded font details from a valid descriptor dictionary."""
    pdf = pikepdf.new()
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
    descriptor = pikepdf.Dictionary({"/FontFile2": pdf.make_stream(b"TrueTypeData")})
    res = _get_embedded_font_details(font_obj, descriptor)
    assert res == ("ttf", b"TrueTypeData", "FontFile2")


# ---------------------------------------------------------------------------
# _export_single_font_binary
# ---------------------------------------------------------------------------


def test_export_single_font_binary_success(tmp_path, sample_pdf_with_type1_font):
    """Guarantees the complete binary file extraction pipeline hits 100% execution."""
    pdf, font_obj = sample_pdf_with_type1_font
    descriptor = find_font_descriptor(font_obj)
    font_entry = {}

    result = _export_single_font_binary(
        "1_0", font_obj, descriptor, "MyFont", tmp_path, font_entry
    )
    assert result is not None
    assert result.name == "font_1_0_MyFont.pfb"
    assert font_entry["embedded_file"] == "font_1_0_MyFont.pfb"
    assert font_entry["embedded_format"] == "pfb"
    assert "binary_md5" in font_entry


def test_export_single_font_binary_oserror(monkeypatch, tmp_path, sample_pdf_with_type1_font):
    pdf, font_obj = sample_pdf_with_type1_font
    descriptor = find_font_descriptor(font_obj)
    font_entry = {}

    def mock_open(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", mock_open)
    result = _export_single_font_binary(
        "1_0", font_obj, descriptor, "MyFont", tmp_path, font_entry
    )
    assert result is None
    assert "embedded_file" not in font_entry


def test_export_single_font_binary_missing_details(tmp_path):
    """Ensure _export_single_font_binary returns None when embedded font details are missing (Line 487)."""
    font_obj = pikepdf.Dictionary()
    descriptor = pikepdf.Dictionary()
    font_entry = {}
    result = _export_single_font_binary(
        "1_0", font_obj, descriptor, "MyFont", tmp_path, font_entry
    )
    assert result is None


# ---------------------------------------------------------------------------
# _export_unified_sidecar
# ---------------------------------------------------------------------------


def test_resolve_font_widths_cid_to_gid_map_type0(monkeypatch):
    """Tests the Type0 branch of _resolve_font_widths_cid_to_gid_map to cover line 178."""
    import pdftl.operations.helpers.font_export_helpers as feh

    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type0")})

    def fake_extract(obj):
        return "Identity"

    monkeypatch.setattr(feh, "extract_cid_to_gid_map", fake_extract)

    res = feh._resolve_font_widths_cid_to_gid_map(font_obj)
    assert res == "Identity"


def test_assemble_unified_mappings_valid_state(tmp_path, monkeypatch):
    """Guarantees coverage for widths sync mapping and inner list populating blocks."""
    import pdftl.operations.helpers.font_export_helpers as feh

    # UPDATE: Add **kwargs to the lambda so it swallows the new cid_to_gid_map parameter safely
    monkeypatch.setattr(
        feh, "get_font_widths_from_file", lambda p, **kwargs: {"01": 250.0, "02": 500.0}
    )

    font_file = tmp_path / "dummy.ttf"
    font_file.write_bytes(b"dummy")

    pdf_widths = {"01": 250.0}
    tounicode_map = {"01": "A", "02": "B"}

    mappings = _assemble_unified_mappings(pdf_widths, tounicode_map, font_file)

    assert "01" in mappings
    assert mappings["01"]["unicode"] == "A"
    assert mappings["01"]["width"]["pdf"] == 250.0
    assert mappings["01"]["width"]["font"] == 250.0

    assert "02" in mappings
    assert mappings["02"]["unicode"] == "B"
    assert "pdf" not in mappings["02"]["width"]
    assert mappings["02"]["width"]["font"] == 500.0


def test_extract_tounicode_from_obj_exceptions():
    """Validates missing and malformed ToUnicode streams fallback quietly."""
    font_obj = pikepdf.Dictionary()
    font_entry = {}
    assert _extract_tounicode_from_obj(font_obj, font_entry, "1_0", pikepdf) == ({}, b"")
    assert font_entry.get("has_to_unicode") is not True

    # Use a bad stream to trigger exception
    font_obj["/ToUnicode"] = pikepdf.Name("/NotAStream")
    assert _extract_tounicode_from_obj(font_obj, font_entry, "1_0", pikepdf) == ({}, b"")


def test_export_unified_sidecar_bad_tounicode_stream(tmp_path, sample_pdf_with_fonts):
    pdf, font_obj = sample_pdf_with_fonts
    font_obj["/ToUnicode"] = pikepdf.Name("/NotAStream")
    font_entry = {}
    # Should not raise; logs a warning and continues.
    _export_unified_sidecar("1_0", font_obj, "MyFont", tmp_path, "json", {}, None, font_entry)
    assert font_entry.get("has_to_unicode") is not True


def test_save_json_sidecar_oserror(tmp_path, monkeypatch):
    """Targets precise exception handling logic for JSON sidecar saves."""

    def mock_open(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", mock_open)
    font_entry = {}
    _save_json_sidecar("1_0", "Test", tmp_path, {}, {}, font_entry)
    assert "sidecar_json_file" not in font_entry


def test_save_ps_sidecar_oserror(tmp_path, monkeypatch):
    """Targets precise exception handling logic for PostScript CMap sidecar saves."""

    def mock_open(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", mock_open)
    font_entry = {}
    _save_ps_sidecar("1_0", "Test", tmp_path, b"test", font_entry)
    assert "tounicode_ps_file" not in font_entry


def test_save_cid_to_gid_sidecar_oserror(tmp_path, monkeypatch):
    """Targets precise exception handling logic for CIDToGIDMap sidecar saves."""
    import pdftl.operations.helpers.font_export_helpers as feh

    def mock_open(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", mock_open)
    font_entry = {}
    feh._save_cid_to_gid_sidecar("1_0", "Test", tmp_path, {0: 5}, font_entry)
    assert "cid_to_gid_map_file" not in font_entry
    assert "cid_to_gid_map_md5" not in font_entry


# ---------------------------------------------------------------------------
# _extract_differences_list: /Encoding present but no /Differences key
# ---------------------------------------------------------------------------


def test_extract_differences_no_differences_key():
    font = pikepdf.Dictionary(
        {"/Encoding": pikepdf.Dictionary({"/Type": pikepdf.Name("/Encoding")})}
    )
    assert _extract_differences_list(font) is None


def test_extract_differences_encoding_is_name():
    # /Encoding may be a bare Name (e.g. /WinAnsiEncoding) rather than a
    # Dictionary when the font has no per-glyph differences. Indexing
    # "/Differences" on a Name previously raised ValueError instead of
    # being treated as "no differences present".
    font = pikepdf.Dictionary({"/Encoding": pikepdf.Name("/WinAnsiEncoding")})
    assert _extract_differences_list(font) is None


def test_extract_differences_list_branches():
    """Thoroughly checks the structural encoding fallback conditions."""
    from pdftl.operations.helpers.font_export_helpers import _extract_differences_list

    font_obj = pikepdf.Dictionary()
    assert _extract_differences_list(font_obj) is None

    font_obj["/Encoding"] = pikepdf.Dictionary()
    assert _extract_differences_list(font_obj) is None

    font_obj["/Encoding"]["/Differences"] = pikepdf.Array([1, pikepdf.Name("/A")])
    # Elements coercable to int parse to int, names stringify directly
    assert _extract_differences_list(font_obj) == [1, "/A"]


# ---------------------------------------------------------------------------
# _extract_base_encoding: /Encoding as a bare Name vs a Dictionary with /BaseEncoding
# ---------------------------------------------------------------------------


def test_extract_base_encoding_no_encoding_key():
    font = pikepdf.Dictionary()
    assert _extract_base_encoding(font) is None


def test_extract_base_encoding_bare_name_recognized():
    font = pikepdf.Dictionary({"/Encoding": pikepdf.Name("/WinAnsiEncoding")})
    assert _extract_base_encoding(font) == "WinAnsiEncoding"


def test_extract_base_encoding_bare_name_unrecognized():
    # A bare /Encoding name that isn't one of the three known base encodings
    # (e.g. a custom PDF producer quirk) is not something we can resolve.
    font = pikepdf.Dictionary({"/Encoding": pikepdf.Name("/SomeCustomEncoding")})
    assert _extract_base_encoding(font) is None


def test_extract_base_encoding_dictionary_with_base_encoding():
    font = pikepdf.Dictionary(
        {"/Encoding": pikepdf.Dictionary({"/BaseEncoding": pikepdf.Name("/MacRomanEncoding")})}
    )
    assert _extract_base_encoding(font) == "MacRomanEncoding"


def test_extract_base_encoding_dictionary_without_base_encoding():
    # /Encoding is a Dictionary (e.g. carrying /Differences) but declares no
    # /BaseEncoding of its own; the font's built-in encoding applies instead.
    font = pikepdf.Dictionary(
        {"/Encoding": pikepdf.Dictionary({"/Differences": pikepdf.Array([1, pikepdf.Name("/A")])})}
    )
    assert _extract_base_encoding(font) is None


def test_extract_base_encoding_dictionary_with_unrecognized_base_encoding():
    font = pikepdf.Dictionary(
        {"/Encoding": pikepdf.Dictionary({"/BaseEncoding": pikepdf.Name("/CustomEncoding")})}
    )
    assert _extract_base_encoding(font) is None


# ---------------------------------------------------------------------------
# Manifest Extraction Integrations
# ---------------------------------------------------------------------------


def test_build_manifest_integration(tmp_path, sample_pdf_with_fonts):
    """Guarantees the core manifest extraction pipeline loops optimally through fonts."""
    manifest = build_manifest(sample_pdf_with_fonts[0], [1], tmp_path, "json")
    assert "fonts" in manifest
    assert len(manifest["fonts"]) > 0


def test_build_manifest_all_modes(tmp_path, sample_pdf_with_fonts):
    """Verifies that the `tounicode=all` mode executes sidecar writes correctly."""
    manifest = build_manifest(sample_pdf_with_fonts[0], [1], tmp_path, "all")
    assert "fonts" in manifest


def test_build_manifest_captures_base_encoding(tmp_path):
    """Confirms a Simple font's /Encoding /BaseEncoding surfaces in the manifest
    font entry, so import-side patch/squash can resolve glyphs correctly."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page()

    font_obj = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/TrueType"),
                "/BaseFont": pikepdf.Name("/MyFont"),
                "/FirstChar": 1,
                "/LastChar": 1,
                "/Widths": pikepdf.Array([250.0]),
                "/Encoding": pikepdf.Name("/WinAnsiEncoding"),
            }
        )
    )
    page.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font_obj})})

    manifest = build_manifest(pdf, [1], tmp_path, "json")
    font_entry = next(iter(manifest["fonts"].values()))
    assert font_entry["base_encoding"] == "WinAnsiEncoding"


def test_build_manifest_no_base_encoding_key_when_absent(tmp_path, sample_pdf_with_type1_font):
    """When a font declares no /BaseEncoding, the manifest entry simply omits
    the key rather than writing a null/empty placeholder."""
    pdf, font_obj = sample_pdf_with_type1_font
    manifest = build_manifest(pdf, [1], tmp_path, "json")
    font_entry = next(iter(manifest["fonts"].values()))
    assert "base_encoding" not in font_entry


def test_build_manifest_type3(tmp_path):
    """Tests Type 3 interceptor routing within the manifest builder."""
    pdf = pikepdf.new()
    glyph_stream_bytes = (
        b"d1 250 0 0 -100 250 800\nBI /W 2 /H 2 /BPC 1 /CS /DeviceGray ID\n\x00\x00EI"
    )
    charprocs_dict = pikepdf.Dictionary({"/A": pikepdf.Stream(pdf, glyph_stream_bytes)})
    font_obj = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type3"),
                "/FontMatrix": pikepdf.Array([0.001, 0, 0, 0.001, 0, 0]),
                "/CharProcs": charprocs_dict,
            }
        )
    )
    page = pdf.add_blank_page()
    page.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font_obj})})

    manifest = build_manifest(pdf, [1], tmp_path, "json")
    assert "fonts" in manifest


def _make_minimal_opentype_bytes(table_tags: list[bytes]) -> bytes:
    """Builds a minimal, valid-enough sfnt header + table directory for sniffing."""
    header = b"OTTO" + len(table_tags).to_bytes(2, "big") + b"\x00\x00\x00\x00\x00\x00"
    directory = b""
    for tag in table_tags:
        directory += tag + b"\x00" * 12  # checksum/offset/length not needed for the sniff
    return header + directory


def test_sniff_is_cff2_detects_cff2_table():
    font_bytes = _make_minimal_opentype_bytes([b"CFF2", b"head"])
    assert _sniff_is_cff2(font_bytes) is True


def test_sniff_is_cff2_absent_for_classic_cff():
    font_bytes = _make_minimal_opentype_bytes([b"CFF ", b"head"])
    assert _sniff_is_cff2(font_bytes) is False


def test_sniff_is_cff2_too_short_header():
    assert _sniff_is_cff2(b"short") is False


def test_sniff_is_cff2_truncated_directory():
    header = b"OTTO" + (5).to_bytes(2, "big") + b"\x00" * 6
    assert _sniff_is_cff2(header) is False  # claims 5 tables but has none


def test_get_font_suffix_cff2_opentype():
    stream = MagicMock()
    stream.Subtype = "/OpenType"
    font_bytes = _make_minimal_opentype_bytes([b"CFF2"])
    assert _get_font_suffix("/FontFile3", stream, font_bytes, "otf") == "cff2"


def test_get_font_suffix_classic_opentype_stays_default():
    stream = MagicMock()
    stream.Subtype = "/OpenType"
    font_bytes = _make_minimal_opentype_bytes([b"CFF "])
    assert _get_font_suffix("/FontFile3", stream, font_bytes, "otf") == "otf"


def test_get_font_suffix_type1c_unaffected_by_signature_change():
    stream = MagicMock()
    stream.Subtype = "/Type1C"
    assert _get_font_suffix("/FontFile3", stream, b"", "otf") == "cff"


def test_build_manifest_unembedded_core14_font_sets_is_embedded_false(tmp_path):
    """A Simple font with no /FontDescriptor at all (e.g. an unembedded
    Core 14 font like Helvetica) still produces a usable manifest entry:
    is_embedded is explicitly False, and embedded_file/embedded_format are
    explicitly null rather than merely absent, so manual width edits stay
    possible downstream in import_fonts."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page()

    font_obj = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
                "/FirstChar": 32,
                "/LastChar": 32,
                "/Widths": pikepdf.Array([278.0]),
            }
        )
    )
    page.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font_obj})})

    manifest = build_manifest(pdf, [1], tmp_path, "json")
    font_entry = next(iter(manifest["fonts"].values()))

    assert font_entry["is_embedded"] is False
    assert font_entry["embedded_file"] is None
    assert font_entry["embedded_format"] is None


def test_build_manifest_embedded_font_sets_is_embedded_true(tmp_path, sample_pdf_with_type1_font):
    """A font with an embedded /FontFile stream is flagged is_embedded True
    and carries its real embedded_file/embedded_format values."""
    pdf, font_obj = sample_pdf_with_type1_font
    manifest = build_manifest(pdf, [1], tmp_path, "json")
    font_entry = next(iter(manifest["fonts"].values()))

    obj_id, gen_id = font_obj.objgen
    expected_filename = f"font_{int(obj_id)}_0_MyType1Font.pfb"

    assert font_entry["is_embedded"] is True
    assert font_entry["embedded_file"] == expected_filename
    assert font_entry["embedded_format"] == "pfb"


def test_build_manifest_type3_font_is_embedded_true(tmp_path):
    """Type 3 fonts carry their glyph programs inline via /CharProcs, so
    they are always considered embedded even though they have no
    FontFile/FontFile2/FontFile3 stream to point at."""
    pdf = pikepdf.new()
    glyph_stream_bytes = (
        b"d1 250 0 0 -100 250 800\nBI /W 2 /H 2 /BPC 1 /CS /DeviceGray ID\n\x00\x00EI"
    )
    charprocs_dict = pikepdf.Dictionary({"/A": pikepdf.Stream(pdf, glyph_stream_bytes)})
    font_obj = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type3"),
                "/FontMatrix": pikepdf.Array([0.001, 0, 0, 0.001, 0, 0]),
                "/CharProcs": charprocs_dict,
            }
        )
    )
    page = pdf.add_blank_page()
    page.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font_obj})})

    manifest = build_manifest(pdf, [1], tmp_path, "json")
    font_entry = next(iter(manifest["fonts"].values()))
    assert font_entry["is_embedded"] is True


def test_build_manifest_identity_h_type0_font_records_encoding_cmap(tmp_path):
    """A Type0 font declaring /Encoding /Identity-H with no /ToUnicode and
    no /W array still produces a manifest entry recording that fact,
    rather than one that's silently empty and uninformative."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page()

    cid_font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/CIDFontType2")})
    font_obj = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type0"),
                "/BaseFont": pikepdf.Name("/MyCIDFont"),
                "/Encoding": pikepdf.Name("/Identity-H"),
                "/DescendantFonts": pikepdf.Array([cid_font]),
            }
        )
    )
    page.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font_obj})})

    manifest = build_manifest(pdf, [1], tmp_path, "json")
    font_entry = next(iter(manifest["fonts"].values()))
    assert font_entry["encoding_cmap"] == "Identity-H"


def test_build_manifest_non_identity_type0_font_omits_encoding_cmap(tmp_path):
    """A Type0 font using a non-identity predefined CMap simply omits the
    key, matching how 'differences'/'base_encoding' are only written when
    applicable."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page()

    cid_font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/CIDFontType0")})
    font_obj = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type0"),
                "/BaseFont": pikepdf.Name("/MyCJKFont"),
                "/Encoding": pikepdf.Name("/UniGB-UCS2-H"),
                "/DescendantFonts": pikepdf.Array([cid_font]),
            }
        )
    )
    page.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font_obj})})

    manifest = build_manifest(pdf, [1], tmp_path, "json")
    font_entry = next(iter(manifest["fonts"].values()))
    assert "encoding_cmap" not in font_entry


def test_save_cid_to_gid_sidecar_writes_explicit_map(tmp_path):
    from pdftl.operations.helpers.font_export_helpers import _save_cid_to_gid_sidecar
    import json

    font_entry = {}
    _save_cid_to_gid_sidecar("1_0", "TestFont", tmp_path, {0: 5, 10: 20}, font_entry)

    assert font_entry["cid_to_gid_map"] == "explicit"
    assert font_entry["cid_to_gid_map_file"] == "font_1_0_TestFont.cid2gid.json"
    assert "cid_to_gid_map_md5" in font_entry

    with open(tmp_path / font_entry["cid_to_gid_map_file"]) as f:
        data = json.load(f)
        assert data["cid_to_gid"] == {"0000": "0005", "000A": "0014"}


def test_make_font_entry_explicit_cid_to_gid(tmp_path, monkeypatch):
    from pdftl.operations.helpers.font_export_helpers import _make_font_entry
    import pdftl.operations.helpers.font_export_helpers as feh
    import pikepdf

    monkeypatch.setattr(feh, "extract_cid_to_gid_map", lambda obj: {0: 5})
    font_obj = pikepdf.Dictionary()

    font_entry = _make_font_entry(font_obj, 1, 0, "1_0", "/TestFont", "TestFont", tmp_path)
    assert font_entry["cid_to_gid_map"] == "explicit"
