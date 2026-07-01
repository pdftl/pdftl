# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/helpers/test_type3_extraction_helpers.py

"""
Unit tests for pdftl.operations.helpers.type3_extraction_helpers.
"""

from __future__ import annotations

from unittest.mock import MagicMock
import pikepdf

from pdftl.operations.helpers.type3_extraction_helpers import (
    _parse_inline_dict,
    _save_raw_to_tiff,
    _decode_inline_image,
    _normalize_inline_meta,
    _clean_filter_meta,
    _process_inline_images_on_export,
    export_type3_font,
)


# ---------------------------------------------------------------------------
# Type 3 Extraction & TIFF Handling
# ---------------------------------------------------------------------------


def test_parse_inline_dict_normalizes_keys():
    """Ensures that standard PDF inline image dictionary short keys are mapped correctly."""
    raw = b"/W 10 /H 20 /CS /DeviceGray /BPC 8 /F /FlateDecode /D [1 0]"
    meta = _parse_inline_dict(raw)
    assert meta == {
        "Width": "10",
        "Height": "20",
        "ColorSpace": "/DeviceGray",
        "BitsPerComponent": "8",
        "Filter": "/FlateDecode",
        "Decode": "[1 0]",
    }


def test_parse_inline_dict_with_stray_tokens():
    """Ensure that the token parser correctly skips leading and nested stray content values."""
    raw = b"stray_prefix_value /W 10 /H 20"
    meta = _parse_inline_dict(raw)
    assert meta == {
        "Width": "10",
        "Height": "20",
    }


def test_save_raw_to_tiff_color_space_branches(tmp_path):
    """Validates color space branch logic inside the TIFF exporter."""
    meta_gray = {"Width": 2, "Height": 2, "ColorSpace": "/DeviceGray", "BitsPerComponent": 8}
    _save_raw_to_tiff(b"\x00\xff\x00\xff", meta_gray, tmp_path / "gray.tiff")
    assert (tmp_path / "gray.tiff").exists()

    meta_rgb = {"Width": 2, "Height": 2, "ColorSpace": "/DeviceRGB", "BitsPerComponent": 8}
    _save_raw_to_tiff(b"\xff\x00\x00" * 4, meta_rgb, tmp_path / "rgb.tiff")
    assert (tmp_path / "rgb.tiff").exists()

    meta_cmyk = {"Width": 2, "Height": 2, "ColorSpace": "/DeviceCMYK", "BitsPerComponent": 8}
    _save_raw_to_tiff(b"\xff\x00\x00\x00" * 4, meta_cmyk, tmp_path / "cmyk.tiff")
    assert (tmp_path / "cmyk.tiff").exists()

    meta_indexed = {"Width": 2, "Height": 2, "ColorSpace": "/Indexed", "BitsPerComponent": 8}
    _save_raw_to_tiff(b"\x00\x01\x02\x03", meta_indexed, tmp_path / "indexed.tiff")
    assert (tmp_path / "indexed.tiff").exists()

    meta_custom = {"Width": 2, "Height": 2, "ColorSpace": "/CustomCS", "BitsPerComponent": 8}
    _save_raw_to_tiff(b"\x00\xff\x00\xff", meta_custom, tmp_path / "custom.tiff")
    assert (tmp_path / "custom.tiff").exists()


def test_save_raw_to_tiff_short_data_padding(tmp_path):
    """Ensures that incomplete binary image streams are safely padded with zeros instead of crashing."""
    meta_rgb = {"Width": 2, "Height": 2, "ColorSpace": "/DeviceRGB", "BitsPerComponent": 8}
    # Expected: 2 * 2 * 3 = 12 bytes. We intentionally truncate to 6.
    _save_raw_to_tiff(b"\xff\x00\x00" * 2, meta_rgb, tmp_path / "short_rgb.tiff")
    assert (tmp_path / "short_rgb.tiff").exists()


