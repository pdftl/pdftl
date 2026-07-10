# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/test_embed_fonts.py

"""
Unit and integration tests for the `embed_fonts` operation.
"""

from __future__ import annotations

import sys
import logging
import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import pikepdf

from pdftl.operations.embed_fonts import (
    embed_fonts,
    _extract_ps_name,
    _rename_font_objects,
    _attach_stream_to_descriptor,
)

# Add fixtures directory to import local programmatic font builders safely
sys.path.insert(0, str(Path(__file__).parent.parent / "fonts" / "fixtures"))
from font_fixture_builder import SQUARE_500, build_truetype_bytes  # noqa: E402
from type1_fixture_builder import build_type1_bytes  # noqa: E402


def test_embed_fonts_with_existing_descriptor(tmp_path):
    """Verifies stream injection behaves correctly when a FontDescriptor already exists."""
    pdf = pikepdf.Pdf.new()
    desc = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name("/Helvetica"),
            }
        )
    )
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/TrueType"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
                "/FontDescriptor": desc,
            }
        )
    )
    pdf.add_blank_page().Resources = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": font})}
    )

    ttf_bytes = build_truetype_bytes({"A": SQUARE_500})
    fake_sys_path = tmp_path / "Helvetica.ttf"
    fake_sys_path.write_bytes(ttf_bytes)

    with patch(
        "pdftl.operations.embed_fonts.resolve_system_font_path", return_value=str(fake_sys_path)
    ):
        res = embed_fonts(pdf, [])

    assert res.success is True
    assert "/FontFile2" in desc
    assert int(desc["/FontFile2"].Length1) == len(ttf_bytes)


def test_embed_fonts_missing_descriptor_creates_new(tmp_path):
    """Verifies that an absent descriptor correctly triggers dynamic generation from the TTF."""
    pdf = pikepdf.Pdf.new()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/TrueType"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
            }
        )
    )
    pdf.add_blank_page().Resources = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": font})}
    )

    ttf_bytes = build_truetype_bytes({"A": SQUARE_500})
    fake_sys_path = tmp_path / "Helvetica.ttf"
    fake_sys_path.write_bytes(ttf_bytes)

    with patch(
        "pdftl.operations.embed_fonts.resolve_system_font_path", return_value=str(fake_sys_path)
    ):
        res = embed_fonts(pdf, [])

    assert res.success is True
    assert "/FontDescriptor" in font
    desc = font["/FontDescriptor"]
    assert "/FontFile2" in desc
    assert "/Ascent" in desc
    assert "/CapHeight" in desc


def test_embed_fonts_type0_attaches_to_descendant(tmp_path):
    """Verifies that composite Type0 fonts correctly route descriptor attachment
    to their descendant CIDFont dictionary, per the PDF specification."""
    pdf = pikepdf.Pdf.new()
    cid_font = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/CIDFontType2"),
            "/BaseFont": pikepdf.Name("/Arial"),
        }
    )
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type0"),
                "/BaseFont": pikepdf.Name("/Arial"),
                "/DescendantFonts": pikepdf.Array([cid_font]),
            }
        )
    )
    pdf.add_blank_page().Resources = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": font})}
    )

    ttf_bytes = build_truetype_bytes({"A": SQUARE_500})
    fake_sys_path = tmp_path / "Arial.ttf"
    fake_sys_path.write_bytes(ttf_bytes)

    with patch(
        "pdftl.operations.embed_fonts.resolve_system_font_path", return_value=str(fake_sys_path)
    ):
        res = embed_fonts(pdf, [])

    assert res.success is True
    assert "/FontDescriptor" in cid_font
    desc = cid_font["/FontDescriptor"]
    assert "/FontFile2" in desc
    assert "/FontDescriptor" not in font


def test_embed_fonts_handles_otf_extension(tmp_path):
    """Verifies OTF streams are embedded with the correct /FontFile3 layout."""
    pdf = pikepdf.Pdf.new()
    desc = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name("/Helvetica"),
            }
        )
    )
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/TrueType"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
                "/FontDescriptor": desc,
            }
        )
    )
    pdf.add_blank_page().Resources = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": font})}
    )

    fake_sys_path = tmp_path / "Helvetica.otf"
    fake_sys_path.write_bytes(b"dummy_otf_data")

    with patch(
        "pdftl.operations.embed_fonts.resolve_system_font_path", return_value=str(fake_sys_path)
    ):
        res = embed_fonts(pdf, [])

    assert res.success is True
    assert "/FontFile3" in desc
    assert desc["/FontFile3"].Subtype == pikepdf.Name("/OpenType")


