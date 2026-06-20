# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/images/embedder.py

"""
Image embedding utilities for native PDF injection.

This module provides tools to lazily read image files from disk and package
them directly into native pikepdf Image XObjects (Streams). It is designed
to strictly avoid generation loss by injecting JPEG/JPEG2000 bytes directly
into the PDF stream, utilizing optimal CCITT Group 4 compression for 1-bit
monochrome images, and automatically handling alpha channels (transparency)
via PDF Soft Masks (/SMask).
"""

from __future__ import annotations

import zlib
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pikepdf

from pdftl.utils.images.pil_to_pdf import get_colorspace_dict, get_optimal_1bit_payload


def create_image_xobject(pdf: pikepdf.Pdf, filepath: Path | str) -> pikepdf.Stream:
    """
    Lazily reads an image from disk and wraps it into a native PDF Image XObject,
    using zero-re-encoding paths for supported formats (JPEG) and optimal
    lossless paths (CCITT/Flate) for everything else.
    """
    from pikepdf import Name
    from PIL import Image

    filepath = Path(filepath)
    img = Image.open(filepath)
    img_format = img.format
    orig_mode = img.mode

    # 1. Handle Transparency (PDF requires a separate /SMask stream for alpha)
    has_alpha = orig_mode in ("RGBA", "LA") or (orig_mode == "P" and "transparency" in img.info)
    alpha_stream = None

    if has_alpha:
        # Extract the alpha channel as a standalone L-mode (grayscale) image
        alpha_channel = img.convert("RGBA").getchannel("A")
        alpha_bytes = zlib.compress(alpha_channel.tobytes(), level=9)

        # Create the Soft Mask XObject
        alpha_stream = pdf.make_stream(alpha_bytes)
        alpha_stream.Type = Name("/XObject")
        alpha_stream.Subtype = Name("/Image")
        alpha_stream.Width = img.width
        alpha_stream.Height = img.height
        alpha_stream.ColorSpace = Name("/DeviceGray")
        alpha_stream.BitsPerComponent = 8
        alpha_stream.Filter = Name("/FlateDecode")

        # Flatten the main image to remove the alpha channel for the primary stream
        base_mode = "RGB" if "RGB" in orig_mode else "L"
        img = img.convert(base_mode)

    # 2. Build the Primary XObject Stream
    if img_format in ("JPEG", "MPO") and not has_alpha:
        # ZERO-LOSS FAST PATH: Direct raw byte injection
        with open(filepath, "rb") as f:
            xobj = pdf.make_stream(f.read())
        xobj.Filter = Name("/DCTDecode")

    elif img_format == "JPEG2000" and not has_alpha:
        # ZERO-LOSS FAST PATH: JPEG2000 injection
        with open(filepath, "rb") as f:
            xobj = pdf.make_stream(f.read())
        xobj.Filter = Name("/JPXDecode")

    elif img.mode == "1":
        # LOSSLESS 1-BIT: Compare CCITT vs Flate
        best_bytes, best_filter, decode_parms = get_optimal_1bit_payload(img)
        xobj = pdf.make_stream(best_bytes)
        xobj.Filter = Name(best_filter)
        if decode_parms is not None:
            xobj.DecodeParms = decode_parms

    else:
        # LOSSLESS FALLBACK: Raw pixels + Zlib (Flate)
        raw_pixels = img.tobytes()
        compressed = zlib.compress(raw_pixels, level=9)
        xobj = pdf.make_stream(compressed)
        xobj.Filter = Name("/FlateDecode")

    # 3. Populate standard required PDF Dictionary entries
    xobj.Type = Name("/XObject")
    xobj.Subtype = Name("/Image")
    xobj.Width = img.width
    xobj.Height = img.height

    # Delegate the complex ColorSpace translations
    cs_dict, bpc = get_colorspace_dict(img)
    xobj.ColorSpace = cs_dict
    xobj.BitsPerComponent = bpc

    # 4. Attach the Soft Mask if transparency was detected
    if alpha_stream is not None:
        xobj.SMask = alpha_stream

    return xobj
