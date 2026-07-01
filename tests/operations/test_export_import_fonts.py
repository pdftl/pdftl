# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/test_export_import_fonts.py

"""
Integration tests for the export_fonts and import_fonts operations
(full-cycle, end-to-end through the public @register_operation entry points),
plus unit tests for the small shared helpers in font_ops_shared.py.

Unit tests that exercise the export/import helper internals directly live in:
  - tests/operations/helpers/test_font_export_helpers.py
  - tests/operations/helpers/test_font_import_helpers.py
  - tests/fonts/test_font_binary_utils.py
"""

from __future__ import annotations

import json

import pikepdf
import pytest

from pdftl.core.core_types import OpResult
from pdftl.exceptions import InvalidArgumentError, UserCommandLineError
from pdftl.operations.export_import_fonts import (
    export_fonts,
    export_fonts_cli_hook,
    import_fonts,
)
from pdftl.operations.helpers.font_ops_shared import file_hash, get_target_pages

# ============================================================================
# Fixtures
# ============================================================================


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


def _mock_font_tools(monkeypatch):
    """Mocks fontTools to safely simulate ttLib behavior without depending on it."""

    class DummyGlyph:
        def __init__(self):
            self.coordinates = [(100.0, 200.0)]
            self.components = []

    class DummyTTFont:
        def __init__(self, filepath=None):
            self.filepath = filepath
            self.head = type("Head", (), {"unitsPerEm": 1000})()
            self.cmap = {1: "A", 2: "B"}
            self.hmtx = {"A": [250.0, 0], "B": [500.0, 0]}
            self.glyf = {"A": DummyGlyph(), "B": DummyGlyph()}

        def getBestCmap(self):
            return self.cmap

        def __getitem__(self, key):
            if key == "hmtx":
                return type(
                    "Hmtx",
                    (),
                    {
                        "metrics": self.hmtx,
                        "__getitem__": lambda s, k: self.hmtx[k],
                        "__setitem__": lambda s, k, v: self.hmtx.update({k: v}),
                    },
                )()
            if key == "glyf":
                return self.glyf
            return getattr(self, key)

        def __contains__(self, key):
            return key in ("glyf", "hmtx")

        def save(self, path_or_stream):
            if hasattr(path_or_stream, "write"):
                path_or_stream.write(b"mock_saved_bytes")
            pass

    monkeypatch.setattr("fontTools.ttLib.TTFont", DummyTTFont)


