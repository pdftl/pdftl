# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/utils/images/test_pil_to_pdf.py

import struct
import zlib
from unittest.mock import patch

import pikepdf
from PIL import Image

from pdftl.utils.images.pil_to_pdf import (
    get_colorspace_dict,
    get_optimal_1bit_payload,
    _needs_inversion,
    _get_ccitt_bytes,
    _extract_raw_ccitt_from_tiff,
)


# --- 1. Colorspace Translation Tests ---


def test_get_colorspace_dict_standard():
    # Test standard modes: "1", "L", "RGB", "CMYK"
    img_1 = Image.new("1", (10, 10))
    cs, bpc = get_colorspace_dict(img_1)
    assert cs == pikepdf.Name("/DeviceGray")
    assert bpc == 1

    img_L = Image.new("L", (10, 10))
    cs, bpc = get_colorspace_dict(img_L)
    assert cs == pikepdf.Name("/DeviceGray")
    assert bpc == 8

    img_rgb = Image.new("RGB", (10, 10))
    cs, bpc = get_colorspace_dict(img_rgb)
    assert cs == pikepdf.Name("/DeviceRGB")
    assert bpc == 8

    img_cmyk = Image.new("CMYK", (10, 10))
    cs, bpc = get_colorspace_dict(img_cmyk)
    assert cs == pikepdf.Name("/DeviceCMYK")
    assert bpc == 8


def test_get_colorspace_dict_palette():
    # Test palette mode "P"
    img_p = Image.new("P", (10, 10))
    # Add a custom palette: Red (255, 0, 0) and Green (0, 255, 0)
    palette = [255, 0, 0, 0, 255, 0] + [0] * 762
    img_p.putpalette(palette)

    cs, bpc = get_colorspace_dict(img_p)
    assert bpc == 8
    assert isinstance(cs, pikepdf.Array)
    assert cs[0] == pikepdf.Name("/Indexed")
    assert cs[1] == pikepdf.Name("/DeviceRGB")
    assert cs[2] == 255
    assert cs[3] == bytes(palette)


def test_get_colorspace_dict_palette_empty():
    # Test palette mode "P" with empty/missing palette
    class MockImage:
        mode = "P"

        def getpalette(self):
            return None

    cs, bpc = get_colorspace_dict(MockImage())
    assert bpc == 8
    assert cs[2] == 0


def test_get_colorspace_dict_fallback():
    # Test fallback
    img_rgba = Image.new("RGBA", (10, 10))
    cs, bpc = get_colorspace_dict(img_rgba)
    assert cs == pikepdf.Name("/DeviceRGB")
    assert bpc == 8


# --- 2. Inversion Assessment Tests ---


def test_needs_inversion_black_dominant():
    # Create a mostly black image (0)
    img = Image.new("1", (10, 10), 0)
    img.putpixel((0, 0), 1)
    assert _needs_inversion(img) is False


def test_needs_inversion_white_dominant():
    # Create a mostly white image (1)
    img = Image.new("1", (10, 10), 1)
    img.putpixel((0, 0), 0)
    assert _needs_inversion(img) is True


# --- 3. Raw CCITT Generation Tests ---


def test_get_ccitt_bytes_standard():
    img = Image.new("1", (32, 32), 0)
    for i in range(0, 32, 2):
        for j in range(32):
            img.putpixel((j, i), 1)

    ccitt_normal = _get_ccitt_bytes(img, invert=False)
    ccitt_inverted = _get_ccitt_bytes(img, invert=True)
    assert isinstance(ccitt_normal, bytes)
    assert len(ccitt_normal) > 0
    assert ccitt_normal != ccitt_inverted


# --- 4. 1-Bit Payload Heuristic Tests ---


def test_get_optimal_1bit_payload_excellent_ccitt():
    # Large monochrome image compresses excellently under CCITT G4 (< 25% raw bytes)
    img = Image.new("1", (100, 100), 0)
    payload, filt, parms = get_optimal_1bit_payload(img)
    assert filt == "/CCITTFaxDecode"
    assert isinstance(parms, pikepdf.Dictionary)
    assert parms.Columns == 100
    assert parms.Rows == 100
    assert parms.K == -1


