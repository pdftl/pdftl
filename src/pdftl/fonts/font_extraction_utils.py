# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/fonts/font_extraction_utils.py

"""Reusable font dictionary crawling and bitmask parsing primitives"""

import re
from typing import Any

# Matches standard PDF subset prefixes like "AAAAAA+FontName"
SUBSET_PREFIX_RE = re.compile(r"^[A-Z]{6}\+")

STANDARD_14_BASE_FONTS = {
    "Courier",
    "Courier-Bold",
    "Courier-Oblique",
    "Courier-BoldOblique",
    "Helvetica",
    "Helvetica-Bold",
    "Helvetica-Oblique",
    "Helvetica-BoldOblique",
    "Times-Roman",
    "Times-Bold",
    "Times-Italic",
    "Times-BoldItalic",
}


def parse_font_flags(flags_int: int) -> dict[str, bool]:
    """Decodes the standard PDF Font Descriptor /Flags 32-bit integer bitmask."""
    return {
        "fixed_pitch": bool(flags_int & (1 << 0)),
        "serif": bool(flags_int & (1 << 1)),
        "symbolic": bool(flags_int & (1 << 2)),
        "script": bool(flags_int & (1 << 3)),
        "nonsymbolic": bool(flags_int & (1 << 5)),
        "italic": bool(flags_int & (1 << 6)),
        "all_cap": bool(flags_int & (1 << 16)),
        "small_cap": bool(flags_int & (1 << 17)),
        "force_bold": bool(flags_int & (1 << 18)),
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

    for pdf_key, json_key in metric_map.items():
        if pdf_key in descriptor:
            try:
                metrics[json_key] = float(descriptor[pdf_key])
            except (ValueError, TypeError):
                pass

    if "/FontBBox" in descriptor:
        try:
            metrics["bbox"] = [float(x) for x in descriptor["/FontBBox"]]
        except (ValueError, TypeError):
            pass

    return is_embedded, font_bytes, flags_parsed, metrics


def get_encoding_name(
    font_obj: Any, subtype: str = "", base_font: str = "", traits: dict[str, bool] | None = None
) -> str:
    """Extracts a clean serialization string representation matching pdffonts encoding logic."""
    if traits is None:
        traits = {}

    if subtype == "Type 3":
        return "Custom"

    if "/Encoding" not in font_obj:
        # 1. Type 1 Base-14 fonts strictly default to StandardEncoding
        if subtype in ("Type 1", "Type1") and base_font in STANDARD_14_BASE_FONTS:
            return "Standard"

        # 2. TrueType fallbacks
        if subtype in ("TrueType", "True Type"):
            # Poppler defaults un-encoded TrueType fonts to WinAnsi, ignoring
            # the symbolic flag which is often incorrectly set by PDF generators.
            return "WinAnsi"

        # 3. Absolute last resort
        return "Builtin"

    enc = font_obj.Encoding
    try:
        import pikepdf

        if isinstance(enc, pikepdf.Name):
            enc_name = str(enc).lstrip("/")
            return enc_name[:-8] if enc_name.endswith("Encoding") else enc_name

        if isinstance(enc, pikepdf.Dictionary) or hasattr(enc, "get"):
            if "/Differences" in enc:
                return "Custom"

            base_enc = None
            if hasattr(enc, "BaseEncoding"):
                base_enc = str(enc.BaseEncoding).lstrip("/")
            elif "/BaseEncoding" in enc:
                base_enc = str(enc["/BaseEncoding"]).lstrip("/")

            if base_enc:
                return base_enc[:-8] if base_enc.endswith("Encoding") else base_enc
            return "Custom"

        enc_str = str(enc).lstrip("/")
        if "pikepdf.Dictionary" in enc_str:
            return "Custom"
        return enc_str.replace("Encoding", "")
    except (AttributeError, KeyError, TypeError, ValueError):
        return "Unknown"


def process_single_font(font_key: str, parent_obj: Any) -> dict[str, Any] | None:
    """
    Parses a single font object. Resolves Type 0 composite fonts down to their physical
    Descendant stream to extract exact metadata matching Poppler's pdffonts standard.
    """
    if not hasattr(parent_obj, "get"):
        return None

    physical_obj = parent_obj

    # Bypass Type0 Wrapper to inspect the underlying CID physical font
    if parent_obj.get("/Subtype") == "/Type0" and "/DescendantFonts" in parent_obj:
        try:
            descendants = parent_obj.DescendantFonts
            if len(descendants) > 0:
                physical_obj = descendants[0]
        except (AttributeError, KeyError, IndexError):
            pass

    subtype_raw = str(physical_obj.get("/Subtype", "Unknown")).lstrip("/")

    raw_name = ""
    if "/BaseFont" in physical_obj:
        raw_name = str(physical_obj["/BaseFont"]).lstrip("/")
    elif "/BaseFont" in parent_obj:
        raw_name = str(parent_obj["/BaseFont"]).lstrip("/")

    raw_name = raw_name or "[none]"
    subtype = subtype_raw

    is_embedded, font_bytes, traits, metrics = get_font_properties(physical_obj)

    # Deep inspect for Type 1C (Compact Font Format) vs Type 1
    descriptor = find_font_descriptor(physical_obj)
    if descriptor and "/FontFile3" in descriptor:
        try:
            ff3 = descriptor["/FontFile3"]
            if "/Subtype" in ff3:
                ff3_sub = str(ff3.get("/Subtype", "")).lstrip("/")
                if ff3_sub == "Type1C":
                    subtype = "Type 1C"
                elif ff3_sub == "CIDFontType0C":
                    subtype = "CID Type 0C"
        except (AttributeError, KeyError, TypeError):
            pass

    # Exact syntax mapping to match pdffonts
    if subtype == "Type1":
        subtype = "Type 1"
    elif subtype == "Type0":
        subtype = "Type 0"
    elif subtype == "Type3":
        subtype = "Type 3"
    elif subtype == "CIDFontType0":
        subtype = "CID Type 0"
    elif subtype == "CIDFontType2":
        subtype = "CID TrueType"

    is_subset = False
    clean_font_name = raw_name
    if raw_name != "[none]":
        is_subset = bool(SUBSET_PREFIX_RE.match(raw_name))
        clean_font_name = raw_name.split("+")[-1] if is_subset else raw_name

    # Poppler outputs the parent dictionary reference (the layout spec), not the descendant stream
    # ID
    obj_id = parent_obj.objgen[0] if hasattr(parent_obj, "objgen") and parent_obj.objgen else None

    # Encodings apply to the composite parent layout
    encoding = get_encoding_name(parent_obj, subtype, clean_font_name, traits)
    has_to_unicode = "/ToUnicode" in parent_obj or "/ToUnicode" in physical_obj

    return {
        "name": raw_name,  # Raw PostScript name (matches pdffonts column)
        "resource_name": str(font_key).lstrip("/"),  # Local page dictionary mapping (e.g. "F1")
        "base_font": clean_font_name,  # Subsets stripped
        "subtype": subtype,
        "is_embedded": is_embedded,
        "font_bytes": font_bytes,
        "is_subset": is_subset,
        "encoding": encoding,
        "has_to_unicode": has_to_unicode,
        "traits": traits,
        "metrics": metrics,
        "obj_id": obj_id,
    }


def extract_resource_fonts(resources: Any) -> list[dict[str, Any]]:
    font_list: list[dict[str, Any]] = []
    if resources is None or "/Font" not in resources:
        return font_list

    for font_key, font_obj in resources.Font.items():
        res = process_single_font(str(font_key), font_obj)
        if res:
            font_list.append(res)

    return font_list


def extract_document_fonts(
    doc: Any, page_indices: list[int] | range | None = None
) -> list[dict[str, Any]]:
    seen_font_ids = set()
    seen_dict_ids = set()
    all_fonts: list[dict[str, Any]] = []

    def crawl_resources(resources: Any) -> None:
        if resources is None:
            return

        if "/Font" in resources:
            try:
                for font_key, font_obj in resources.Font.items():
                    f_id = (
                        font_obj.objgen[0]
                        if hasattr(font_obj, "objgen") and font_obj.objgen
                        else id(font_obj)
                    )
                    if f_id in seen_dict_ids:
                        continue
                    seen_dict_ids.add(f_id)

                    if "/Resources" in font_obj:
                        crawl_resources(font_obj.Resources)

                    font_data = process_single_font(str(font_key), font_obj)
                    if font_data:
                        obj_id = font_data.get("obj_id")
                        if obj_id is not None:
                            if obj_id in seen_font_ids:
                                continue
                            seen_font_ids.add(obj_id)
                        all_fonts.append(font_data)
            except (AttributeError, KeyError, TypeError):
                pass

        if "/XObject" in resources:
            try:
                for _, xobj in resources.XObject.items():
                    x_id = xobj.objgen[0] if hasattr(xobj, "objgen") and xobj.objgen else id(xobj)
                    if x_id in seen_dict_ids:
                        continue
                    seen_dict_ids.add(x_id)

                    if "/Resources" in xobj:
                        crawl_resources(xobj.Resources)
            except (AttributeError, KeyError, TypeError):
                pass

        if "/Pattern" in resources:
            try:
                for _, pat in resources.Pattern.items():
                    p_id = pat.objgen[0] if hasattr(pat, "objgen") and pat.objgen else id(pat)
                    if p_id in seen_dict_ids:
                        continue
                    seen_dict_ids.add(p_id)

                    if "/Resources" in pat:
                        crawl_resources(pat.Resources)
            except (AttributeError, KeyError, TypeError):
                pass

        if "/ExtGState" in resources:
            try:
                for gs_key, gs in resources.ExtGState.items():
                    gs_id = gs.objgen[0] if hasattr(gs, "objgen") and gs.objgen else id(gs)
                    if gs_id in seen_dict_ids:
                        continue
                    seen_dict_ids.add(gs_id)

                    if "/Font" in gs:
                        try:
                            font_arr = gs.Font
                            if len(font_arr) > 0:
                                f_obj = font_arr[0]
                                font_data = process_single_font(f"{gs_key}_ExtGState", f_obj)
                                if font_data:
                                    fid = font_data.get("obj_id")
                                    if fid is not None:
                                        if fid in seen_font_ids:
                                            continue
                                        seen_font_ids.add(fid)
                                    all_fonts.append(font_data)
                        except (AttributeError, KeyError, TypeError, IndexError):
                            pass
            except (AttributeError, KeyError, TypeError):
                pass

    pages_to_crawl = []
    if page_indices is not None:
        for idx in page_indices:
            try:
                pages_to_crawl.append(doc.pages[idx])
            except (IndexError, TypeError):
                continue
    else:
        try:
            pages_to_crawl = doc.pages
        except AttributeError:
            pass

    for page in pages_to_crawl:
        try:
            if "/Resources" in page:
                crawl_resources(page.Resources)

            if "/Annots" in page:
                for annot in page.Annots:
                    if "/AP" in annot:
                        for ap_key in ("/N", "/D", "/R"):
                            if ap_key in annot.AP:
                                ap_state = annot.AP[ap_key]
                                if hasattr(ap_state, "get"):
                                    if "/Resources" in ap_state:
                                        crawl_resources(ap_state.Resources)
                                    else:
                                        for _, sub_ap in ap_state.items():
                                            if hasattr(sub_ap, "get") and "/Resources" in sub_ap:
                                                crawl_resources(sub_ap.Resources)
        except (AttributeError, TypeError):
            continue

    return sorted(
        all_fonts, key=lambda x: x["obj_id"] if x["obj_id"] is not None else float("inf")
    )