@pytest.fixture
def sample_pdf_with_type3_font(tmp_path):
    """Constructs a structurally valid in-memory PDF containing a Type 3 font."""
    pdf = pikepdf.new()
    glyph_stream_bytes = (
        b"d1 250 0 0 -100 250 800\n"
        b"0 0 m 250 700 l S\n"
        b"BI /W 8 /H 8 /BPC 1 /CS /DeviceGray ID\n"
        b"\xff\x00\xff\x00\xff\x00\xff\x00\nEI\n"
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
    return pdf, font_obj


# ============================================================================
# Full-cycle integration tests
# ============================================================================


def test_export_import_fonts_cycle(tmp_path, sample_pdf_with_fonts, monkeypatch):
    pdf, font_obj = sample_pdf_with_fonts
    export_dir = tmp_path / "extracted_fonts"

    _mock_font_tools(monkeypatch)

    # --- TEST EXPORT_FONTS ---
    # Run the export operation to directory in 'all' mode to extract both JSON and PS sidecars
    res_export = export_fonts(pdf, [str(export_dir), "tounicode=all"])
    assert res_export.success
    export_fonts_cli_hook(res_export, None, None)

    manifest_path = export_dir / "manifest.json"
    assert manifest_path.is_file()

    # Verify extracted binary is created
    font_files = list(export_dir.glob("font_*.ttf"))
    assert len(font_files) == 1
    font_file = font_files[0]
    assert font_file.is_file()

    # Verify unified JSON and PS sidecars exist
    json_cmaps = list(export_dir.glob("font_*.json"))
    assert len(json_cmaps) == 1
    ps_cmaps = list(export_dir.glob("font_*.ps"))
    assert len(ps_cmaps) == 1

    # --- TEST CLASH GUARDRAIL ---
    with pytest.raises(UserCommandLineError, match="Ambiguous ToUnicode source"):
        import_fonts(pdf, [str(export_dir)])

    # Delete the PS sidecar to resolve clash
    ps_cmaps[0].unlink()

    # --- TEST MODIFYING ASSETS ---
    sidecar_path = json_cmaps[0]
    with open(sidecar_path, encoding="utf-8") as f:
        sidecar_data = json.load(f)

    # Force manual width sync and modify ToUnicode
    sidecar_data["width_sync_mode"] = "manual"
    sidecar_data["mappings"]["01"]["width"] = {"pdf": 999.0, "font": 250.0}
    sidecar_data["mappings"]["01"]["unicode"] = "Z"

    with open(sidecar_path, "w", encoding="utf-8") as f:
        json.dump(sidecar_data, f)

    # --- TEST IMPORT_FONTS (MANUAL MODE) ---
    res_import = import_fonts(pdf, [str(export_dir)])
    assert res_import.success

    assert float(font_obj.Widths[0]) == 999.0
    imported_cmap_bytes = font_obj["/ToUnicode"].read_bytes()
    assert b"<01> <005A>" in imported_cmap_bytes

    # --- TEST SQUASH MODE ---
    sidecar_data["width_sync_mode"] = "squash_font_vectors"
    with open(font_file, "wb") as f:
        f.write(b"Modified TrueType Font Bytes")
    with open(sidecar_path, "w", encoding="utf-8") as f:
        json.dump(sidecar_data, f)

    res_import_squash = import_fonts(pdf, [str(export_dir)])
    assert res_import_squash.success

    # --- TEST PATCH MODE ---
    sidecar_data["width_sync_mode"] = "patch_font_metrics"
    with open(sidecar_path, "w", encoding="utf-8") as f:
        json.dump(sidecar_data, f)

    res_import_patch = import_fonts(pdf, [str(export_dir)])
    assert res_import_patch.success

    # --- TEST PRESERVE MODE ---
    sidecar_data["width_sync_mode"] = "preserve"
    with open(sidecar_path, "w", encoding="utf-8") as f:
        json.dump(sidecar_data, f)

    res_import_preserve = import_fonts(pdf, [str(export_dir)])
    assert res_import_preserve.success


def test_type3_universal_roundtrip(sample_pdf_with_type3_font, tmp_path):
    """Ensures Type 3 fonts and associated inline bitmaps round-trip perfectly via top-level CLI."""
    from PIL import Image

    pdf, font_obj = sample_pdf_with_type3_font
    export_dir = tmp_path / "extracted_fonts"

    # 1. Execute Export Pipeline
    res_export = export_fonts(pdf, [str(export_dir)])
    assert res_export.success
    export_fonts_cli_hook(res_export, None, None)

    manifest_file = export_dir / "manifest.json"
    assert manifest_file.exists()
    with open(manifest_file) as f:
        manifest = json.load(f)

    # Find the font entry in manifest
    font_key = list(manifest["fonts"].keys())[0]
    font_entry = manifest["fonts"][font_key]
    charprocs_file = export_dir / font_entry["charprocs_file"]
    assert charprocs_file.exists()

    bitmap_file = export_dir / font_entry["inline_images"]["A_0"]["filename"]
    assert bitmap_file.exists()

    # 2. Mutate
    img = Image.open(bitmap_file)
    assert img.size == (8, 8)
    mutated_img = Image.new("1", (8, 8), color=0)
    mutated_img.save(bitmap_file, format="TIFF")

    # 3. Execute Import Pipeline
    res_import = import_fonts(pdf, [str(export_dir)])
    assert res_import.success

    # 4. Assert Lossless Re-injection
    rebuilt_stream = font_obj["/CharProcs"]["/A"].read_bytes()
    assert b"0 0 m 250 700 l S" in rebuilt_stream
    assert b"\x00\x00\x00\x00" in rebuilt_stream


def test_export_fonts_errors(tmp_path, sample_pdf_with_fonts):
    pdf, _ = sample_pdf_with_fonts

    with pytest.raises(InvalidArgumentError, match="Missing required directory argument"):
        export_fonts(pdf, [])

    with pytest.raises(InvalidArgumentError, match="Invalid tounicode mode: 'invalid'"):
        export_fonts(pdf, ["tounicode=invalid", str(tmp_path)])


def test_import_fonts_errors(tmp_path, sample_pdf_with_fonts):
    pdf, _ = sample_pdf_with_fonts

    with pytest.raises(InvalidArgumentError, match="Missing required directory argument"):
        import_fonts(pdf, [])

    with pytest.raises(InvalidArgumentError, match="Target directory does not exist"):
        import_fonts(pdf, ["/nonexistent_dir"])

    out_dir = tmp_path / "test_fonts"
    out_dir.mkdir()

    with pytest.raises(InvalidArgumentError, match="Manifest file not found"):
        import_fonts(pdf, [str(out_dir)])

    # Write broken manifest
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text("{ broken json")

    with pytest.raises(InvalidArgumentError, match="Invalid JSON manifest"):
        import_fonts(pdf, [str(out_dir)])


def test_import_fonts_hook_and_missing_object(tmp_path, sample_pdf_with_fonts):
    pdf, _ = sample_pdf_with_fonts
    out_dir = tmp_path / "test_fonts"
    out_dir.mkdir()

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "fonts": {
                    "999_0": {  # Missing object
                        "obj_id": 999,
                        "gen_id": 0,
                    }
                }
            }
        )
    )

    # Should not crash, just skip the missing font
    res = import_fonts(pdf, [str(out_dir)])
    assert res.success


