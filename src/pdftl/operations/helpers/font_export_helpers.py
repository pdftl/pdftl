# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/helpers/font_export_helpers.py

"""
Helpers implementing the `export_fonts` operation: extracting embedded font
binaries and building the unified sidecar JSON/PS metadata files.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pdftl.fonts.font_binary_utils import get_font_widths_from_file
from pdftl.fonts.widths_utils import extract_cid_to_gid_map
from pdftl.operations.helpers.font_ops_shared import (
    file_hash,
    sanitize_name,
    decode_font_flags,
)
from pdftl.operations.helpers.type3_extraction_helpers import export_type3_font
from pdftl.utils.pdf_resources import get_all_fonts_recursive
from pdftl.fonts.cmap_utils import detect_predefined_identity_encoding
from pdftl.fonts.font_extraction_utils import find_font_descriptor
from pdftl.fonts.widths_utils import extract_font_widths

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)


# ============================================================================
# Export Fonts Logic
# ============================================================================


def _extract_string_keys(descriptor: Any, desc_data: dict, pikepdf: Any) -> None:
    """Helper to extract Name and String properties safely."""
    for key in ("/FontName", "/FontFamily", "/FontStretch", "/Lang"):
        if key in descriptor:
            try:
                desc_data[key.lstrip("/")] = str(descriptor[key]).lstrip("/")
            except (TypeError, ValueError, pikepdf.PdfError):
                # Skip malformed string objects rather than failing export
                pass


def _extract_number_keys(descriptor: Any, desc_data: dict, pikepdf: Any) -> None:
    """Helper to extract numeric font metric properties safely."""
    number_keys = (
        "/FontWeight",
        "/ItalicAngle",
        "/Ascent",
        "/Descent",
        "/Leading",
        "/CapHeight",
        "/XHeight",
        "/StemV",
        "/StemH",
        "/AvgWidth",
        "/MaxWidth",
        "/MissingWidth",
    )
    for key in number_keys:
        if key in descriptor:
            val = descriptor[key]
            try:
                desc_data[key.lstrip("/")] = float(val) if isinstance(val, float) else int(val)
            except (TypeError, ValueError, pikepdf.PdfError):
                # Skip non-numeric objects rather than failing export
                pass


def _extract_bbox(descriptor: Any, desc_data: dict, pikepdf: Any) -> None:
    """Helper to extract the bounding box array safely."""
    if "/FontBBox" in descriptor:
        try:
            desc_data["FontBBox"] = [float(x) for x in descriptor["/FontBBox"]]
        except (TypeError, ValueError, pikepdf.PdfError):
            # Skip invalid or non-numeric arrays rather than failing export
            pass


def _extract_flags(descriptor: Any, desc_data: dict, pikepdf: Any) -> None:
    """Helper to extract the 32-bit FontDescriptor flag integer."""
    if "/Flags" in descriptor:
        try:
            desc_data["flags"] = decode_font_flags(int(descriptor["/Flags"]))
        except (TypeError, ValueError, pikepdf.PdfError):
            # Skip malformed flag bitmasks rather than failing export
            pass


def _extract_style(descriptor: Any, desc_data: dict, pikepdf: Any) -> None:
    """Helper to extract the Panose stylistic string safely."""
    if "/Style" in descriptor:
        style = descriptor["/Style"]
        if hasattr(style, "get") and "/Panose" in style:
            try:
                desc_data["Style"] = {"Panose": bytes(style["/Panose"]).hex().upper()}
            except (TypeError, ValueError, pikepdf.PdfError):
                # Skip unreadable or non-binary Panose strings rather than failing export
                pass


def _extract_descriptor_properties(descriptor: Any) -> dict[str, Any]:
    """
    Extracts all standard metadata and metrics from a pikepdf FontDescriptor
    for inclusion in the sidecar JSON, strictly matching ISO 32000-2 Table 120.
    """
    import pikepdf

    desc_data: dict[str, Any] = {}
    if descriptor is None:
        return desc_data

    _extract_string_keys(descriptor, desc_data, pikepdf)
    _extract_number_keys(descriptor, desc_data, pikepdf)
    _extract_bbox(descriptor, desc_data, pikepdf)
    _extract_flags(descriptor, desc_data, pikepdf)
    _extract_style(descriptor, desc_data, pikepdf)

    return desc_data


def _sniff_is_cff2(font_bytes: bytes) -> bool:
    """
    Sniffs whether an OpenType-wrapped font program contains a CFF2 table.

    PDF's /FontFile3 /Subtype /OpenType covers both classic CFF-flavored
    and newer variable-font CFF2-flavored OpenType programs; the PDF-level
    Subtype name doesn't distinguish between them; there is no /CFF2
    Subtype value in the PDF spec. The only reliable signal is the sfnt
    table directory inside the binary itself. Only the fixed-size header
    and table directory are inspected, not glyph data, so this stays cheap
    even for large embedded fonts.
    """
    if len(font_bytes) < 12:
        return False
    num_tables = int.from_bytes(font_bytes[4:6], "big")
    directory_end = 12 + num_tables * 16
    if len(font_bytes) < directory_end:
        return False
    for i in range(num_tables):
        entry_offset = 12 + i * 16
        tag = font_bytes[entry_offset : entry_offset + 4]
        if tag == b"CFF2":
            return True
    return False


def _get_font_suffix(key: str, stream: Any, font_bytes: bytes, default_suffix: str) -> str:
    """Helper to detect special program formatting subtypes in OpenType container tables."""
    if key == "/FontFile3" and hasattr(stream, "Subtype"):
        sub = str(stream.Subtype).lstrip("/").lower()
        if sub in ("type1c", "cidfonttype0c"):
            return "cff"
        if sub == "opentype" and _sniff_is_cff2(font_bytes):
            return "cff2"
    return default_suffix


def _try_read_embedded_stream(
    descriptor: Any, key: str, default_suffix: str, attr: str, pikepdf
) -> tuple[str, bytes, str] | None:
    """Safe reader for single font descriptors to unpack compressed payloads cleanly."""
    if key not in descriptor:
        return None
    try:
        stream = descriptor[key]
        font_bytes = stream.read_bytes()
        suffix = _get_font_suffix(key, stream, font_bytes, default_suffix)
        return suffix, font_bytes, attr
    except (AttributeError, TypeError, ValueError, pikepdf.PdfError):
        return None


def _get_embedded_font_details(font_obj, descriptor) -> tuple[str, bytes, str] | None:
    """Resolves which font stream to extract and returns (suffix, data, key)."""
    import pikepdf

    if font_obj.get("/Subtype") == "/Type3":
        return None

    if not descriptor:
        return None

    key_map = {
        "/FontFile": ("pfb", "FontFile"),
        "/FontFile2": ("ttf", "FontFile2"),
        "/FontFile3": ("otf", "FontFile3"),
    }

    for key, (suffix, attr) in key_map.items():
        res = _try_read_embedded_stream(descriptor, key, suffix, attr, pikepdf)
        if res is not None:
            return res
    return None


def _export_single_font_binary(
    obj_id_str: str,
    font_obj: Any,
    descriptor: Any,
    base_font_clean: str,
    out_dir: Path,
    font_entry: dict,
) -> Path | None:
    """Extracts and saves the raw font binary file to disk if present."""
    details = _get_embedded_font_details(font_obj, descriptor)
    if not details:
        return None

    ext, font_bytes, descriptor_key = details
    filename = f"font_{obj_id_str}_{base_font_clean}.{ext}"
    filepath = out_dir / filename

    try:
        with open(filepath, "wb") as f:
            f.write(font_bytes)

        font_entry["embedded_file"] = filename
        font_entry["embedded_format"] = ext
        font_entry["descriptor_key"] = descriptor_key
        font_entry["binary_md5"] = file_hash(filepath)
        return filepath
    except OSError as exc:
        logger.warning("Failed to save font binary to %s: %s", filepath, exc)
        return None


def _extract_tounicode_from_obj(
    font_obj: Any, font_entry: dict, obj_id_str: str, pikepdf
) -> tuple[dict[str, str], bytes]:
    """Retrieves and parses ToUnicode CMap configuration table streams if present."""
    from pdftl.fonts.cmap_utils import parse_to_unicode_cmap

    tounicode_map = {}
    cmap_bytes = b""
    if "/ToUnicode" in font_obj:
        stream = font_obj["/ToUnicode"]
        try:
            cmap_bytes = stream.read_bytes()
            tounicode_map = parse_to_unicode_cmap(cmap_bytes)
            font_entry["has_to_unicode"] = True
        except (AttributeError, ValueError, pikepdf.PdfError):
            logger.warning("Failed to read ToUnicode stream for %s", obj_id_str)
    return tounicode_map, cmap_bytes


def _resolve_font_widths_cid_to_gid_map(font_obj: Any) -> dict[int, int] | str | None:
    """
    Resolves the /CIDToGIDMap to use when measuring a Type0 font binary's
    own advance widths for the informational sidecar `width.font` field.

    Reused (rather than re-derived from disk sidecars) directly from the
    live PDF font object via extract_cid_to_gid_map, since that's already
    the authoritative source for the CIDFontType2 descendant's own
    /CIDToGIDMap and requires no extra file I/O here. Returns None for a
    non-Type0 font (the caller then falls back to the plain Unicode-cmap
    reading path, correct for Simple fonts).
    """
    if str(font_obj.get("/Subtype", "")) != "/Type0":
        return None
    return extract_cid_to_gid_map(font_obj)


def _assemble_unified_mappings(
    pdf_widths: dict[str, float],
    tounicode_map: dict[str, str],
    font_file_path: Path | None,
    font_obj: Any | None = None,
) -> dict[str, dict]:
    """
    Combines metrics, mappings, and font binaries into a unified database
    list.

    For a Type0 (CID-keyed) font, the informational `width.font` value is
    measured via the font's own /CIDToGIDMap-derived GID rather than its
    Unicode cmap: a CID-keyed font's own Unicode cmap is frequently absent,
    partial, or numerically unrelated to its CIDs, so reading widths
    through it (as if `pdf_widths` keys were Unicode code points) produced
    a `width.font` that was silently empty or wrong for CJK fonts even
    though the CID-aware import-side patch/squash/auto sync already
    resolves this correctly. Pass `font_obj` for this to take effect;
    omitting it (or passing a Simple font) preserves the original
    cmap-based reading.
    """
    unique_keys = set(pdf_widths.keys())

    tounicode_hex = {}
    for hex_key, u in tounicode_map.items():
        clean_hex = hex_key.upper()
        tounicode_hex[clean_hex] = u
        unique_keys.add(clean_hex)

    font_widths_hex = {}
    if font_file_path and font_file_path.is_file():
        cid_to_gid_map = (
            _resolve_font_widths_cid_to_gid_map(font_obj) if font_obj is not None else None
        )
        font_widths_hex = get_font_widths_from_file(font_file_path, cid_to_gid_map=cid_to_gid_map)

    mappings = {}
    for hex_key in sorted(unique_keys):
        entry = {}
        if hex_key in tounicode_hex:
            entry["unicode"] = tounicode_hex[hex_key]

        width_entry = {}
        if hex_key in pdf_widths:
            width_entry["pdf"] = pdf_widths[hex_key]
        if hex_key in font_widths_hex:
            width_entry["font"] = font_widths_hex[hex_key]

        if width_entry:
            entry["width"] = width_entry

        mappings[hex_key] = entry
    return mappings


def _save_json_sidecar(
    obj_id_str: str,
    base_font_clean: str,
    out_dir: Path,
    mappings: dict[str, dict],
    descriptor_properties: dict[str, Any],
    font_entry: dict,
) -> None:
    """Helper block to store sidecar structures back out into JSON files."""
    json_filename = f"font_{obj_id_str}_{base_font_clean}.json"
    json_filepath = out_dir / json_filename
    try:
        sidecar_data = {
            "width_sync_mode": "auto",
            "descriptor": descriptor_properties,
            "mappings": mappings,
        }
        with open(json_filepath, "w", encoding="utf-8") as f:
            json.dump(sidecar_data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        font_entry["sidecar_json_file"] = json_filename
        font_entry["sidecar_json_md5"] = file_hash(json_filepath)
    except OSError as exc:
        logger.warning("Failed to save unified JSON to %s: %s", json_filepath, exc)


def _save_ps_sidecar(
    obj_id_str: str,
    base_font_clean: str,
    out_dir: Path,
    cmap_bytes: bytes,
    font_entry: dict,
) -> None:
    """Helper block to save out pristine raw PostScript map representations."""
    ps_filename = f"font_{obj_id_str}_{base_font_clean}.ps"
    ps_filepath = out_dir / ps_filename
    try:
        with open(ps_filepath, "wb") as f:
            f.write(cmap_bytes)
        font_entry["tounicode_ps_file"] = ps_filename
        font_entry["tounicode_ps_md5"] = file_hash(ps_filepath)
    except OSError as exc:
        logger.warning("Failed to save ToUnicode PS to %s: %s", ps_filepath, exc)


def _save_cid_to_gid_sidecar(
    obj_id_str: str,
    base_font_clean: str,
    out_dir: Path,
    mapping: dict[int, int],
    font_entry: dict,
) -> None:
    """
    Writes an explicit CID->GID table out to its own sidecar JSON file.

    Kept separate from the unified width/unicode mappings sidecar because
    CIDToGIDMap tables can be very large (tens of thousands of entries for
    CJK fonts) and are keyed and compressed along an entirely different
    axis (GID identity) than widths (width equality) or ToUnicode (Unicode
    value equality); folding it into the same structure would force one of
    the three compression schemes to compromise for the others.
    """
    json_filename = f"font_{obj_id_str}_{base_font_clean}.cid2gid.json"
    json_filepath = out_dir / json_filename
    try:
        hex_mapping = {f"{cid:04X}": f"{gid:04X}" for cid, gid in mapping.items()}
        with open(json_filepath, "w", encoding="utf-8") as f:
            json.dump({"cid_to_gid": hex_mapping}, f, indent=2)
            f.write("\n")
        font_entry["cid_to_gid_map"] = "explicit"
        font_entry["cid_to_gid_map_file"] = json_filename
        font_entry["cid_to_gid_map_md5"] = file_hash(json_filepath)
    except OSError as exc:
        logger.warning("Failed to save CIDToGIDMap sidecar to %s: %s", json_filepath, exc)


def _export_unified_sidecar(
    obj_id_str: str,
    font_obj: Any,
    base_font_clean: str,
    out_dir: Path,
    tounicode_mode: str,
    pdf_widths: dict[str, float],
    font_file_path: Path | None,
    font_entry: dict,
) -> None:
    """Extracts, unifies and formats the single unified sidecar file."""
    import pikepdf

    tounicode_map, cmap_bytes = _extract_tounicode_from_obj(
        font_obj, font_entry, obj_id_str, pikepdf
    )
    mappings = _assemble_unified_mappings(pdf_widths, tounicode_map, font_file_path, font_obj)

    descriptor = find_font_descriptor(font_obj)
    descriptor_properties = _extract_descriptor_properties(descriptor)

    # 2. Save Unified JSON Sidecar
    if tounicode_mode in ("json", "all"):
        _save_json_sidecar(
            obj_id_str, base_font_clean, out_dir, mappings, descriptor_properties, font_entry
        )

    # 3. Save Raw PostScript CMap Sidecar if requested
    if tounicode_mode in ("ps", "all") and "/ToUnicode" in font_obj:
        _save_ps_sidecar(obj_id_str, base_font_clean, out_dir, cmap_bytes, font_entry)


def _extract_differences_list(font_obj) -> list | None:
    """Helper to extract /Differences array from Simple font encoding dictionary."""
    if "/Encoding" not in font_obj:
        return None
    enc = font_obj["/Encoding"]

    try:
        has_differences = "/Differences" in enc
    except (TypeError, ValueError):
        return None

    if not has_differences:
        return None

    diff_array = enc["/Differences"]
    serialized = []
    for item in diff_array:
        try:
            # Attempt to parse as an integer sequence code
            serialized.append(int(item))
        except (TypeError, ValueError):
            # Fall back to stringifying object representations (e.g. pikepdf.Name)
            serialized.append(str(item))
    return serialized


def _extract_base_encoding(font_obj) -> str | None:
    """
    Helper to extract the effective /BaseEncoding name from a Simple font's
    /Encoding entry, if any. /Encoding may itself be a bare Name (e.g.
    /WinAnsiEncoding), in which case that name IS the base encoding, or a
    Dictionary carrying an explicit /BaseEncoding key. Returns None if
    neither form declares one, meaning the font's own built-in encoding
    applies instead (left untouched here; see G-3 in the roadmap).
    """
    if "/Encoding" not in font_obj:
        return None
    enc = font_obj["/Encoding"]

    try:
        is_dict = "/BaseEncoding" in enc
    except (TypeError, ValueError):
        # /Encoding is a bare Name, not a Dictionary: the name itself is the
        # base encoding (e.g. /WinAnsiEncoding), if it's one we recognize.
        name = str(enc).lstrip("/")
        return (
            name if name in ("WinAnsiEncoding", "MacRomanEncoding", "StandardEncoding") else None
        )

    if not is_dict:
        return None

    name = str(enc["/BaseEncoding"]).lstrip("/")
    return name if name in ("WinAnsiEncoding", "MacRomanEncoding", "StandardEncoding") else None


def build_manifest(
    pdf: pikepdf.Pdf, target_pages: list[int], out_dir: Path, tounicode_mode: str
) -> dict:
    manifest: dict = {"fonts": {}}

    for local_alias, font_obj, page_num in get_all_fonts_recursive(pdf, target_pages):
        obj_id = int(font_obj.objgen[0])
        gen_id = int(font_obj.objgen[1])
        obj_id_str = f"{obj_id}_{gen_id}"

        raw_name = str(font_obj.get("/BaseFont", "UnnamedFont")).lstrip("/")
        base_font_clean = sanitize_name(raw_name)

        if obj_id_str not in manifest["fonts"]:
            descriptor = find_font_descriptor(font_obj)
            pdf_widths = extract_font_widths(font_obj)
            font_entry = _make_font_entry(
                font_obj,
                obj_id,
                gen_id,
                obj_id_str,
                raw_name,
                base_font_clean,
                out_dir,
            )

            if font_entry["subtype"] == "Type3":
                export_type3_font(font_obj, obj_id, gen_id, base_font_clean, out_dir, font_entry)
                font_file_path = None
                # Type 3 glyph programs live inline in /CharProcs, not in a
                # FontFile* stream, so they are always "embedded" by
                # definition -- there is no unembedded/Core 14 concept here.
                font_entry["is_embedded"] = True
            else:
                # Extract raw binary if embedded
                font_file_path = _export_single_font_binary(
                    obj_id_str, font_obj, descriptor, base_font_clean, out_dir, font_entry
                )
                font_entry["is_embedded"] = font_file_path is not None
                if not font_entry["is_embedded"]:
                    # No embedded FontFile/FontFile2/FontFile3 stream was
                    # found (e.g. a standard, unembedded Core 14 font like
                    # Helvetica). Write explicit nulls rather than simply
                    # omitting the keys, so consumers of the manifest can
                    # tell "checked, not embedded" apart from "field absent".
                    font_entry["embedded_file"] = None
                    font_entry["embedded_format"] = None

            # Export unified JSON metrics and mapping sidecar
            _export_unified_sidecar(
                obj_id_str,
                font_obj,
                base_font_clean,
                out_dir,
                tounicode_mode,
                pdf_widths,
                font_file_path,
                font_entry,
            )

            manifest["fonts"][obj_id_str] = font_entry

        # Track usage details
        usages = manifest["fonts"][obj_id_str]["usages"]
        usage_entry = {"page": page_num, "local_alias": local_alias}
        if usage_entry not in usages:
            usages.append(usage_entry)

    return manifest


def _make_font_entry(font_obj, obj_id, gen_id, obj_id_str, raw_name, base_font_clean, out_dir):
    font_entry = {
        "obj_id": obj_id,
        "gen_id": gen_id,
        "base_font": raw_name,
        "subtype": str(font_obj.get("/Subtype", "Unknown")).lstrip("/"),
        "has_to_unicode": False,
        "is_embedded": False,
        "embedded_file": None,
        "embedded_format": None,
        "usages": [],
    }

    diffs = _extract_differences_list(font_obj)
    if diffs:
        font_entry["differences"] = diffs

    base_encoding = _extract_base_encoding(font_obj)
    if base_encoding:
        font_entry["base_encoding"] = base_encoding

    predefined_cmap = detect_predefined_identity_encoding(font_obj)
    if predefined_cmap:
        # Recorded regardless of whether /ToUnicode or /W are present, so
        # that even a font with neither still carries a meaningful,
        # non-empty manifest entry explaining why its glyph mappings may
        # be sparse: CID equals code, and no CMap resolution beyond that
        # is possible from the PDF alone.
        font_entry["encoding_cmap"] = predefined_cmap

    cid_to_gid = extract_cid_to_gid_map(font_obj)
    if cid_to_gid == "Identity":
        font_entry["cid_to_gid_map"] = "Identity"
    elif isinstance(cid_to_gid, dict):
        _save_cid_to_gid_sidecar(obj_id_str, base_font_clean, out_dir, cid_to_gid, font_entry)
    # cid_to_gid is None for non-Type0 fonts; the key is simply
    # omitted, matching how "differences"/"base_encoding" are only
    # written when applicable.

    return font_entry
