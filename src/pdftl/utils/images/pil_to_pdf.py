# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/images/pil_to_pdf.py

"""
Low-level PDF image specification utilities.

This module acts as a pure-function translation layer between raster image
processing (PIL/Pillow) and the native PDF specification. It provides stateless
helpers to translate image properties (modes, palettes, bit depths) into exact
PDF constructs (ColorSpace dictionaries, CCITTFaxDecode payloads) without
mutating the PDF DOM or handling file I/O.
"""

from __future__ import annotations

import io
import logging
import struct
import zlib
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)


def get_colorspace_dict(pil_img: Any) -> tuple[Any, int]:
    """
    Translates a PIL image mode into a native pikepdf ColorSpace and BitsPerComponent.
    Returns: (ColorSpace Object, BitsPerComponent)
    """
    from pikepdf import Array, Name

    mode_map = {
        "1": (Name("/DeviceGray"), 1),
        "L": (Name("/DeviceGray"), 8),
        "RGB": (Name("/DeviceRGB"), 8),
        "CMYK": (Name("/DeviceCMYK"), 8),
    }
    if pil_img.mode in mode_map:
        return mode_map[pil_img.mode]

    if pil_img.mode == "P":
        # Extract the flat RGB palette list [r, g, b, r, g, b...]
        palette_data = pil_img.getpalette() or []

        # Calculate the highest valid index (e.g., 255 for a 256-color palette)
        max_index = max(0, (len(palette_data) // 3) - 1)

        # Build the exact /Indexed array PDF requires
        cs = Array(
            [
                Name("/Indexed"),
                Name("/DeviceRGB"),
                max_index,
                bytes(palette_data),
            ]
        )
        return cs, 8

    # Fallback default
    return Name("/DeviceRGB"), 8


def get_optimal_1bit_payload(pil_img: Any) -> tuple[bytes, str, pikepdf.Dictionary | None]:
    """
    Determines the most optimal lossless compression for a 1-bit image
    by testing CCITT Group 4 against Zlib Flate.

    Returns: (Payload Bytes, Filter Name, DecodeParms Dictionary or None)
    """
    import pikepdf

    raw_pixel_bytes = pil_img.tobytes()
    raw_size = len(raw_pixel_bytes)

    # 1. Determine optimal polarity and get CCITT bytes
    invert = _needs_inversion(pil_img)
    ccitt_bytes = _get_ccitt_bytes(pil_img, invert=invert)
    ccitt_size = len(ccitt_bytes)

    # 2. The Short-Circuit Heuristic
    best_bytes, best_filter = ccitt_bytes, "/CCITTFaxDecode"
    if ccitt_size < (raw_size * 0.25):
        logger.debug(
            "1-bit compression: CCITT G4 ratio is excellent (%.2f%%). Skipping Flate.",
            (ccitt_size / raw_size) * 100,
        )
    else:
        # The compression ratio is suspicious (noisy or dithered). Bring in Flate.
        flate_bytes = zlib.compress(raw_pixel_bytes, level=9)
        if len(flate_bytes) < ccitt_size:
            best_bytes, best_filter = flate_bytes, "/FlateDecode"
            logger.debug(
                "1-bit compression: Flate wins (%d vs %d bytes)", len(flate_bytes), ccitt_size
            )
        else:
            logger.debug(
                "1-bit compression: CCITT G4 barely wins (%d vs %d bytes)",
                ccitt_size,
                len(flate_bytes),
            )

    decode_parms = None
    if best_filter == "/CCITTFaxDecode":
        decode_parms = pikepdf.Dictionary(
            K=-1, Columns=pil_img.width, Rows=pil_img.height, BlackIs1=not invert
        )

    return best_bytes, best_filter, decode_parms


def _needs_inversion(pil_img: Any) -> bool:
    dominant_color_value = max(pil_img.getcolors(2), key=lambda x: x[0])[1]
    # In Pillow mode '1', 0 is Black, 255 (or 1) is White
    # If the dominant color is White, we WANT to invert it before encoding
    # so that libtiff applies the shorter 0-bit Huffman codes to the massive background.
    return dominant_color_value != 0


def _get_ccitt_bytes(pil_img: Any, invert: bool = False) -> bytes:
    img = pil_img
    if invert:
        from PIL import ImageOps

        img = ImageOps.invert(pil_img)

    ccitt_buf = io.BytesIO()
    img.save(
        ccitt_buf,
        format="TIFF",
        compression="group4",
        tiffinfo={278: img.height},  # Tag 278: RowsPerStrip
    )
    return _extract_raw_ccitt_from_tiff(ccitt_buf.getvalue())


def _extract_raw_ccitt_from_tiff(tiff_bytes: bytes) -> bytes:
    """Isolates the raw CCITT fax bitstream data out of a standard TIFF byte array
    by safely concatenating multiple data strips.
    """
    if len(tiff_bytes) < 8:
        return tiff_bytes

    endian = "<" if tiff_bytes[:2] == b"II" else ">"
    magic = struct.unpack_from(f"{endian}H", tiff_bytes, 2)[0]
    if magic != 42:
        return tiff_bytes

    strip_offsets, strip_byte_counts = _get_tiff_strip_parameters(tiff_bytes, endian)
    if not strip_offsets or not strip_byte_counts:
        return tiff_bytes

    return b"".join(
        tiff_bytes[off : off + length] for off, length in zip(strip_offsets, strip_byte_counts)
    )


def _get_tiff_strip_parameters(tiff_bytes: bytes, endian: str) -> tuple[list[int], list[int]]:
    ifd_offset = struct.unpack_from(f"{endian}I", tiff_bytes, 4)[0]
    num_entries = struct.unpack_from(f"{endian}H", tiff_bytes, ifd_offset)[0]
    curr_pos = ifd_offset + 2
    strip_offsets = []
    strip_byte_counts = []

    for _ in range(num_entries):
        tag, tag_type, count, val_or_offset = struct.unpack_from(
            f"{endian}HHII", tiff_bytes, curr_pos
        )
        curr_pos += 12

        if tag not in (273, 279):
            continue

        fmt_char = "I" if tag_type == 4 else "H"
        type_size = 4 if tag_type == 4 else 2

        values = []
        if type_size * count <= 4:
            raw_4 = struct.pack(f"{endian}I", val_or_offset)
            values = list(struct.unpack(f"{endian}{count}{fmt_char}", raw_4[: type_size * count]))
        else:
            values = list(
                struct.unpack_from(f"{endian}{count}{fmt_char}", tiff_bytes, val_or_offset)
            )

        if tag == 273:
            strip_offsets = values
        elif tag == 279:
            strip_byte_counts = values

    return strip_offsets, strip_byte_counts