def test_export_type1_font_decompression(tmp_path, sample_pdf_with_type1_font):
    """Verifies that Type 1 (.pfb) fonts are correctly decompressed upon extraction."""
    pdf, font_obj = sample_pdf_with_type1_font
    export_dir = tmp_path / "extracted_fonts"

    res_export = export_fonts(pdf, [str(export_dir)])
    assert res_export.success

    pfb_files = list(export_dir.glob("font_*.pfb"))
    assert len(pfb_files) == 1

    # Verify that we exported the DECOMPRESSED bytes, not the zlib payload
    assert pfb_files[0].read_bytes() == b"Fake Type1 PFB Bytes"


def test_export_cli_hook_early_exit():
    # Returns immediately without writing if not success
    res = OpResult(success=False, data={"test": "data"}, meta={"output_file": "missing.json"})
    export_fonts_cli_hook(res, None, None)


def test_export_fonts_edge_cases(tmp_path, sample_pdf_with_fonts):
    pdf, font_obj = sample_pdf_with_fonts

    # 1. Type 3 font skipping
    font_obj["/Subtype"] = pikepdf.Name("/Type3")
    res = export_fonts(pdf, [str(tmp_path)])
    assert res.success

    # 2. Missing descriptor
    del font_obj["/Subtype"]
    del font_obj["/FontDescriptor"]
    res = export_fonts(pdf, [str(tmp_path)])
    assert res.success


def test_import_fonts_edge_cases(tmp_path, sample_pdf_with_fonts, monkeypatch):
    pdf, font_obj = sample_pdf_with_fonts

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "fonts": {
                    "1_0": {
                        "obj_id": font_obj.objgen[0],
                        "gen_id": font_obj.objgen[1],
                        "embedded_file": "missing.ttf",
                        "descriptor_key": "FontFile2",
                        "sidecar_json_file": "missing.json",
                    }
                }
            }
        )
    )

    # File not found won't crash, it just logs warning
    res = import_fonts(pdf, [str(tmp_path)])
    assert res.success

    # Missing ToUnicode sidecar or unreadable sidecar
    sidecar_path = tmp_path / "missing.json"
    sidecar_path.write_text("invalid json")
    res = import_fonts(pdf, [str(tmp_path)])
    assert res.success

    # Test reading raw PS ToUnicode
    ps_sidecar_path = tmp_path / "tounicode.ps"
    ps_sidecar_path.write_bytes(b"Valid PS")
    manifest_path.write_text(
        json.dumps(
            {
                "fonts": {
                    "1_0": {
                        "obj_id": font_obj.objgen[0],
                        "gen_id": font_obj.objgen[1],
                        "tounicode_ps_file": "tounicode.ps",
                    }
                }
            }
        )
    )
    res = import_fonts(pdf, [str(tmp_path)])
    assert res.success
    assert pdf.pages[0].Resources.Font.F1.ToUnicode.read_bytes() == b"Valid PS"


