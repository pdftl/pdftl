# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/barcode_utils.py

"""Shared utility for scanning and generating barcodes via zxing-cpp."""

import logging
from typing import Any

# Import internal utilities at the top level where they belong
from pdftl.utils.dependencies import ensure_dependencies

logger = logging.getLogger(__name__)


def _get_zxing():
    """Lazy-load the optional third-party library using top-level checker."""
    ensure_dependencies("barcode", ["zxingcpp"], "barcode")
    import zxingcpp

    return zxingcpp


def scan_image(pil_image) -> list[dict[str, Any]]:
    """
    Scans a PIL image for barcodes and returns a list of dictionaries.

    Returning native Python dictionaries keeps the CLI/business logic layer
    agnostic to zxingcpp's specific C++ wrapper objects, making it trivial
    to serialize the output to JSON.

    Args:
        pil_image: A PIL Image object.

    Returns:
        A list of dictionaries containing barcode data and metadata.
    """
    zxingcpp = _get_zxing()
    results = zxingcpp.read_barcodes(pil_image)

    barcodes = []
    for result in results:
        barcodes.append(
            {
                "text": result.text,
                "format": result.format.name,  # e.g., 'Code128', 'QRCode'
                "content_type": result.content_type.name,  # e.g., 'Text', 'Binary'
                "position": {  # Bounding box points
                    "top_left": (result.position.top_left.x, result.position.top_left.y),
                    "bottom_right": (
                        result.position.bottom_right.x,
                        result.position.bottom_right.y,
                    ),
                },
            }
        )

    return barcodes


def scan_pdf_pages(
    pdf, dpi: float = 150.0, page_indices: list[int] | None = None
) -> dict[int, list[dict[str, Any]]]:
    """
    Scans specified pages of a pikepdf.Pdf object for barcodes.

    Leverages `iter_pages_as_pil` to serialize the PDF only once, scanning
    images as they are yielded.

    Args:
        pdf:          An open pikepdf.Pdf object.
        dpi:          Render resolution. 150-200 is generally ideal for zxingcpp.
        page_indices: Optional list of 0-based page indices to render.

    Returns:
        A dictionary mapping 0-based page indices to a list of found barcodes.
        Pages with no barcodes are omitted from the returned dictionary.
    """
    from pdftl.utils.page_images import iter_pages_as_pil

    found_barcodes = {}

    for page_index, pil_image in iter_pages_as_pil(pdf, dpi=dpi, page_indices=page_indices):
        results = scan_image(pil_image)
        if results:
            found_barcodes[page_index] = results
            logger.debug("Found %d barcode(s) on page %d.", len(results), page_index)

    return found_barcodes


def generate_barcode(text: str, format_name: str = "QRCode", scale: int = 5):
    """
    Generates a barcode image from text.

    Args:
        text:        The data payload to encode.
        format_name: String name of the barcode format (e.g., 'QRCode', 'Code128').
        scale:       Integer multiplier for the visual size of the output.

    Returns:
        A PIL Image object containing the generated barcode.

    Raises:
        ValueError: If the requested barcode format is invalid.
    """
    zxingcpp = _get_zxing()
    from PIL import Image

    try:
        barcode_format = getattr(zxingcpp.BarcodeFormat, format_name)
    except AttributeError as exc:
        available = [f for f in dir(zxingcpp.BarcodeFormat) if not f.startswith("_")]
        raise ValueError(
            f"Invalid barcode format '{format_name}'. Available formats: {', '.join(available)}"
        ) from exc

    barcode = zxingcpp.create_barcode(text, barcode_format)
    img_array = barcode.to_image(scale=scale)

    return Image.fromarray(img_array)