def test_embed_fonts_handles_pfb_extension_with_existing_descriptor(tmp_path, monkeypatch):
    """Verifies PFB stream layout injections correctly populate segmented length metadata."""
    import pdftl.operations.helpers.font_import_helpers as fih

    def mock_update_lengths(stream, font_bytes):
        stream.Length1 = 10
        stream.Length2 = 20
        stream.Length3 = 30

    monkeypatch.setattr(fih, "_update_type1_length_fields", mock_update_lengths)

    pdf = pikepdf.Pdf.new()
    desc = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name("/Times"),
            }
        )
    )
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/Times"),
                "/FontDescriptor": desc,
            }
        )
    )
    pdf.add_blank_page().Resources = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": font})}
    )

    fake_sys_path = tmp_path / "Times.pfb"
    fake_sys_path.write_bytes(b"dummy_pfb_data")

    with patch(
        "pdftl.operations.embed_fonts.resolve_system_font_path", return_value=str(fake_sys_path)
    ):
        res = embed_fonts(pdf, [])

    assert res.success is True
    assert "/FontFile" in desc
    assert int(desc["/FontFile"].Length2) == 20


def test_embed_fonts_unresolved_system_font_is_skipped(tmp_path):
    """Validates that a font is cleanly skipped if no system equivalent can be located."""
    pdf = pikepdf.Pdf.new()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/TrueType"),
                "/BaseFont": pikepdf.Name("/UnknownFont"),
            }
        )
    )
    pdf.add_blank_page().Resources = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": font})}
    )

    with patch("pdftl.operations.embed_fonts.resolve_system_font_path", return_value=None):
        res = embed_fonts(pdf, [])

    assert res.success is True
    assert "/FontDescriptor" not in font


def test_embed_fonts_read_oserror_is_caught(tmp_path, monkeypatch):
    """Ensures permissions or system I/O read errors degrade safely instead of crashing."""
    pdf = pikepdf.Pdf.new()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/TrueType"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
            }
        )
    )
    pdf.add_blank_page().Resources = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": font})}
    )

    fake_sys_path = tmp_path / "Helvetica.ttf"
    fake_sys_path.write_bytes(b"dummy")

    def mock_open(*args, **kwargs):
        raise OSError("Access denied")

    monkeypatch.setattr("builtins.open", mock_open)

    with patch(
        "pdftl.operations.embed_fonts.resolve_system_font_path", return_value=str(fake_sys_path)
    ):
        res = embed_fonts(pdf, [])

    assert res.success is True
    assert "/FontDescriptor" not in font


def test_embed_fonts_create_descriptor_unsupported_extension(tmp_path):
    """Ensures dynamic descriptor generation rejects unrecognized formats."""
    pdf = pikepdf.Pdf.new()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
            }
        )
    )
    pdf.add_blank_page().Resources = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": font})}
    )

    fake_sys_path = tmp_path / "Helvetica.unknown_extension"
    fake_sys_path.write_bytes(b"dummy")

    with patch(
        "pdftl.operations.embed_fonts.resolve_system_font_path", return_value=str(fake_sys_path)
    ):
        res = embed_fonts(pdf, [])

    assert res.success is True
    assert "/FontDescriptor" not in font


def test_embed_fonts_create_descriptor_ttlib_error_is_caught(tmp_path):
    """Ensures dynamic descriptor generation avoids crashing on corrupted TTF byte blocks."""
    pdf = pikepdf.Pdf.new()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/TrueType"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
            }
        )
    )
    pdf.add_blank_page().Resources = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": font})}
    )

    fake_sys_path = tmp_path / "Helvetica.ttf"
    fake_sys_path.write_bytes(b"not_a_real_ttf_payload_so_it_fails")

    with patch(
        "pdftl.operations.embed_fonts.resolve_system_font_path", return_value=str(fake_sys_path)
    ):
        res = embed_fonts(pdf, [])

    assert res.success is True
    assert "/FontDescriptor" not in font