def test_export_fonts_with_explicit_page_spec(tmp_path, sample_pdf_with_fonts):
    pdf, _ = sample_pdf_with_fonts
    export_dir = tmp_path / "out"
    res = export_fonts(pdf, ["1", str(export_dir)])
    assert res.success
    assert res.data["fonts"]  # font on page 1 was found via the explicit spec


def test_import_fonts_empty_fonts_dict(tmp_path, sample_pdf_with_fonts):
    pdf, _ = sample_pdf_with_fonts
    out_dir = tmp_path / "d"
    out_dir.mkdir()
    (out_dir / "manifest.json").write_text(json.dumps({"fonts": {}}))
    res = import_fonts(pdf, [str(out_dir)])
    assert res.success


def test_import_fonts_missing_object_with_real_payload_does_not_crash(
    tmp_path, sample_pdf_with_fonts
):
    pdf, _ = sample_pdf_with_fonts
    out_dir = tmp_path / "d"
    out_dir.mkdir()
    (out_dir / "manifest.json").write_text(
        json.dumps(
            {
                "fonts": {
                    "999_0": {
                        "obj_id": 999,
                        "gen_id": 0,
                        "differences": ["/A", "/B"],  # non-trivial payload
                    }
                }
            }
        )
    )
    # Without the None-guard fix, this crashes with TypeError deep inside
    # import_differences when it tries `"/Encoding" not in font_obj` on None.
    res = import_fonts(pdf, [str(out_dir)])
    assert res.success


def test_import_fonts_get_object_raises_pdferror(tmp_path, sample_pdf_with_fonts):
    """
    Covers the except-clause path where pdf.get_object() itself raises,
    as opposed to simply returning None for a missing object (which is
    what happens for a plain out-of-range obj/gen in this pikepdf version).
    """
    pdf, _ = sample_pdf_with_fonts

    def raise_pdferror(objgen):
        raise pikepdf.PdfError("simulated corrupt xref entry")

    pdf.get_object = raise_pdferror

    out_dir = tmp_path / "d"
    out_dir.mkdir()
    (out_dir / "manifest.json").write_text(
        json.dumps({"fonts": {"1_0": {"obj_id": 1, "gen_id": 0}}})
    )

    # Should not raise; the except clause swallows PdfError/KeyError and
    # skips the font via the "not found" warning path.
    res = import_fonts(pdf, [str(out_dir)])
    assert res.success


def test_import_fonts_success_end_to_end(tmp_path, sample_pdf_with_fonts):
    """Ensures that the final update statement block in import_fonts is executed."""
    pdf, font_obj = sample_pdf_with_fonts
    out_dir = tmp_path / "imported_fonts"
    out_dir.mkdir()

    manifest = {
        "fonts": {
            "1_0": {
                "obj_id": font_obj.objgen[0],
                "gen_id": font_obj.objgen[1],
                "base_font": "MyFont",
                "subtype": "TrueType",
            }
        }
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest))

    res = import_fonts(pdf, [str(out_dir)])
    assert res.success


# ============================================================================
# font_ops_shared unit tests
# ============================================================================


def testfile_hash_oserror(monkeypatch, tmp_path):
    test_file = tmp_path / "test.txt"
    test_file.write_text("test")

    def mock_open(*args, **kwargs):
        raise OSError("Permission denied")

    monkeypatch.setattr("builtins.open", mock_open)
    assert file_hash(test_file) == ""


def testget_target_pages_direct():
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.add_blank_page()
    assert get_target_pages(pdf, ["1"]) == [1]
    assert get_target_pages(pdf, []) == [1, 2]


@pytest.fixture
def sample_pdf_with_type0_font():
    pdf = pikepdf.new()
    page = pdf.add_blank_page()

    cid_font = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/CIDFontType2"),
            "/BaseFont": pikepdf.Name("/MyCIDFont"),
            "/CIDToGIDMap": pdf.make_stream(b"\x00\x05\x00\x0a\x00\x00"),
        }
    )
    font_obj = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type0"),
                "/BaseFont": pikepdf.Name("/MyCIDFont"),
                "/DescendantFonts": pikepdf.Array([cid_font]),
            }
        )
    )
    page.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font_obj})})
    return pdf, font_obj, cid_font