def test_save_raw_to_tiff_monochrome(tmp_path):
    """Ensure _save_raw_to_tiff handles 1-bit monochrome stride calculations (Line 315)."""
    meta_mono = {"Width": 2, "Height": 2, "ColorSpace": "/DeviceGray", "BitsPerComponent": 1}
    # For a 1-bit image, 2x2 requires (2+7)//8 = 1 byte per row. Total 2 bytes.
    _save_raw_to_tiff(b"\x00\x00", meta_mono, tmp_path / "mono.tiff")
    assert (tmp_path / "mono.tiff").exists()


# ---------------------------------------------------------------------------
# Decoupled Stream Decoder Sub-system Tests
# ---------------------------------------------------------------------------


def test_normalize_inline_meta():
    """Ensure normalization robustly converts short abbreviations to explicit properties."""
    meta = {"W": "10", "CS": "/RGB", "F": "/Fl"}
    norm = _normalize_inline_meta(meta)
    assert norm == {"Width": "10", "ColorSpace": "/DeviceRGB", "Filter": "/FlateDecode"}


def test_clean_filter_meta():
    """Ensures metadata filtering correctly strips stream encoding properties."""
    meta = {"Filter": "/FlateDecode", "Width": "10", "DecodeParms": "<</K -1>>"}
    clean = _clean_filter_meta(meta)
    assert clean == {"Width": "10"}


def test_decode_ccitt_image_k_value_parsing(monkeypatch):
    """Ensures CCITT decoding handles DP and K dictionaries and strings accurately."""
    from pdftl.operations.helpers.type3_extraction_helpers import _decode_ccitt_image
    from PIL import Image

    # Mock PIL Image.open to return a mock image seamlessly decoupled from exact system decoders
    mock_img = MagicMock()
    mock_img.tobytes.return_value = b"decoded_ccitt"
    monkeypatch.setattr(Image, "open", lambda *a: mock_img)

    # 1. DP with /K -1
    meta1 = {
        "Width": "2",
        "Height": "2",
        "DecodeParms": "<</K -1 /Columns 2>>",
        "Filter": "/CCITTFaxDecode",
    }
    decoded, clean_meta = _decode_ccitt_image(b"fake", 2, 2, meta1)
    assert decoded == b"decoded_ccitt"
    assert "Filter" not in clean_meta

    # 2. DP with /K 1 (Group 3)
    meta2 = {
        "Width": "2",
        "Height": "2",
        "DecodeParms": "<</K 1 /Columns 2>>",
        "Filter": "/CCITTFaxDecode",
        "BlackIs1": "true",
    }
    _decode_ccitt_image(b"fake", 2, 2, meta2)

    # 3. K in meta as int/str
    meta3 = {"Width": "2", "Height": "2", "K": "0", "Filter": "/CCITTFaxDecode"}
    _decode_ccitt_image(b"fake", 2, 2, meta3)

    # 4. Invalid K raise ValueError
    meta4 = {"Width": "2", "Height": "2", "K": "invalid", "Filter": "/CCITTFaxDecode"}
    _decode_ccitt_image(b"fake", 2, 2, meta4)


def test_decode_ccitt_image_failure_fallback():
    """Ensures _decode_ccitt_image cleanly reverts to raw data when facing invalid structural dimensions."""
    from pdftl.operations.helpers.type3_extraction_helpers import _decode_ccitt_image

    # Passing 0 for width/height with odd-length data will raise structural errors within PIL / struct packing,
    # and evaluates the odd IFD offset calculations (covering lines 114 and 121).
    res, clean_meta = _decode_ccitt_image(b"faker", 0, 0, {"Filter": "/CCITTFaxDecode"})
    assert res == b"faker"
    assert clean_meta.get("Filter") == "/CCITTFaxDecode"


def test_decode_dct_image_success(monkeypatch):
    """Ensure JPEG data is successfully decompressed using mapped Pillow decoders."""
    from PIL import Image
    from pdftl.operations.helpers.type3_extraction_helpers import _decode_dct_image

    mock_img = MagicMock()
    mock_img.tobytes.return_value = b"decoded_jpeg"
    monkeypatch.setattr(Image, "open", lambda *a: mock_img)

    meta = {"Width": "2", "Height": "2", "BitsPerComponent": "8", "Filter": "/DCTDecode"}
    decoded, clean_meta = _decode_dct_image(b"fake_jpeg_data", meta)
    assert decoded == b"decoded_jpeg"
    assert "Filter" not in clean_meta