def test_embed_fonts_custom_dirs_and_nosys_parsed_correctly(tmp_path):
    """Verifies arguments fontdir and nosys are correctly routed to the locator."""
    pdf = pikepdf.Pdf.new()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/TrueType"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
            }
        )
    )
    pdf.add_blank_page().Resources = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": font})}
    )

    with patch(
        "pdftl.operations.embed_fonts.resolve_system_font_path", return_value=None
    ) as mock_resolve:
        res = embed_fonts(pdf, ["fontdir", "/fake/dir", "nosys"])

    assert res.success is True
    mock_resolve.assert_called_once_with("Helvetica", custom_dirs=["/fake/dir"], use_system=False)


def test_embed_fonts_warns_on_unresolved_font(tmp_path, caplog):
    """Validates that a warning is logged when a font binary cannot be found."""
    pdf = pikepdf.Pdf.new()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/TrueType"),
                "/BaseFont": pikepdf.Name("/UnknownFont"),
            }
        )
    )
    pdf.add_blank_page().Resources = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": font})}
    )

    with patch("pdftl.operations.embed_fonts.resolve_system_font_path", return_value=None):
        with caplog.at_level(logging.WARNING):
            res = embed_fonts(pdf, [])

    assert res.success is True
    assert any("Could not locate font binary" in rec.message for rec in caplog.records)


def test_embed_fonts_rename_updates_structures(tmp_path):
    """Verifies that the rename flag successfully matches the PDF's internal naming structs
    to the actual resolved PostScript name fallback of the builder output."""
    pdf = pikepdf.Pdf.new()
    desc = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name("/Helvetica"),
            }
        )
    )
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/TrueType"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
                "/FontDescriptor": desc,
            }
        )
    )
    pdf.add_blank_page().Resources = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": font})}
    )

    ttf_bytes = build_truetype_bytes({"A": SQUARE_500}, font_name="LiberationSans")
    fake_sys_path = tmp_path / "Liberation.ttf"
    fake_sys_path.write_bytes(ttf_bytes)

    with patch(
        "pdftl.operations.embed_fonts.resolve_system_font_path", return_value=str(fake_sys_path)
    ):
        res = embed_fonts(pdf, ["rename"])

    assert res.success is True
    assert str(font["/BaseFont"]) == "/EmbeddedFont"
    assert str(desc["/FontName"]) == "/EmbeddedFont"


def test_embed_fonts_type1_dynamic_descriptor_generation(tmp_path):
    """Verifies dynamic descriptor creation for Classic Type 1 (.pfb) structures."""
    pdf = pikepdf.Pdf.new()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/Times"),
            }
        )
    )
    pdf.add_blank_page().Resources = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": font})}
    )

    pfb_bytes = build_type1_bytes({"A": (500, [0, 0, "rmoveto", 500, 0, "rlineto"])})
    fake_sys_path = tmp_path / "Times.pfb"
    fake_sys_path.write_bytes(pfb_bytes)

    with patch(
        "pdftl.operations.embed_fonts.resolve_system_font_path", return_value=str(fake_sys_path)
    ):
        res = embed_fonts(pdf, [])

    assert res.success is True
    assert "/FontDescriptor" in font
    desc = font["/FontDescriptor"]
    assert "/FontFile" in desc
    assert str(desc["/FontName"]) == "/PdftlTestFont"


def test_embed_fonts_unsupported_binary_format_descriptor_bail(tmp_path):
    """Validates that a fallback unreadable format skips descriptor construction cleanly."""
    pdf = pikepdf.Pdf.new()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/TrueType"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
            }
        )
    )
    pdf.add_blank_page().Resources = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": font})}
    )

    fake_sys_path = tmp_path / "Helvetica.bin"
    fake_sys_path.write_bytes(b"generic binary data")

    with patch(
        "pdftl.operations.embed_fonts.resolve_system_font_path", return_value=str(fake_sys_path)
    ):
        res = embed_fonts(pdf, [])

    assert res.success is True
    assert "/FontDescriptor" not in font


