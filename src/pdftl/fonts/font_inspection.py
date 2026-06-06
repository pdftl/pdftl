# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/fonts/font_inspection.py

# Copyright (c) 2026 The pdftl developers

"""Font inspection and validation utilities"""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)

# Standard 14 fonts defined by the PDF specification.
# These are natively supported by viewers and normally not embedded.
STANDARD_14_FONTS = {
    "/Times-Roman",
    "/Times-Bold",
    "/Times-Italic",
    "/Times-BoldItalic",
    "/Helvetica",
    "/Helvetica-Bold",
    "/Helvetica-Oblique",
    "/Helvetica-BoldOblique",
    "/Courier",
    "/Courier-Bold",
    "/Courier-Oblique",
    "/Courier-BoldOblique",
    "/Symbol",
    "/ZapfDingbats",
}


@dataclass
class FontInfo:
    """Dataclass holding extracted metadata for a single font instance."""

    base_font: str
    subtype: str
    is_embedded: bool
    pages: list[int] = field(default_factory=list)


def _check_embedding(font_obj: Any) -> bool:
    """Recursively checks if a font object contains embedded binary streams."""
    # Type 3 fonts define glyphs via content streams inside the font dictionary itself
    if font_obj.get("/Subtype") == "/Type3":
        return True

    # Standard check via FontDescriptor
    if "/FontDescriptor" in font_obj:
        descriptor = font_obj.FontDescriptor
        return any(k in descriptor for k in ("/FontFile", "/FontFile2", "/FontFile3"))

    # Composite Type0 fonts nest their descriptor inside a descendant CIDFont
    if "/DescendantFonts" in font_obj:
        try:
            descendants = font_obj.DescendantFonts
            if len(descendants) > 0:
                return _check_embedding(descendants[0])
        except (AttributeError, KeyError, IndexError):
            pass

    return False


def inspect_pdf_fonts(pdf: "pikepdf.Pdf") -> dict[str, FontInfo]:
    """
    Crawls the PDF page tree to gather unique fonts and evaluate their metrics.

    Returns:
        A dictionary mapping the cleaned BaseFont name to its FontInfo metadata.
    """
    font_registry: dict[str, FontInfo] = {}

    for page_idx, page in enumerate(pdf.pages, start=1):
        resources = page.get("/Resources")
        if not resources or "/Font" not in resources:
            continue

        for _, font_obj in resources.Font.items():
            # Resolve structural indirect references safely
            if not hasattr(font_obj, "get"):
                continue

            base_font_raw = font_obj.get("/BaseFont")
            if not base_font_raw:
                continue

            base_font = str(base_font_raw)
            subtype = str(font_obj.get("/Subtype", "/Unknown"))
            is_embedded = _check_embedding(font_obj)

            if base_font not in font_registry:
                font_registry[base_font] = FontInfo(
                    base_font=base_font, subtype=subtype, is_embedded=is_embedded, pages=[page_idx]
                )
            elif page_idx not in font_registry[base_font].pages:
                font_registry[base_font].pages.append(page_idx)

    return font_registry


def list_fonts(pdf: "pikepdf.Pdf") -> list[dict[str, Any]]:
    """
    Exposes serialized font layout details per page for pdftl CLI consumption.
    """
    registry = inspect_pdf_fonts(pdf)
    return [
        {
            "font_name": info.base_font.lstrip("/"),
            "subtype": info.subtype.lstrip("/"),
            "is_embedded": info.is_embedded,
            "pages": info.pages,
        }
        for info in registry.values()
    ]


def missing_fonts(pdf: "pikepdf.Pdf") -> list[dict[str, Any]]:
    """
    Identifies non-embedded font declarations, skipping the safe Standard 14 sets.
    """
    registry = inspect_pdf_fonts(pdf)
    missing = []

    for info in registry.values():
        if not info.is_embedded and info.base_font not in STANDARD_14_FONTS:
            missing.append(
                {
                    "font_name": info.base_font.lstrip("/"),
                    "subtype": info.subtype.lstrip("/"),
                    "pages": info.pages,
                }
            )

    return missing
