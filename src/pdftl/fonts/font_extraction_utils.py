# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/fonts/font_extraction_utils.py

"""Reusable font dictionary crawling and bitmask parsing primitives"""

import re
from typing import Any

# Matches standard PDF subset prefixes like "AAAAAA+FontName"
SUBSET_PREFIX_RE = re.compile(r"^[A-Z]{6}\+")


def parse_font_flags(flags_int: int) -> dict[str, bool]:
    """Decodes the standard PDF Font Descriptor /Flags 32-bit integer bitmask."""
    return {
        "fixed_pitch": bool(flags_int & (1 << 0)),  # Bit 1
        "serif": bool(flags_int & (1 << 1)),  # Bit 2
        "symbolic": bool(flags_int & (1 << 2)),  # Bit 3
        "script": bool(flags_int & (1 << 3)),  # Bit 4
        "nonsymbolic": bool(flags_int & (1 << 5)),  # Bit 6
        "italic": bool(flags_int & (1 << 6)),  # Bit 7
        "all_cap": bool(flags_int & (1 << 16)),  # Bit 17
        "small_cap": bool(flags_int & (1 << 17)),  # Bit 18
        "force_bold": bool(flags_int & (1 << 18)),  # Bit 19
    }


def find_font_descriptor(font_obj: Any) -> Any | None:
    """Recursively locates the /FontDescriptor dictionary, handling Type0 descendants."""
    if "/FontDescriptor" in font_obj:
        return font_obj.FontDescriptor

    if "/DescendantFonts" in font_obj:
        try:
            descendants = font_obj.DescendantFonts
            if len(descendants) > 0:
                return find_font_descriptor(descendants[0])
        except (AttributeError, KeyError, IndexError):
            pass

    return None


def get_font_properties(font_obj: Any) -> tuple[bool, int, dict[str, bool], dict[str, Any]]:
    """Extracts embedding verification state, accurate stream byte metrics, traits, and typography
    metrics."""
    if font_obj.get("/Subtype") == "/Type3":
        # Type3 fonts are self-contained layouts; no external font descriptor stream exists
        return True, 0, {}, {}

    descriptor = find_font_descriptor(font_obj)
    if not descriptor:
        return False, 0, {}, {}

    is_embedded = False
    font_bytes = 0
    for key in ("/FontFile", "/FontFile2", "/FontFile3"):
        if key in descriptor:
            is_embedded = True
            try:
                font_bytes = len(descriptor[key].read_raw_bytes())
            except (AttributeError, TypeError):
                if hasattr(descriptor[key], "Length1"):
                    try:
                        font_bytes = int(descriptor[key].Length1)
                    except (AttributeError, TypeError, ValueError):
                        font_bytes = 0
            break

    flags_raw = descriptor.get("/Flags")
    flags_parsed = parse_font_flags(int(flags_raw)) if flags_raw else {}

    metrics: dict[str, Any] = {}

    # Map raw PDF keys to clean JSON output keys
    metric_map = {
        "/ItalicAngle": "italic_angle",
        "/Ascent": "ascent",
        "/Descent": "descent",
        "/CapHeight": "cap_height",
        "/XHeight": "x_height",
        "/AvgWidth": "avg_width",
        "/MaxWidth": "max_width",
        "/StemV": "stem_v",
        "/StemH": "stem_h",
    }

    # Only inject the key into our metrics dict if it exists in the PDF descriptor
    for pdf_key, json_key in metric_map.items():
        if pdf_key in descriptor:
            try:
                metrics[json_key] = float(descriptor[pdf_key])
            except (ValueError, TypeError):
                pass  # Skip silently if the PDF contains malformed non-numeric data

    # Handle the Bounding Box array separately
    if "/FontBBox" in descriptor:
        try:
            metrics["bbox"] = [float(x) for x in descriptor["/FontBBox"]]
        except (ValueError, TypeError):
            pass

    return is_embedded, font_bytes, flags_parsed, metrics


def get_encoding_name(font_obj: Any) -> str:
    """Extracts a clean serialization string representation of the character mapping."""
    if "/Encoding" not in font_obj:
        return "Standard"
    enc = font_obj.Encoding
    try:
        if hasattr(enc, "get") and "/BaseEncoding" in enc:
            return str(enc.BaseEncoding).lstrip("/")
        return str(enc).lstrip("/")
    except (AttributeError, KeyError, TypeError, ValueError):
        return "Unknown"


def extract_resource_fonts(resources: Any) -> list[dict[str, Any]]:
    """Parses a resource dictionary block and transforms discovered fonts into uniform data
    layouts.
    """
    font_list: list[dict[str, Any]] = []
    if resources is None or "/Font" not in resources:
        return font_list

    for font_key, font_obj in resources.Font.items():
        if not hasattr(font_obj, "get") or not font_obj.get("/BaseFont"):
            continue

        raw_name = str(font_obj.get("/BaseFont")).lstrip("/")
        subtype = str(font_obj.get("/Subtype", "Unknown")).lstrip("/")

        is_embedded, font_bytes, traits, metrics = get_font_properties(font_obj)
        is_subset = bool(SUBSET_PREFIX_RE.match(raw_name))
        clean_font_name = raw_name.split("+")[-1] if is_subset else raw_name

        obj_id = font_obj.objgen[0] if hasattr(font_obj, "objgen") and font_obj.objgen else None

        font_list.append(
            {
                "name": str(font_key).lstrip("/"),
                "base_font": clean_font_name,
                "subtype": subtype,
                "is_embedded": is_embedded,
                "font_bytes": font_bytes,
                "is_subset": is_subset,
                "encoding": get_encoding_name(font_obj),
                "has_to_unicode": "/ToUnicode" in font_obj,
                "traits": traits,
                "metrics": metrics,
                "obj_id": obj_id,
            }
        )
    return font_list