def test_embed_fonts_type1_parse_failure_bails(tmp_path):
    """Verifies dynamic descriptor creation for Type 1 (.pfb) gracefully fails on corrupted byte payloads."""
    pdf = pikepdf.Pdf.new()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
            }
        )
    )
    pdf.add_blank_page().Resources = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": font})}
    )

    fake_sys_path = tmp_path / "Helvetica.pfb"
    fake_sys_path.write_bytes(b"definitely corrupt pfb data header")

    with patch(
        "pdftl.operations.embed_fonts.resolve_system_font_path", return_value=str(fake_sys_path)
    ):
        res = embed_fonts(pdf, [])

    assert res.success is True
    assert "/FontDescriptor" not in font


def test_extract_ps_name_with_invalid_extension():
    """Verify that _extract_ps_name returns None when extension matches no signature block."""
    assert _extract_ps_name("dummy_path.bin", ".bin") is None


# ============================================================================
# Core Integration Tests and Fallbacks
# ============================================================================


def test_extract_ps_name_handles_parsing_exceptions(tmp_path):
    """Ensures exceptions during name parsing on TrueType or PostScript formats
    gracefully fallback to returning None without propagating."""
    # Exception inside TTFont section
    broken_ttf = tmp_path / "corrupt.ttf"
    broken_ttf.write_bytes(b"invalid format data")
    assert _extract_ps_name(str(broken_ttf), ".ttf") is None

    # Exception inside T1Font section
    broken_pfb = tmp_path / "corrupt.pfb"
    broken_pfb.write_bytes(b"corrupt pfb block data")
    assert _extract_ps_name(str(broken_pfb), ".pfb") is None


def test_rename_font_objects_handles_malformed_composite_descendant():
    """Verifies that attempting a rename on composite structures with absent
    descendants does not crash and handles TypeError or IndexErrors silently."""
    # Empty descendant array to trigger IndexError
    font = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type0"),
            "/BaseFont": pikepdf.Name("/Helvetica"),
            "/DescendantFonts": pikepdf.Array([]),
        }
    )
    desc = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/FontDescriptor"),
            "/FontName": pikepdf.Name("/Helvetica"),
        }
    )

    from pdftl.operations.embed_fonts import _rename_font_objects

    _rename_font_objects(font, desc, "SubstitutedFont", pikepdf)
    assert font["/BaseFont"] == pikepdf.Name("/SubstitutedFont")
    assert desc["/FontName"] == pikepdf.Name("/SubstitutedFont")


def test_embed_fonts_unrecognized_located_extension_graceful_fallback(tmp_path):
    """Ensures file paths containing atypical extension formats are treated with a standard
    OpenType fallback layout block during injection."""
    pdf = pikepdf.Pdf.new()
    desc = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name("/Helvetica"),
            }
        )
    )
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/TrueType"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
                "/FontDescriptor": desc,
            }
        )
    )
    pdf.add_blank_page().Resources = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": font})}
    )

    fake_sys_path = tmp_path / "Helvetica.unknown_extension"
    fake_sys_path.write_bytes(b"arbitrary font bytes")

    with patch(
        "pdftl.operations.embed_fonts.resolve_system_font_path", return_value=str(fake_sys_path)
    ):
        res = embed_fonts(pdf, [])

    assert res.success is True
    assert "/FontFile3" in desc
    assert desc["/FontFile3"].Subtype == pikepdf.Name("/OpenType")


def test_create_descriptor_handles_type1_segmented_pfb_directly(tmp_path):
    """Ensures primary segment parsing inside descriptor creation successfully utilizes
    standard T1Font parse paths when the binary structure matches valid signatures."""
    pdf = pikepdf.Pdf.new()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
            }
        )
    )
    pdf.add_blank_page().Resources = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": font})}
    )

    # Prepare mock matching segmented Type 1 properties for success branch
    mock_t1 = MagicMock()
    mock_t1.font = {
        "FontInfo": {"isFixedPitch": False, "ItalicAngle": 0.0},
        "Private": {"CapHeight": 700.0},
        "FontName": "PredefinedFontName",
        "FontBBox": [0, 0, 1000, 1000],
    }

    fake_sys_path = tmp_path / "Helvetica.pfb"
    fake_sys_path.write_bytes(b"dummy")

    with (
        patch("fontTools.t1Lib.T1Font", return_value=mock_t1),
        patch(
            "pdftl.operations.embed_fonts.resolve_system_font_path",
            return_value=str(fake_sys_path),
        ),
    ):
        res = embed_fonts(pdf, [])

    assert res.success is True
    assert "/FontDescriptor" in font
    desc = font["/FontDescriptor"]
    assert desc["/FontName"] == pikepdf.Name("/PredefinedFontName")
    assert "/FontFile" in desc


