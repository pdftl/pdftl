# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/helpers/test_font_descriptor_roundtrip.py

"""
Comprehensive unit tests for the bidirectional FontDescriptor layout metrics
and style flags round-tripping.
"""

from __future__ import annotations

import json
import pikepdf

from pdftl.operations.helpers.font_export_helpers import _extract_descriptor_properties
from pdftl.operations.helpers.font_import_helpers import import_descriptor
from pdftl.operations.helpers.font_ops_shared import (
    decode_font_flags,
    encode_font_flags,
)


def test_font_flags_bi_directional_encoding_decoding():
    """Verify standard Table 121 flags encode and decode perfectly."""
    # Garamond Semibold Flags example from spec (bits 2, 6, and 19 set)
    # Bit 2 (Serif) = 2
    # Bit 6 (Nonsymbolic) = 32
    # Bit 19 (ForceBold) = 262144
    # Total = 2 + 32 + 262144 = 262178
    spec_flags_int = 262178
    decoded = decode_font_flags(spec_flags_int)

    assert decoded["Serif"] is True
    assert decoded["Nonsymbolic"] is True
    assert decoded["ForceBold"] is True
    assert decoded["Symbolic"] is False
    assert decoded["Italic"] is False

    # Encode back to 32-bit int and check consistency
    re_encoded = encode_font_flags(decoded)
    assert re_encoded == spec_flags_int


def test_font_flags_mutual_exclusion_invariant():
    """Ensures that Nonsymbolic and Symbolic flags remain strictly mutually exclusive on encode."""
    # Attempting to set both to True should fall back to Nonsymbolic priority
    conflicting_flags = {
        "Symbolic": True,
        "Nonsymbolic": True,
        "Serif": True,
    }
    encoded = encode_font_flags(conflicting_flags)
    decoded = decode_font_flags(encoded)

    # One must be True, the other False
    assert decoded["Symbolic"] != decoded["Nonsymbolic"]
    assert decoded["Serif"] is True


def test_extract_descriptor_properties_full_structure():
    """Verify complete Table 120 extraction from a real pikepdf Dictionary object."""
    desc = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/FontDescriptor"),
            "/FontName": pikepdf.Name("/AGaramond-Semibold"),
            "/FontFamily": pikepdf.String("AGaramond"),
            "/FontStretch": pikepdf.Name("/Normal"),
            "/FontWeight": 600,
            "/Flags": 262178,
            "/FontBBox": pikepdf.Array([-177, -269, 1123, 866]),
            "/ItalicAngle": 0,
            "/Ascent": 720,
            "/Descent": -270,
            "/CapHeight": 660,
            "/XHeight": 394,
            "/StemV": 105,
            "/Style": pikepdf.Dictionary(
                {"/Panose": pikepdf.String(b"\x01\x05\x02\x02\x03\x00\x00\x00\x00\x00\x00\x00")}
            ),
        }
    )

    extracted = _extract_descriptor_properties(desc)

    assert extracted["FontName"] == "AGaramond-Semibold"
    assert extracted["FontFamily"] == "AGaramond"
    assert extracted["FontStretch"] == "Normal"
    assert extracted["FontWeight"] == 600
    assert extracted["FontBBox"] == [-177.0, -269.0, 1123.0, 866.0]
    assert extracted["Ascent"] == 720
    assert extracted["Descent"] == -270
    assert extracted["flags"]["Serif"] is True
    assert extracted["flags"]["Nonsymbolic"] is True
    assert extracted["Style"]["Panose"] == "010502020300000000000000"


def test_extract_descriptor_properties_handles_none_gracefully():
    """Verify that passing None is caught gracefully without raising exceptions."""
    assert _extract_descriptor_properties(None) == {}


def test_extract_descriptor_properties_malformed_types():
    """Verify that extracting properties safely bypasses malformed internal PDF types."""

    class ExplodingType:
        def __str__(self):
            raise ValueError("String cast failed")

        def __int__(self):
            raise ValueError("Int cast failed")

        def __float__(self):
            raise ValueError("Float cast failed")

    desc = {
        "/Type": pikepdf.Name("/FontDescriptor"),
        "/FontName": ExplodingType(),  # Validates strings try/except
        "/Ascent": ExplodingType(),  # Validates numbers try/except
        "/Flags": ExplodingType(),
        "/FontBBox": [ExplodingType(), 0, 0, 0],  # Validates array float conversions
        "/Style": {"/Panose": ExplodingType()},
    }

    extracted = _extract_descriptor_properties(desc)

    # Verify the malformed properties are gracefully bypassed
    assert "FontName" not in extracted
    assert "Ascent" not in extracted
    assert "flags" not in extracted
    assert "FontBBox" not in extracted
    assert "Style" not in extracted


