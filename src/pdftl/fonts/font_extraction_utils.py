# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/fonts/font_extraction_utils.py

"""Reusable font dictionary crawling and bitmask parsing primitives"""

import re
from contextlib import suppress
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
        with suppress(AttributeError, KeyError, IndexError):
            descendants = font_obj.DescendantFonts
            if len(descendants) > 0:
                return find_font_descriptor(descendants[0])

    return None


def _get_font_bytes(descriptor: Any) -> tuple[bool, int]:
    """Helper to safely extract the byte length of the embedded font file."""
    for key in ("/FontFile", "/FontFile2", "/FontFile3"):
        if key not in descriptor:
            continue
        try:
            return True, len(descriptor[key].read_raw_bytes())
        except (AttributeError, TypeError):
            if hasattr(descriptor[key], "Length1"):
                with suppress(AttributeError, TypeError, ValueError):
                    return True, int(descriptor[key].Length1)
        return True, 0
    return False, 0


def _get_font_metrics(descriptor: Any) -> dict[str, Any]:
    """Helper to safely extract typography metrics from a Font Descriptor."""
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
            with suppress(ValueError, TypeError):
                metrics[json_key] = float(descriptor[pdf_key])

    if "/FontBBox" in descriptor:
        with suppress(ValueError, TypeError):
            metrics["bbox"] = [float(x) for x in descriptor["/FontBBox"]]

    return metrics


def get_font_properties(font_obj: Any) -> tuple[bool, int, dict[str, bool], dict[str, Any]]:
    """Extracts embedding verification state, accurate stream byte metrics, traits, and metrics."""
    if font_obj.get("/Subtype") == "/Type3":
        return True, 0, {}, {}

    descriptor = find_font_descriptor(font_obj)
    if not descriptor:
        return False, 0, {}, {}

    is_embedded, font_bytes = _get_font_bytes(descriptor)

    flags_raw = descriptor.get("/Flags")
    flags_parsed = parse_font_flags(int(flags_raw)) if flags_raw else {}

    metrics = _get_font_metrics(descriptor)

    return is_embedded, font_bytes, flags_parsed, metrics


def _get_fallback_encoding(subtype: str, base_font: str) -> str:
    """Helper providing standard Poppler encoding fallbacks."""
    if subtype == "Type 3":
        return "Custom"
    if subtype in ("Type 1", "Type1") and base_font in STANDARD_14_BASE_FONTS:
        return "Standard"
    if subtype in ("TrueType", "True Type"):
        return "WinAnsi"
    return "Builtin"


def _strip_encoding_suffix(enc_name: str) -> str:
    """Removes the 'Encoding' suffix from a string if present."""
    return enc_name[:-8] if enc_name.endswith("Encoding") else enc_name


def _parse_dict_encoding(enc: Any) -> str:
    """Extracts base encoding from a Dictionary-like object."""
    if "/Differences" in enc:
        return "Custom"

    base_enc = None
    if hasattr(enc, "BaseEncoding"):
        base_enc = str(enc.BaseEncoding).lstrip("/")
    elif "/BaseEncoding" in enc:
        base_enc = str(enc["/BaseEncoding"]).lstrip("/")

    if base_enc:
        return _strip_encoding_suffix(base_enc)
    return "Custom"


def _parse_explicit_encoding(enc: Any) -> str:
    """Helper to cleanly extract string encoding from complex PDF objects."""
    import pikepdf

    if isinstance(enc, pikepdf.Name):
        return _strip_encoding_suffix(str(enc).lstrip("/"))

    if isinstance(enc, pikepdf.Dictionary) or hasattr(enc, "get"):
        return _parse_dict_encoding(enc)

    enc_str = str(enc).lstrip("/")
    if "pikepdf.Dictionary" in enc_str:
        return "Custom"
    return enc_str.replace("Encoding", "")


def get_encoding_name(
    font_obj: Any, subtype: str = "", base_font: str = "", traits: dict[str, bool] | None = None
) -> str:
    """Extracts a clean serialization string representation matching pdffonts encoding logic."""
    if "/Encoding" not in font_obj:
        return _get_fallback_encoding(subtype, base_font)

    try:
        return _parse_explicit_encoding(font_obj.Encoding)
    except (AttributeError, KeyError, TypeError, ValueError):
        return "Unknown"