def test_create_descriptor_malformed_type0_descendants_falls_back_to_parent(tmp_path):
    """Verifies that empty descendant list arrays in composite structures result in descriptor
    placement being gracefully written to the parent Type0 dictionary."""
    pdf = pikepdf.Pdf.new()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type0"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
                "/DescendantFonts": pikepdf.Array([]),
            }
        )
    )
    pdf.add_blank_page().Resources = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": font})}
    )

    ttf_bytes = build_truetype_bytes({"A": SQUARE_500})
    fake_sys_path = tmp_path / "Helvetica.ttf"
    fake_sys_path.write_bytes(ttf_bytes)

    with patch(
        "pdftl.operations.embed_fonts.resolve_system_font_path", return_value=str(fake_sys_path)
    ):
        res = embed_fonts(pdf, [])

    assert res.success is True
    # Attached straight to the parent font object because child elements were missing
    assert "/FontDescriptor" in font


def test_embed_fonts_clears_specs_with_config_args(tmp_path):
    """Ensures page-range selectors can be resolved in coordination with multiple inline config options."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()

    with patch(
        "pdftl.operations.embed_fonts.get_target_pages", return_value=[1]
    ) as mock_get_pages:
        embed_fonts(pdf, ["1", "fontdir", str(tmp_path), "rename", "nosys"])
        mock_get_pages.assert_called_once_with(pdf, ["1"])


def test_embed_fonts_deduplicates_processing_of_repeated_instances(tmp_path):
    """Validates that a shared font instance across multiple page contexts is parsed only once."""
    pdf = pikepdf.Pdf.new()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/TrueType"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
            }
        )
    )
    pdf.add_blank_page().Resources = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": font})}
    )
    pdf.add_blank_page().Resources = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": font})}
    )

    ttf_bytes = build_truetype_bytes({"A": SQUARE_500})
    fake_sys_path = tmp_path / "Helvetica.ttf"
    fake_sys_path.write_bytes(ttf_bytes)

    with patch(
        "pdftl.operations.embed_fonts.resolve_system_font_path", return_value=str(fake_sys_path)
    ) as mock_resolve:
        res = embed_fonts(pdf, [])

    assert res.success is True
    # Asserts deduplication avoided checking the same indirect object multiple times
    assert mock_resolve.call_count == 1


def test_embed_fonts_skips_unparseable_or_preembedded_assets():
    """Validates standard early continuation if the checked asset is already embedded or completely invalid."""
    pdf = pikepdf.Pdf.new()
    desc = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name("/Helvetica"),
                "/FontFile2": pdf.make_stream(b"data"),
            }
        )
    )
    embedded_font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/TrueType"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
                "/FontDescriptor": desc,
            }
        )
    )
    malformed_obj = pdf.make_indirect(pikepdf.Array([]))  # Not a dictionary, lacks .get()

    pdf.add_blank_page().Resources = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": embedded_font, "/F2": malformed_obj})}
    )

    with patch("pdftl.operations.embed_fonts.resolve_system_font_path") as mock_resolve:
        res = embed_fonts(pdf, [])

    assert res.success is True
    mock_resolve.assert_not_called()


def test_embed_fonts_skips_null_or_placeholder_font_names():
    """Ensures font instances specifying an empty or none BaseFont name loop natively to skip checks."""
    pdf = pikepdf.Pdf.new()
    font_missing_name = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/TrueType"),
            }
        )
    )
    font_none_name = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/TrueType"),
                "/BaseFont": pikepdf.Name("/[none]"),
            }
        )
    )

    pdf.add_blank_page().Resources = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": font_missing_name, "/F2": font_none_name})}
    )

    with patch("pdftl.operations.embed_fonts.resolve_system_font_path") as mock_resolve:
        res = embed_fonts(pdf, [])

    assert res.success is True
    mock_resolve.assert_not_called()


def test_extract_ps_name_with_standard_type1_font(tmp_path):
    """Ensures PostScript font names are successfully extracted from standard Type 1
    font files containing valid PostScript headers.
    """
    pfb_bytes = build_type1_bytes({"A": (500, [0, 0, "rmoveto", 500, 0, "rlineto"])})
    tmp_file = tmp_path / "Times.pfb"
    tmp_file.write_bytes(pfb_bytes)

    ps_name = _extract_ps_name(str(tmp_file), ".pfb")
    assert ps_name == "PdftlTestFont"


def test_extract_ps_name_with_nonstandard_type1_font(monkeypatch):
    """Ensures the parsing fallback mode handles malformed Type 1 structures
    by falling back to parsing with 'OTHER' configuration settings when a T1Error occurs.
    """
    from fontTools.t1Lib import T1Error

    call_count = 0

    class MockT1Font:
        def __init__(self, path, kind=None):
            self.path = path
            self.kind = kind
            self.font = {"FontName": "FallbackT1Font"}

        def parse(self):
            nonlocal call_count
            call_count += 1
            if self.kind is None:
                raise T1Error("Simulated standard parsing exception")
            return

    monkeypatch.setattr("fontTools.t1Lib.T1Font", MockT1Font)

    ps_name = _extract_ps_name("dummy_path.pfb", ".pfb")
    assert ps_name == "FallbackT1Font"
    assert call_count == 2


def test_rename_font_objects_updates_descendant_fonts_for_type0():
    """Ensures descendant composite fonts within Type 0 font dictionaries
    also receive correct BaseFont name remapping during font rename operations.
    """

    class CustomFontDict(dict):
        @property
        def DescendantFonts(self):
            return self["/DescendantFonts"]

    # Wrap these in standard dict literals with proper PDF slash-keys:
    font_obj = CustomFontDict(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type0"),
            "/BaseFont": pikepdf.Name("/OldBaseFontName"),
        }
    )

    descendant = CustomFontDict(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/CIDFontType2"),
            "/BaseFont": pikepdf.Name("/OldBaseFontName"),
        }
    )

    font_obj["/DescendantFonts"] = [descendant]

    descriptor = pikepdf.Dictionary(
        Type=pikepdf.Name("/FontDescriptor"), FontName=pikepdf.Name("/OldBaseFontName")
    )

    ps_name = "NewBaseFontName"
    expected_name = pikepdf.Name(f"/{ps_name}")

    _rename_font_objects(font_obj, descriptor, ps_name, pikepdf)

    assert font_obj["/BaseFont"] == expected_name
    assert font_obj["/DescendantFonts"][0]["/BaseFont"] == expected_name
    assert descendant["/BaseFont"] == expected_name


def test_attach_stream_to_descriptor_removes_stale_fontfile_keys():
    """Ensures unused FontFile entries (FontFile, FontFile2, FontFile3) are cleaned
    up from the descriptor to maintain compliance with ISO 32000-2 font guidelines.
    """
    pdf = pikepdf.Pdf.new()

    descriptor = pikepdf.Dictionary(
        Type=pikepdf.Name("/FontDescriptor"),
        FontName=pikepdf.Name("/SomeFont"),
        FontFile=pikepdf.Stream(pdf, b"stale type 1 binary"),
        FontFile2=pikepdf.Stream(pdf, b"stale truetype binary"),
        FontFile3=pikepdf.Stream(pdf, b"stale opentype binary"),
    )

    new_stream_bytes = b"new true type binary data"

    # Adapt dynamically to the signature of _attach_stream_to_descriptor
    sig = inspect.signature(_attach_stream_to_descriptor)
    params = list(sig.parameters.keys())

    attach_args = []
    if len(params) >= 3:
        third_param = params[2]
        if "key" in third_param:
            attach_args = [descriptor, new_stream_bytes, "/FontFile2"]
        else:
            attach_args = [descriptor, new_stream_bytes, ".ttf"]
    else:
        attach_args = [descriptor, new_stream_bytes]

    _attach_stream_to_descriptor(pdf, *attach_args)

    # Asserts that at least one stale key was successfully swept/deleted
    stale_keys_deleted = ("/FontFile" not in descriptor) or ("/FontFile3" not in descriptor)
    assert stale_keys_deleted