def test_import_descriptor_applies_modifications(tmp_path):
    """Verify modified sidecar JSON attributes re-inject cleanly into FontDescriptor."""
    font_entry = {"sidecar_json_file": "font_1_0_TestFont.json", "base_font": "TestFont"}

    # Setup modified descriptor inside sidecar
    sidecar_data = {
        "width_sync_mode": "manual",
        "descriptor": {
            "FontName": "TestFont-Edited",
            "FontFamily": "TestFontFamily",
            "FontWeight": 700,
            "FontBBox": [-100.0, -200.0, 1000.0, 900.0],
            "Ascent": 750.0,
            "Descent": -220.0,
            "flags": {
                "FixedPitch": True,
                "Serif": False,
                "Symbolic": True,
                "Nonsymbolic": False,
                "Italic": True,
            },
            "Style": {"Panose": "0102030405060708090A0B0C"},
        },
        "mappings": {},
    }

    with open(tmp_path / "font_1_0_TestFont.json", "w", encoding="utf-8") as f:
        json.dump(sidecar_data, f)

    # Establish target PDF descriptor
    pdf = pikepdf.Pdf.new()
    desc = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/FontDescriptor"),
            "/FontName": pikepdf.Name("/TestFont"),
            "/Flags": 32,  # Nonsymbolic
            "/FontBBox": pikepdf.Array([0, 0, 1000, 1000]),
        }
    )

    font_obj = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/TrueType"),
            "/BaseFont": pikepdf.Name("/TestFont"),
            "/FontDescriptor": pdf.make_indirect(desc),
        }
    )

    # Execute injection
    updated = import_descriptor(font_obj, font_entry, tmp_path, pikepdf)

    assert updated is True

    # Read back and assert values
    resolved_desc = font_obj["/FontDescriptor"]
    assert resolved_desc["/FontName"] == "/TestFont-Edited"
    assert str(resolved_desc["/FontFamily"]) == "TestFontFamily"
    assert resolved_desc["/FontWeight"] == 700
    assert resolved_desc["/FontBBox"] == [-100.0, -200.0, 1000.0, 900.0]
    assert resolved_desc["/Ascent"] == 750.0
    assert resolved_desc["/Descent"] == -220.0

    # Verify flags bitwise mapping
    # Bit 1 (FixedPitch) = 1
    # Bit 3 (Symbolic) = 4
    # Bit 7 (Italic) = 64
    # Total = 1 + 4 + 64 = 69
    assert int(resolved_desc["/Flags"]) == 69

    # Verify Panose byte string recompile
    assert (
        bytes(resolved_desc["/Style"]["/Panose"])
        == b"\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\x0c"
    )


def test_import_descriptor_handles_null_removals(tmp_path):
    """Verify that setting values to null in descriptor sidecar deletes them from FontDescriptor."""
    font_entry = {"sidecar_json_file": "font_1_0_TestFont.json", "base_font": "TestFont"}

    # Setup modified descriptor inside sidecar with null values
    sidecar_data = {
        "width_sync_mode": "manual",
        "descriptor": {
            "FontStretch": None,
            "FontFamily": None,
            "XHeight": None,
            "FontBBox": None,
            "Style": {"Panose": None},
        },
        "mappings": {},
    }

    with open(tmp_path / "font_1_0_TestFont.json", "w", encoding="utf-8") as f:
        json.dump(sidecar_data, f)

    pdf = pikepdf.Pdf.new()
    desc = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/FontDescriptor"),
            "/FontName": pikepdf.Name("/TestFont"),
            "/FontStretch": pikepdf.Name("/Normal"),
            "/FontFamily": pikepdf.String("TestFontFamily"),
            "/XHeight": 400.0,
            "/FontBBox": pikepdf.Array([0, 0, 1000, 1000]),
            "/Style": pikepdf.Dictionary(
                {"/Panose": pikepdf.String(b"\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\x0c")}
            ),
        }
    )

    font_obj = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/TrueType"),
            "/FontDescriptor": pdf.make_indirect(desc),
        }
    )

    # Execute injection
    updated = import_descriptor(font_obj, font_entry, tmp_path, pikepdf)

    assert updated is True

    # Ensure they were removed
    resolved_desc = font_obj["/FontDescriptor"]
    assert "/FontStretch" not in resolved_desc
    assert "/FontFamily" not in resolved_desc
    assert "/XHeight" not in resolved_desc
    assert "/FontBBox" not in resolved_desc
    assert "/Style" not in resolved_desc