def _unwrap_physical_font(parent_obj: Any) -> Any:
    """Bypasses Type0 Wrapper to inspect the underlying CID physical font."""
    if parent_obj.get("/Subtype") == "/Type0" and "/DescendantFonts" in parent_obj:
        with suppress(AttributeError, KeyError, IndexError):
            descendants = parent_obj.DescendantFonts
            if len(descendants) > 0:
                return descendants[0]
    return parent_obj


def _resolve_font_name(physical_obj: Any, parent_obj: Any) -> tuple[str, str, bool]:
    """Extracts raw name, base name without subsets, and subset boolean."""
    raw_name = ""
    if "/BaseFont" in physical_obj:
        raw_name = str(physical_obj["/BaseFont"]).lstrip("/")
    elif "/BaseFont" in parent_obj:
        raw_name = str(parent_obj["/BaseFont"]).lstrip("/")

    raw_name = raw_name or "[none]"

    is_subset = False
    clean_font_name = raw_name
    if raw_name != "[none]":
        is_subset = bool(SUBSET_PREFIX_RE.match(raw_name))
        clean_font_name = raw_name.split("+")[-1] if is_subset else raw_name

    return raw_name, clean_font_name, is_subset


def _refine_subtype(subtype_raw: str, physical_obj: Any) -> str:
    """Deep inspects for Type 1C vs Type 1, and applies exact syntax mappings."""
    subtype = subtype_raw
    descriptor = find_font_descriptor(physical_obj)

    if descriptor and "/FontFile3" in descriptor:
        with suppress(AttributeError, KeyError, TypeError):
            ff3 = descriptor["/FontFile3"]
            if "/Subtype" in ff3:
                ff3_sub = str(ff3.get("/Subtype", "")).lstrip("/")
                if ff3_sub == "Type1C":
                    subtype = "Type 1C"
                elif ff3_sub == "CIDFontType0C":
                    subtype = "CID Type 0C"

    mapping = {
        "Type1": "Type 1",
        "Type0": "Type 0",
        "Type3": "Type 3",
        "CIDFontType0": "CID Type 0",
        "CIDFontType2": "CID TrueType",
    }
    return mapping.get(subtype, subtype)