def test_get_optimal_1bit_payload_flate_wins():
    # Tiny dithered/complex image where zlib Flate compresses better than CCITT G4
    img = Image.new("1", (8, 8))
    with patch("pdftl.utils.images.pil_to_pdf._get_ccitt_bytes", return_value=b"A" * 1000):
        payload, filt, parms = get_optimal_1bit_payload(img)
        assert filt == "/FlateDecode"
        assert parms is None
        assert payload == zlib.compress(img.tobytes(), level=9)


def test_get_optimal_1bit_payload_ccitt_barely_wins():
    # Heuristic scenario where the ratio doesn't immediately short-circuit, but CCITT barely wins
    img = Image.new("1", (8, 8))
    with patch("pdftl.utils.images.pil_to_pdf._get_ccitt_bytes", return_value=b"12345"):
        with patch("zlib.compress", return_value=b"123456"):
            payload, filt, parms = get_optimal_1bit_payload(img)
            assert filt == "/CCITTFaxDecode"
            assert payload == b"12345"
            assert isinstance(parms, pikepdf.Dictionary)


# --- 5. Byte-Level TIFF Extraction Tests ---


def test_extract_raw_ccitt_from_tiff():
    # 1. Byte length too short
    assert _extract_raw_ccitt_from_tiff(b"short") == b"short"

    # 2. Bad Magic identifier
    bad_magic = b"II" + struct.pack("<HI", 99, 8)
    assert _extract_raw_ccitt_from_tiff(bad_magic) == bad_magic

    # 3. Little Endian, valid inline payload
    header = b"II" + struct.pack("<HI", 42, 8)
    num_entries = struct.pack("<H", 2)
    # FIX: Shifted offset from 34 to 38 to clear the 38-byte IFD structure
    entry1 = struct.pack("<HHII", 273, 4, 1, 38)  # Offset: 38
    entry2 = struct.pack("<HHII", 279, 4, 1, 4)  # Count: 4

    # 38 bytes exactly, so b"DATA" starts right at offset 38
    tiff_inline = header + num_entries + entry1 + entry2 + b"\x00" * 4 + b"DATA"

    assert _extract_raw_ccitt_from_tiff(tiff_inline) == b"DATA"

    # 4. Big Endian, valid pointer payload (Count > 1 bypasses inline limits)
    header_be = b"MM" + struct.pack(">HI", 42, 8)
    num_entries_be = struct.pack(">H", 2)
    # FIX: Shift offset to 38 to point to the pointer array
    entry1_be = struct.pack(">HHII", 273, 4, 2, 38)
    entry2_be = struct.pack(">HHII", 279, 4, 1, 5)  # Count: 5

    # Offset 38 contains the pointer array. We point the first value to offset 46.
    pointer_data = struct.pack(">I", 46) + b"\x00" * 4
    data_be = b"HELLO"  # Length 5, sits at offset 46

    tiff_pointer = (
        header_be + num_entries_be + entry1_be + entry2_be + b"\x00" * 4 + pointer_data + data_be
    )

    assert _extract_raw_ccitt_from_tiff(tiff_pointer) == b"HELLO"

    # 5. Missing critical tags bypass
    tiff_missing_tag = header + struct.pack("<H", 1) + entry1 + b"\x00" * 4 + b"DATA"
    assert _extract_raw_ccitt_from_tiff(tiff_missing_tag) == tiff_missing_tag

    # 6. Ignore non-essential tags (Coverage for line 238)
    header_ignore = b"II" + struct.pack("<HI", 42, 8)
    num_entries_ignore = struct.pack("<H", 3)  # 3 entries this time

    # Tag 256 (ImageWidth), Type 4, Count 1, Value 100
    # This is the tag that will trigger line 238!
    entry_ignored = struct.pack("<HHII", 256, 4, 1, 100)

    # We have to shift the offset to 50 to clear the larger 50-byte IFD structure
    # 8 (header) + 2 (num entries) + 36 (3x 12-byte entries) + 4 (next IFD ptr) = 50
    entry1_ignore = struct.pack("<HHII", 273, 4, 1, 50)
    entry2_ignore = struct.pack("<HHII", 279, 4, 1, 4)

    tiff_ignore_tag = (
        header_ignore
        + num_entries_ignore
        + entry_ignored
        + entry1_ignore
        + entry2_ignore
        + b"\x00" * 4
        + b"DATA"
    )

    assert _extract_raw_ccitt_from_tiff(tiff_ignore_tag) == b"DATA"