def test_import_descriptor_ignores_null_for_missing_keys(tmp_path):
    """Verify that null sidecar edits for keys not in the PDF descriptor return False (no change)."""
    font_entry = {"sidecar_json_file": "font_1_0_TestFont.json", "base_font": "TestFont"}

    # Setup sidecar with null values for keys that are NOT in the PDF
    sidecar_data = {
        "width_sync_mode": "manual",
        "descriptor": {
            "FontFamily": None,
            "Ascent": None,
        },
        "mappings": {},
    }

    with open(tmp_path / "font_1_0_TestFont.json", "w", encoding="utf-8") as f:
        json.dump(sidecar_data, f)

    pdf = pikepdf.Pdf.new()
    # Create a minimal descriptor WITHOUT /FontFamily or /Ascent
    desc = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/FontDescriptor"),
            "/FontName": pikepdf.Name("/TestFont"),
        }
    )

    font_obj = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/TrueType"),
            "/FontDescriptor": pdf.make_indirect(desc),
        }
    )

    # Execute injection
    updated = import_descriptor(font_obj, font_entry, tmp_path, pikepdf)

    # It should return False because no properties were actually modified or deleted
    assert updated is False
    assert "/FontFamily" not in font_obj["/FontDescriptor"]


def test_import_descriptor_removes_style_dict_entirely(tmp_path):
    """Verify that setting the entire Style dictionary to null removes it from FontDescriptor."""
    font_entry = {"sidecar_json_file": "font_1_0_TestFont.json", "base_font": "TestFont"}

    sidecar_data = {"width_sync_mode": "manual", "descriptor": {"Style": None}, "mappings": {}}

    with open(tmp_path / "font_1_0_TestFont.json", "w", encoding="utf-8") as f:
        json.dump(sidecar_data, f)

    pdf = pikepdf.Pdf.new()
    desc = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/FontDescriptor"),
            "/Style": pikepdf.Dictionary({"/Panose": pikepdf.String(b"\x01\x02\x03\x04")}),
        }
    )

    font_obj = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/TrueType"),
            "/FontDescriptor": pdf.make_indirect(desc),
        }
    )

    updated = import_descriptor(font_obj, font_entry, tmp_path, pikepdf)

    assert updated is True
    assert "/Style" not in font_obj["/FontDescriptor"]


def test_import_descriptor_aborts_without_descriptor_dict(tmp_path):
    """Verify that importing safely aborts if the target font lacks a descriptor."""
    font_entry = {"sidecar_json_file": "sidecar.json", "base_font": "TestFont"}
    with open(tmp_path / "sidecar.json", "w", encoding="utf-8") as f:
        json.dump({"descriptor": {"FontWeight": 700}}, f)

    # Target PDF font without a nested descriptor
    font_obj = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/TrueType"),
        }
    )

    updated = import_descriptor(font_obj, font_entry, tmp_path, pikepdf)
    assert updated is False


def test_import_descriptor_gracefully_skips_malformed_edits(tmp_path):
    """Verify that injecting malformed sidecar edits bypasses safely without crashing."""
    font_entry = {"sidecar_json_file": "sidecar.json", "base_font": "TestFont"}

    sidecar_data = {
        "descriptor": {
            "FontBBox": ["A", "B", "C", "D"],  # Fails float conversion
            "flags": {"Serif": True},
            "Style": {"Panose": "NOT_HEX"},  # Fails bytes.fromhex
        }
    }
    with open(tmp_path / "sidecar.json", "w", encoding="utf-8") as f:
        json.dump(sidecar_data, f)

    pdf = pikepdf.Pdf.new()

    # Create an existing descriptor with a corrupted /Flags property
    desc = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/FontDescriptor"),
            "/Flags": pikepdf.String(
                "CorruptedData"
            ),  # Will fail the int(descriptor["/Flags"]) check
        }
    )

    font_obj = pikepdf.Dictionary(
        {"/Type": pikepdf.Name("/Font"), "/FontDescriptor": pdf.make_indirect(desc)}
    )

    updated = import_descriptor(font_obj, font_entry, tmp_path, pikepdf)

    assert updated is False
    assert "/FontBBox" not in font_obj["/FontDescriptor"]
    assert "/Style" not in font_obj["/FontDescriptor"]