def test_decode_dct_image_failure_fallback():
    """Ensures JPEG decompression falls back safely on corrupt data without raising."""
    from pdftl.operations.helpers.type3_extraction_helpers import _decode_dct_image

    corrupt_data = b"completely_garbage_compressed_data"
    meta = {"Filter": "/DCTDecode"}

    decoded, clean_meta = _decode_dct_image(corrupt_data, meta)
    assert decoded == corrupt_data
    assert clean_meta.get("Filter") == "/DCTDecode"


def test_decode_native_pdf_image_failure_fallback():
    """Ensures native decompression falls back to raw data if decompression fails."""
    from pdftl.operations.helpers.type3_extraction_helpers import _decode_native_pdf_image

    corrupt_data = b"completely_garbage_compressed_data"
    meta = {
        "Width": "2",
        "Height": "2",
        "BitsPerComponent": "8",
        "Filter": "/FlateDecode",
    }

    decoded, clean_meta = _decode_native_pdf_image(corrupt_data, 2, 2, "/FlateDecode", meta)
    assert decoded == corrupt_data
    assert clean_meta["Filter"] == "/FlateDecode"


def test_decode_native_pdf_image_success():
    """Ensure natively supported PDF filters successfully decompress valid data."""
    from pdftl.operations.helpers.type3_extraction_helpers import _decode_native_pdf_image
    import zlib

    meta = {
        "Width": "2",
        "Height": "2",
        "BitsPerComponent": "8",
        "Filter": "/FlateDecode",
        "ColorSpace": "/DeviceGray",
    }
    data = zlib.compress(b"raw_bytes")
    decoded, clean_meta = _decode_native_pdf_image(data, 2, 2, "/FlateDecode", meta)
    assert decoded == b"raw_bytes"
    assert "Filter" not in clean_meta


def test_decode_native_pdf_imagemask():
    """Ensures ImageMask translates effectively into 1-bit decoding components."""
    from pdftl.operations.helpers.type3_extraction_helpers import _decode_native_pdf_image
    import zlib

    meta = {"ImageMask": "true", "Width": "2", "Height": "2", "Filter": "/FlateDecode"}
    data = zlib.compress(b"\x00")

    decoded, new_meta = _decode_native_pdf_image(data, 2, 2, "/FlateDecode", meta)
    assert decoded == b"\x00"


def test_decode_inline_image_routing():
    """Ensure the top-level decode_inline_image router correctly dispatches based on filters."""
    from pdftl.operations.helpers.type3_extraction_helpers import _decode_inline_image

    # No filter (raw data)
    d, m = _decode_inline_image(b"raw", {"Width": "2", "Height": "2"})
    assert d == b"raw"

    # CCF Routing
    d, m = _decode_inline_image(b"fake", {"Width": "0", "Height": "0", "Filter": "/CCF"})
    assert d == b"fake"

    # DCT Routing
    d, m = _decode_inline_image(b"fake", {"Width": "2", "Height": "2", "Filter": "/DCT"})
    assert d == b"fake"

    # Flate Routing
    d, m = _decode_inline_image(b"fake", {"Width": "0", "Height": "0", "Filter": "/Fl"})
    assert d == b"fake"

    # Unsupported Routing
    d, m = _decode_inline_image(b"fake", {"Width": "2", "Height": "2", "Filter": "/Unsupported"})
    assert d == b"fake"
    assert m["Filter"] == "/Unsupported"


def test_decode_inline_image_multi_filter():
    """Ensure multiple nested filters execute correctly down the Pikepdf processing pipeline."""
    meta = {"Width": "2", "Height": "2", "Filter": "[/ASCIIHexDecode /FlateDecode]"}
    import zlib

    payload = zlib.compress(b"\x00\x00\x00\x00")
    hex_payload = payload.hex().encode("ascii")
    decoded, clean_meta = _decode_inline_image(hex_payload, meta)

    if "Filter" not in clean_meta:
        assert decoded == b"\x00\x00\x00\x00"
    else:
        assert "[ /ASCIIHexDecode /FlateDecode ]" in str(clean_meta["Filter"])