def test_cid_to_gid_map_export_import_round_trip(tmp_path, sample_pdf_with_type0_font):
    pdf, font_obj, cid_font = sample_pdf_with_type0_font
    export_dir = tmp_path / "fonts"

    res_export = export_fonts(pdf, [str(export_dir)])
    assert res_export.success
    export_fonts_cli_hook(res_export, None, None)

    sidecar_files = list(export_dir.glob("*.cid2gid.json"))
    assert len(sidecar_files) == 1

    with open(sidecar_files[0]) as f:
        sidecar = json.load(f)
    assert sidecar["cid_to_gid"] == {"0000": "0005", "0001": "000A"}

    # Mutate on disk, then import, and confirm the PDF's own stream changes
    sidecar["cid_to_gid"] = {"0000": "0063"}
    with open(sidecar_files[0], "w") as f:
        json.dump(sidecar, f)

    res_import = import_fonts(pdf, [str(export_dir)])
    assert res_import.success

    from pdftl.fonts.widths_utils import parse_cid_to_gid_map

    assert parse_cid_to_gid_map(cid_font["/CIDToGIDMap"].read_bytes()) == {0: 0x63}


def test_identity_cid_to_gid_map_export_import_round_trip(tmp_path):
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    cid_font = pikepdf.Dictionary(
        {"/Subtype": pikepdf.Name("/CIDFontType2"), "/CIDToGIDMap": pikepdf.Name("/Identity")}
    )
    font_obj = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Subtype": pikepdf.Name("/Type0"),
                "/BaseFont": pikepdf.Name("/MyCIDFont"),
                "/DescendantFonts": pikepdf.Array([cid_font]),
            }
        )
    )
    page.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font_obj})})

    export_dir = tmp_path / "fonts"
    res_export = export_fonts(pdf, [str(export_dir)])
    assert res_export.success
    export_fonts_cli_hook(res_export, None, None)

    manifest_path = export_dir / "manifest.json"
    with open(manifest_path) as f:
        manifest = json.load(f)
    entry = next(iter(manifest["fonts"].values()))
    assert entry["cid_to_gid_map"] == "Identity"
    assert "cid_to_gid_map_file" not in entry

    res_import = import_fonts(pdf, [str(export_dir)])
    assert res_import.success
    assert cid_font["/CIDToGIDMap"] == pikepdf.Name("/Identity")


def test_export_import_unembedded_core14_font_round_trip(tmp_path):
    """End-to-end coverage for G-2: a standard Core 14 font referenced
    without any embedded FontFile stream exports cleanly with an explicit
    is_embedded: false / null embedded_file marker, and still accepts a
    manual width edit back into the PDF's /Widths array on import."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page()

    font_obj = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
                "/FirstChar": 65,
                "/LastChar": 65,
                "/Widths": pikepdf.Array([722.0]),
            }
        )
    )
    page.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font_obj})})

    export_dir = tmp_path / "core14_fonts"
    res_export = export_fonts(pdf, [str(export_dir)])
    assert res_export.success
    export_fonts_cli_hook(res_export, None, None)

    manifest_path = export_dir / "manifest.json"
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    font_entry = next(iter(manifest["fonts"].values()))
    assert font_entry["is_embedded"] is False
    assert font_entry["embedded_file"] is None
    assert font_entry["embedded_format"] is None

    sidecar_path = export_dir / font_entry["sidecar_json_file"]
    with open(sidecar_path, encoding="utf-8") as f:
        sidecar_data = json.load(f)

    sidecar_data["width_sync_mode"] = "manual"
    sidecar_data["mappings"]["41"]["width"] = {"pdf": 750.0}
    with open(sidecar_path, "w", encoding="utf-8") as f:
        json.dump(sidecar_data, f)

    res_import = import_fonts(pdf, [str(export_dir)])
    assert res_import.success
    assert float(font_obj.Widths[0]) == 750.0