def process_single_font(font_key: str, parent_obj: Any) -> dict[str, Any] | None:
    """Parses a single font object, extracting exact metadata matching Poppler's pdffonts."""
    import pikepdf

    if not isinstance(parent_obj, (dict, pikepdf.Dictionary, pikepdf.Stream)):
        return None

    physical_obj = _unwrap_physical_font(parent_obj)
    if not isinstance(physical_obj, (dict, pikepdf.Dictionary, pikepdf.Stream)):
        return None

    raw_name, clean_font_name, is_subset = _resolve_font_name(physical_obj, parent_obj)

    subtype_raw = str(physical_obj.get("/Subtype", "Unknown")).lstrip("/")
    subtype = _refine_subtype(subtype_raw, physical_obj)

    is_embedded, font_bytes, traits, metrics = get_font_properties(physical_obj)

    descriptor = find_font_descriptor(physical_obj)
    descriptor_name = ""
    if descriptor and "/FontName" in descriptor:
        descriptor_name = str(descriptor["/FontName"]).lstrip("/")

    obj_id = parent_obj.objgen[0] if hasattr(parent_obj, "objgen") and parent_obj.objgen else None

    encoding = get_encoding_name(parent_obj, subtype, clean_font_name, traits)
    has_to_unicode = "/ToUnicode" in parent_obj or "/ToUnicode" in physical_obj

    return {
        "name": raw_name,
        "resource_name": str(font_key).lstrip("/"),
        "base_font": clean_font_name,
        "descriptor_font": descriptor_name,
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


class _DocumentFontExtractor:
    """Stateful helper to crawl PDF resources without deep recursive closure nesting."""

    def __init__(self):
        self.seen_font_ids = set()
        self.seen_dict_ids = set()
        self.all_fonts: list[dict[str, Any]] = []

    def _mark_dict_seen(self, obj: Any) -> bool:
        """Returns True if the dictionary object has already been processed."""
        obj_id = obj.objgen[0] if hasattr(obj, "objgen") and obj.objgen else id(obj)
        if obj_id in self.seen_dict_ids:
            return True
        self.seen_dict_ids.add(obj_id)
        return False

    def _process_and_store_font(self, font_key: str, font_obj: Any) -> None:
        """Processes a single font and stores it if it hasn't been seen."""
        font_data = process_single_font(font_key, font_obj)
        if not font_data:
            return

        obj_id = font_data.get("obj_id")
        if obj_id is not None:
            if obj_id in self.seen_font_ids:
                return
            self.seen_font_ids.add(obj_id)

        self.all_fonts.append(font_data)

    def crawl_resources(self, resources: Any) -> None:
        if resources is None:
            return

        self._crawl_fonts(resources)
        self._crawl_xobjects(resources)
        self._crawl_patterns(resources)
        self._crawl_extgstates(resources)

    def _crawl_fonts(self, resources: Any) -> None:
        if "/Font" not in resources:
            return
        with suppress(AttributeError, KeyError, TypeError):
            for font_key, font_obj in resources.Font.items():
                if self._mark_dict_seen(font_obj):
                    continue
                if "/Resources" in font_obj:
                    self.crawl_resources(font_obj.Resources)
                self._process_and_store_font(str(font_key), font_obj)

    def _crawl_xobjects(self, resources: Any) -> None:
        if "/XObject" not in resources:
            return
        with suppress(AttributeError, KeyError, TypeError):
            for _, xobj in resources.XObject.items():
                if self._mark_dict_seen(xobj):
                    continue
                if "/Resources" in xobj:
                    self.crawl_resources(xobj.Resources)

    def _crawl_patterns(self, resources: Any) -> None:
        if "/Pattern" not in resources:
            return
        with suppress(AttributeError, KeyError, TypeError):
            for _, pat in resources.Pattern.items():
                if self._mark_dict_seen(pat):
                    continue
                if "/Resources" in pat:
                    self.crawl_resources(pat.Resources)

    def _process_single_extgstate(self, gs_key: str, gs: Any) -> None:
        if "/Font" not in gs:
            return
        with suppress(AttributeError, KeyError, TypeError, IndexError):
            font_arr = gs.Font
            if len(font_arr) > 0:
                self._process_and_store_font(f"{gs_key}_ExtGState", font_arr[0])

    def _crawl_extgstates(self, resources: Any) -> None:
        if "/ExtGState" not in resources:
            return
        with suppress(AttributeError, KeyError, TypeError):
            for gs_key, gs in resources.ExtGState.items():
                if not self._mark_dict_seen(gs):
                    self._process_single_extgstate(str(gs_key), gs)

    def crawl_page(self, page: Any) -> None:
        """Entry point for a single page, processing its Resources and Annotations."""
        with suppress(AttributeError, TypeError):
            if "/Resources" in page:
                self.crawl_resources(page.Resources)

            if "/Annots" in page:
                self._crawl_annots(page.Annots)

    def _crawl_ap_state(self, ap_state: Any) -> None:
        """Crawls an individual Annotation Appearance state mapping."""
        if not hasattr(ap_state, "get"):
            return

        if "/Resources" in ap_state:
            self.crawl_resources(ap_state.Resources)
        else:
            for _, sub_ap in ap_state.items():
                if hasattr(sub_ap, "get") and "/Resources" in sub_ap:
                    self.crawl_resources(sub_ap.Resources)

    def _crawl_single_annot_ap(self, ap_dict: Any) -> None:
        """Iterates over common Annotation Appearance dictionary keys."""
        for ap_key in ("/N", "/D", "/R"):
            if ap_key in ap_dict:
                self._crawl_ap_state(ap_dict[ap_key])

    def _crawl_annots(self, annots: Any) -> None:
        """Crawls Annotation Appearance (AP) streams."""
        for annot in annots:
            if "/AP" in annot:
                self._crawl_single_annot_ap(annot.AP)


def extract_document_fonts(
    doc: Any, page_indices: list[int] | range | None = None
) -> list[dict[str, Any]]:
    extractor = _DocumentFontExtractor()
    pages_to_crawl = []

    if page_indices is not None:
        for idx in page_indices:
            try:
                pages_to_crawl.append(doc.pages[idx])
            except (IndexError, TypeError):
                continue
    else:
        with suppress(AttributeError):
            pages_to_crawl = doc.pages

    for page in pages_to_crawl:
        extractor.crawl_page(page)

    return sorted(
        extractor.all_fonts, key=lambda x: x["obj_id"] if x["obj_id"] is not None else float("inf")
    )