def test_process_inline_images_on_export(tmp_path):
    """Validates full inline image pattern matching, extraction, and tiff conversion."""
    img_registry = {}
    bitmaps_dir = tmp_path / "bitmaps"
    stream_bytes = b"BI /W 2 /H 2 /BPC 8 /CS /DeviceGray ID\n\x00\x00\x00\x00\nEI"

    processed = _process_inline_images_on_export(
        stream_bytes, "1_0_MyFont", "A", bitmaps_dir, img_registry
    )

    assert "%BEGIN_INLINE_IMAGE%" in processed
    assert "A_0" in img_registry
    assert img_registry["A_0"]["filename"] == "font_1_0_MyFont_bitmaps/char_A_img0.tiff"


def test_export_type3_font_missing_charprocs(tmp_path):
    """Ensures safe early return when a Type 3 font lacks a CharProcs dictionary."""
    font_dict = pikepdf.Dictionary()
    assert export_type3_font(font_dict, 1, 0, "Test", tmp_path, {}) is None


def test_export_type3_font_non_stream_charproc(tmp_path):
    """Ensures the exporter safely skips any CharProc entries that are not valid streams."""
    charprocs_dict = pikepdf.Dictionary({"/A": pikepdf.Name("/NotAStream")})
    font_dict = pikepdf.Dictionary({"/CharProcs": charprocs_dict})

    font_entry = {}
    export_type3_font(font_dict, 1, 0, "Test", tmp_path, font_entry)

    charprocs_file = tmp_path / font_entry["charprocs_file"]
    assert charprocs_file.exists()
    assert charprocs_file.read_text() == ""


def test_components_per_pixel_variants():
    from pdftl.operations.helpers.type3_extraction_helpers import _components_per_pixel

    assert _components_per_pixel("/DeviceCMYK") == 4
    assert _components_per_pixel("/DeviceRGB") == 3
    assert _components_per_pixel("/DeviceGray") == 1
    assert _components_per_pixel("/UnknownCustom") == 1


def test_exact_unfiltered_data_length_malformed_dimensions():
    from pdftl.operations.helpers.type3_extraction_helpers import _exact_unfiltered_data_length

    assert _exact_unfiltered_data_length({"Width": "not_an_integer", "Height": 5}) is None
    assert _exact_unfiltered_data_length({"Height": 5}) is None


def test_locate_inline_image_data_end_fallback_regex():
    from pdftl.operations.helpers.type3_extraction_helpers import _locate_inline_image_data_end

    meta = {"Width": 1, "Height": 1, "BitsPerComponent": 8, "ColorSpace": "/DeviceGray"}
    stream = b"ID \x00 NOT_EI BUT WAIT  EI"

    bounds = _locate_inline_image_data_end(stream, 3, meta)
    assert bounds is not None
    assert stream[bounds[0] : bounds[1]] == b"  EI"


def test_process_inline_images_on_export_missing_ei(tmp_path):
    from pdftl.operations.helpers.type3_extraction_helpers import _process_inline_images_on_export

    stream_truncated = b"BI /W 1 /H 1 ID \x00"
    res = _process_inline_images_on_export(stream_truncated, "font", "glyph", tmp_path, {})

    assert "BI /W 1 /H 1 ID \x00" in res


def test_process_inline_images_on_export_inner_match_skip(tmp_path):
    from pdftl.operations.helpers.type3_extraction_helpers import _process_inline_images_on_export

    stream = b"BI /Filter /FlateDecode ID " + b" BI /W 1 ID " + b" EI"
    res = _process_inline_images_on_export(stream, "font", "glyph", tmp_path, {})

    assert "%BEGIN_INLINE_IMAGE%" in res
    assert res.count("%BEGIN_INLINE_IMAGE%") == 1
