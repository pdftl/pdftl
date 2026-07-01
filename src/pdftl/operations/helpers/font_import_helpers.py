# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/helpers/font_import_helpers.py

"""
Helpers implementing the `import_fonts` operation: writing edited binary font
streams, Type 3 character procedures, /ToUnicode maps, /Differences,
/Encoding (identity CMap only), /CIDToGIDMap, and /Widths back into the PDF.

## Module split

Width synchronization (deciding sync mode, resolving CID->GID mappings for
Type0 binary edits, and executing the in-memory patch/squash with its
fallback-to-manual-/Widths guarantee) now lives in
pdftl.operations.helpers.font_widths_sync -- see that module's own
docstring, including why the font_binary_utils/widths_utils/file_hash
imports below are kept even though nothing in *this* file calls most of
them directly anymore: font_widths_sync.py looks them up as attributes on
this module at call time, specifically so the existing test suite's
`monkeypatch.setattr(fih, "patch_font_file_metrics", ...)`-style patches
keep working unchanged.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from pdftl.exceptions import UserCommandLineError
from pdftl.fonts.cmap_utils import _PREDEFINED_IDENTITY_CMAPS
from pdftl.fonts.font_binary_utils import (  # noqa: F401
    classify_binary_format,
    get_font_widths_from_file,
    patch_font_file_metrics,
    rekey_name_widths_to_hex_codes,
    squash_font_file_vectors,
)
from pdftl.fonts.widths_utils import update_font_widths  # noqa: F401
from pdftl.operations.helpers.font_ops_shared import file_hash, encode_font_flags

# Re-exported for backward compatibility: existing call sites (and the
# existing test suite's monkeypatches -- see module docstring above)
# reference these as pdftl.operations.helpers.font_import_helpers.<name>.
# See font_widths_sync.py's own docstring for where each now actually lives.
from pdftl.operations.helpers.font_widths_sync import (  # noqa: F401
    _apply_in_memory_patch,
    _apply_in_memory_squash,
    _auto_sync_widths_from_font,
    _execute_widths_sync,
    _extract_manual_widths,
    _read_sync_mode,
    _rekey_simple_font_widths_if_needed,
    _resolve_cid_to_gid_for_sync,
    import_widths,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Type 3 Font Import Logic
# ============================================================================


def _read_descriptor_sidecar(font_entry: dict, out_dir: Path) -> dict | None:
    """Loads and returns the descriptor payload from the sidecar JSON."""
    json_file = font_entry.get("sidecar_json_file")
    if not json_file:
        return None

    json_path = out_dir / json_file
    if not json_path.is_file():
        return None

    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("descriptor")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to read descriptor sidecar JSON from %s: %s", json_path, exc)
        return None


def _apply_descriptor_edit(descriptor: Any, pdf_key: str, new_val: Any) -> bool:
    """Helper to apply a value or delete a key if None, tracking changes."""
    if new_val is None:
        if pdf_key in descriptor:
            del descriptor[pdf_key]
            return True
        return False

    if pdf_key not in descriptor or descriptor[pdf_key] != new_val:
        descriptor[pdf_key] = new_val
        return True
    return False


def _import_name_keys(descriptor: Any, desc_data: dict, pikepdf: Any) -> bool:
    """Helper to update string/Name keys from the sidecar."""
    updated = False
    for key in ("FontName", "FontStretch", "Lang"):
        if key in desc_data:
            val = desc_data[key]
            new_val = pikepdf.Name("/" + str(val)) if val is not None else None
            if _apply_descriptor_edit(descriptor, "/" + key, new_val):
                updated = True
    return updated


def _import_string_keys(descriptor: Any, desc_data: dict, pikepdf: Any) -> bool:
    """Helper to update PDF String keys from the sidecar."""
    updated = False
    for key in ("FontFamily",):
        if key in desc_data:
            val = desc_data[key]
            new_val = pikepdf.String(str(val)) if val is not None else None
            if _apply_descriptor_edit(descriptor, "/" + key, new_val):
                updated = True
    return updated


def _import_number_keys(descriptor: Any, desc_data: dict) -> bool:
    """Helper to update numeric keys from the sidecar."""
    updated = False
    number_keys = (
        "FontWeight",
        "ItalicAngle",
        "Ascent",
        "Descent",
        "Leading",
        "CapHeight",
        "XHeight",
        "StemV",
        "StemH",
        "AvgWidth",
        "MaxWidth",
        "MissingWidth",
    )
    for key in number_keys:
        if key in desc_data:
            val = desc_data[key]
            if val is None:
                new_val = None
            else:
                new_val = float(val) if isinstance(val, float) else int(val)
            if _apply_descriptor_edit(descriptor, "/" + key, new_val):
                updated = True
    return updated


def _import_bbox(descriptor: Any, desc_data: dict, pikepdf: Any) -> bool:
    """Helper to update the FontBBox Array from the sidecar."""
    if "FontBBox" not in desc_data:
        return False

    bbox = desc_data["FontBBox"]
    if bbox is None:
        return _apply_descriptor_edit(descriptor, "/FontBBox", None)

    if isinstance(bbox, list) and len(bbox) == 4:
        try:
            new_bbox = pikepdf.Array([float(x) for x in bbox])
            return _apply_descriptor_edit(descriptor, "/FontBBox", new_bbox)
        except (TypeError, ValueError):
            pass

    return False


def _import_flags(descriptor: Any, desc_data: dict) -> bool:
    """Helper to update the 32-bit Flags integer from the sidecar."""
    if "flags" not in desc_data or not isinstance(desc_data["flags"], dict):
        return False

    try:
        new_flags = encode_font_flags(desc_data["flags"])
        # Explicitly cast the existing descriptor value to int to preserve
        # the fallback behavior expected by the malformed edits test.
        if "/Flags" not in descriptor or int(descriptor["/Flags"]) != new_flags:
            descriptor["/Flags"] = new_flags
            return True
    except (TypeError, ValueError):
        pass

    return False


def _import_style(descriptor: Any, desc_data: dict, pikepdf: Any) -> bool:
    """Helper to update the Style/Panose Dictionary from the sidecar."""
    if "Style" not in desc_data:
        return False

    style_data = desc_data["Style"]
    if style_data is None:
        return _apply_descriptor_edit(descriptor, "/Style", None)

    if isinstance(style_data, dict):
        panose_hex = style_data.get("Panose")
        if panose_hex is None:
            return _apply_descriptor_edit(descriptor, "/Style", None)

        try:
            panose_bytes = bytes.fromhex(panose_hex)
            new_style = pikepdf.Dictionary({"/Panose": pikepdf.String(panose_bytes)})
            return _apply_descriptor_edit(descriptor, "/Style", new_style)
        except (TypeError, ValueError):
            pass

    return False


def import_descriptor(font_obj: Any, font_entry: dict, out_dir: Path, pikepdf) -> bool:
    """
    Reads the edited 'descriptor' properties from the sidecar JSON (if present)
    and updates the corresponding keys in the PDF's /FontDescriptor.

    Returns True if the FontDescriptor was successfully updated.
    """
    from pdftl.fonts.font_extraction_utils import find_font_descriptor

    desc_data = _read_descriptor_sidecar(font_entry, out_dir)
    if not desc_data:
        return False

    descriptor = find_font_descriptor(font_obj)
    if not descriptor:
        logger.warning(
            "No /FontDescriptor found for %s to inject properties.",
            font_entry.get("base_font", ""),
        )
        return False

    # Apply each block sequentially; any returning True marks the descriptor as updated
    updates = [
        _import_name_keys(descriptor, desc_data, pikepdf),
        _import_string_keys(descriptor, desc_data, pikepdf),
        _import_number_keys(descriptor, desc_data),
        _import_bbox(descriptor, desc_data, pikepdf),
        _import_flags(descriptor, desc_data),
        _import_style(descriptor, desc_data, pikepdf),
    ]

    if any(updates):
        logger.info(
            "Successfully updated /FontDescriptor properties from sidecar JSON for %s",
            font_entry.get("base_font", ""),
        )
        return True

    return False


def _reconstruct_inline_images_on_import(
    glyph_body: str, src_dir: Path, img_registry: dict
) -> bytes:
    """
    Parses custom structural tags inside the stream, reads edited TIFF assets,
    re-encodes them into binary blocks, and returns the compiled stream bytes.
    """
    from PIL import Image

    lines = glyph_body.splitlines()
    output_bytes = bytearray()

    in_image = False
    meta_json = ""
    ref_path = ""

    for line in lines:
        if line.startswith("%BEGIN_INLINE_IMAGE%"):
            in_image = True
            continue
        if line.startswith("%END_INLINE_IMAGE%"):
            in_image = False
            meta = json.loads(meta_json)
            tiff_full_path = src_dir / ref_path

            dict_elements = []
            for k, v in meta.items():
                short_k = {
                    "Width": "W",
                    "Height": "H",
                    "ColorSpace": "CS",
                    "BitsPerComponent": "BPC",
                    "Filter": "F",
                    "Decode": "D",
                }.get(k, k)
                dict_elements.append(f"/{short_k} {v}")

            dict_header = "BI " + " ".join(dict_elements) + " ID\n"
            output_bytes.extend(dict_header.encode("utf-8"))

            img = Image.open(tiff_full_path)
            raw_pixel_bytes = img.tobytes()
            output_bytes.extend(raw_pixel_bytes)
            output_bytes.extend(b"\nEI")
            continue

        if in_image:
            if line.startswith("%META:"):
                meta_json = line.replace("%META:", "").strip()
            elif line.startswith("%REF:"):
                ref_path = line.replace("%REF:", "").strip()
            continue

        output_bytes.extend((line + "\n").encode("latin-1"))

    return bytes(output_bytes)


def import_type3_font(font_obj: Any, font_entry: dict, src_dir: Path) -> None:
    """
    Parses an ad-hoc .charprocs file and accurately reconstructs Type 3 glyph streams
    and inline image structures back into the target PDF document.
    """
    charprocs_filename = font_entry.get("charprocs_file")
    if not charprocs_filename:
        return

    charprocs_path = src_dir / charprocs_filename
    if not charprocs_path.exists():
        return

    with open(charprocs_path, encoding="utf-8") as f:
        content = f.read()

    blocks = re.split(r"={72}\n=== Font .*? / CharProcs /(.*?)\n={72}\n", content)

    charprocs_dict = font_obj.get("/CharProcs")
    if not charprocs_dict:
        return

    import pikepdf

    for i in range(1, len(blocks), 2):
        glyph_name = blocks[i].strip()
        glyph_body = blocks[i + 1].strip()

        rebuilt_bytes = _reconstruct_inline_images_on_import(
            glyph_body, src_dir, font_entry.get("inline_images", {})
        )

        if f"/{glyph_name}" in charprocs_dict:
            stream_target = charprocs_dict[f"/{glyph_name}"]
            if isinstance(stream_target, pikepdf.Stream):
                stream_target.write(rebuilt_bytes)


# ============================================================================
# Import Fonts Logic
# ============================================================================


def _find_type1_segment_lengths(font_bytes: bytes) -> tuple[int, int, int] | None:
    """
    Computes the (Length1, Length2, Length3) segment boundaries of a raw
    Type 1 font program.

    A /FontFile stream is a single concatenated blob of three segments: a
    cleartext PostScript header ending at the "eexec" operator, an encrypted
    (binary or ASCII-hex) charstring segment, and a trailing zero-padded
    "cleartomark" block (Adobe Type 1 Font Format, Chapter 7; referenced by
    ISO 32000-2 Table 121 as /Length1, /Length2, /Length3). These markers are
    the standard, well-defined way to locate the boundaries; this is not a
    heuristic guess about font-specific structure.

    Returns None if either boundary marker can located (e.g. a
    non-standard or already-malformed program), so callers can fall back to
    leaving the existing Length* entries untouched rather than writing
    lengths computed from an unreliable split point.
    """
    eexec_marker = b"eexec"
    eexec_idx = font_bytes.find(eexec_marker)
    if eexec_idx == -1:
        return None

    # Length1 covers the cleartext portion up to and including the "eexec"
    # operator plus the single end-of-line sequence that conventionally
    # follows it.
    after_eexec = eexec_idx + len(eexec_marker)
    if font_bytes[after_eexec : after_eexec + 2] == b"\r\n":
        eol_len = 2
    elif font_bytes[after_eexec : after_eexec + 1] in (b"\r", b"\n"):
        eol_len = 1
    else:
        eol_len = 0
    length1 = after_eexec + eol_len

    cleartomark_marker = b"cleartomark"
    cleartomark_idx = font_bytes.rfind(cleartomark_marker)
    if cleartomark_idx == -1 or cleartomark_idx < length1:
        return None

    # Length3 covers the trailing zero-padding lines and the "cleartomark"
    # operator itself, walked back to (but not including) the last byte of
    # the encrypted segment.
    idx = cleartomark_idx
    while idx > length1 and font_bytes[idx - 1] in b"0\r\n \t":
        idx -= 1
    trailer_start = idx

    length3 = len(font_bytes) - trailer_start
    length2 = trailer_start - length1

    # No further bounds check is needed here: the guard above already
    # ensures cleartomark_idx >= length1, and the trim loop above can only
    # decrease idx down to length1, so trailer_start is always in
    # [length1, cleartomark_idx]. That makes length2 (trailer_start -
    # length1) always >= 0, and length3 (len(font_bytes) - trailer_start)
    # always > 0, since trailer_start can be at most cleartomark_idx, which
    # by definition still has the "cleartomark" marker bytes ahead of it.

    return length1, length2, length3


def _update_type1_length_fields(stream: Any, font_bytes: bytes) -> None:
    """
    Recomputes and writes /Length1, /Length2, /Length3 on a Type 1 /FontFile
    stream after its bytes have been overwritten from disk.

    Unlike /FontFile2's single /Length1 (a plain byte count), a stale value
    here after an edit changes the program's overall size doesn't just
    under/over-report length -- it tells a PDF reader to split the
    reconstituted PostScript program at the wrong byte offsets entirely,
    corrupting the font. If the standard eexec/cleartomark boundary markers
    can't be located, the existing Length* entries are left untouched rather
    than writing guessed values.
    """
    lengths = _find_type1_segment_lengths(font_bytes)
    if lengths is None:
        # Explanatory comment: a non-standard or already-malformed Type 1
        # program (or a test fixture with no real Type 1 structure) has no
        # reliable segment boundaries to recompute from. Leaving the old
        # Length* values in place is safer than writing a guess.
        logger.warning(
            "Could not locate Type 1 segment boundaries (eexec/cleartomark) "
            "in edited font program; leaving existing /Length1, /Length2, "
            "/Length3 unchanged."
        )
        return

    length1, length2, length3 = lengths
    stream.Length1 = length1
    stream.Length2 = length2
    stream.Length3 = length3


def _inject_font_bytes(font_obj: Any, font_entry: dict, font_bytes: bytes) -> bool:
    """Injects raw font bytes into the PDF descriptor."""
    from pdftl.fonts.font_extraction_utils import find_font_descriptor

    descriptor_key = font_entry.get("descriptor_key")
    if not descriptor_key:
        return False

    descriptor = find_font_descriptor(font_obj)
    if not descriptor:
        return False

    stream = descriptor[f"/{descriptor_key}"]
    stream.write(font_bytes)

    if descriptor_key == "FontFile2":
        stream.Length1 = len(font_bytes)
    elif descriptor_key == "FontFile":
        _update_type1_length_fields(stream, font_bytes)

    return True


def import_single_font_binary(font_obj: Any, font_entry: dict, out_dir: Path) -> bool:
    """Overwrites the raw binary font stream if the file exists and its MD5 differs."""
    filename = font_entry.get("embedded_file")
    if not filename:
        return False

    filepath = out_dir / filename
    if not filepath.is_file():
        logger.warning("Embedded font file %s not found in directory.", filename)
        return False

    current_hash = file_hash(filepath)
    if current_hash == font_entry.get("binary_md5"):
        return False

    try:
        with open(filepath, "rb") as f:
            font_bytes = f.read()

        if _inject_font_bytes(font_obj, font_entry, font_bytes):
            logger.info(
                "Updated embedded font binary stream from disk edit for %s",
                font_entry["base_font"],
            )
            return True
        return False
    except OSError as exc:
        logger.warning("Failed to import font binary from %s: %s", filepath, exc)
        return False


def _resolve_sidecar_clash_and_files(
    font_entry: dict, out_dir: Path
) -> tuple[Path | None, Path | None]:
    """Checks for file presence, raising an error if both .json and .ps exist."""
    json_file = font_entry.get("sidecar_json_file")
    ps_file = font_entry.get("tounicode_ps_file")

    json_path = out_dir / json_file if json_file else None
    ps_path = out_dir / ps_file if ps_file else None

    json_exists = json_path is not None and json_path.is_file()
    ps_exists = ps_path is not None and ps_path.is_file()

    if json_exists and ps_exists:
        raise UserCommandLineError(
            f"Ambiguous ToUnicode source: Both '{json_file}' and '{ps_file}' "
            "exist in the directory. Please delete or rename one of them "
            "to indicate which format you prefer to import."
        )

    resolved_json = json_path if json_exists else None
    resolved_ps = ps_path if ps_exists else None
    return resolved_json, resolved_ps


def _import_tounicode_json(
    font_obj: Any, json_path: Path, font_entry: dict, pikepdf, pdf=None
) -> dict[str, dict]:
    """Compiles /ToUnicode map from sidecar JSON mappings."""
    from pdftl.fonts.cmap_utils import compile_to_unicode_cmap

    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)

        mappings = data.get("mappings", {})
        cmap_mappings = {}
        for hex_key, entry in mappings.items():
            if "unicode" in entry:
                cmap_mappings[hex_key] = entry["unicode"]

    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to compile ToUnicode JSON from %s: %s", json_path, exc)
        return {}

    if cmap_mappings:
        base_font = font_entry.get("base_font", "Custom")
        compiled_bytes = compile_to_unicode_cmap(cmap_mappings, name=base_font)
        if "/ToUnicode" in font_obj:
            font_obj["/ToUnicode"].write(compiled_bytes)
        else:
            if pdf is None:
                raise ValueError(
                    "A valid pikepdf.Pdf context must be provided "
                    "to create a new ToUnicode stream."
                )
            font_obj["/ToUnicode"] = pdf.make_stream(compiled_bytes)
        logger.info("Updated /ToUnicode CMap from edited JSON %s", json_path.name)

    return mappings


def _import_tounicode_ps(
    font_obj: Any, ps_path: Path, font_entry: dict, pikepdf, pdf=None
) -> None:
    """Overwrites /ToUnicode CMap stream verbatim from PostScript file."""
    try:
        with open(ps_path, "rb") as f:
            compiled_bytes = f.read()
    except OSError as exc:
        logger.warning("Failed to import ToUnicode PS from %s: %s", ps_path, exc)
        return

    if "/ToUnicode" in font_obj:
        font_obj["/ToUnicode"].write(compiled_bytes)
    else:
        if pdf is None:
            raise ValueError(
                "A valid pikepdf.Pdf context must be provided to create a new ToUnicode stream."
            )
        font_obj["/ToUnicode"] = pdf.make_stream(compiled_bytes)
    logger.info("Updated /ToUnicode CMap verbatim from edited PS CMap %s", ps_path.name)


def import_tounicode_and_get_mappings(
    font_obj: Any, font_entry: dict, out_dir: Path, pikepdf, pdf=None
) -> dict[str, dict]:
    """Resolves and executes /ToUnicode updates, returning sidecar mappings."""
    json_path, ps_path = _resolve_sidecar_clash_and_files(font_entry, out_dir)

    if json_path:
        return _import_tounicode_json(font_obj, json_path, font_entry, pikepdf, pdf)
    elif ps_path:
        _import_tounicode_ps(font_obj, ps_path, font_entry, pikepdf, pdf)

    return {}


def import_differences(font_obj: Any, font_entry: dict, pikepdf) -> None:
    """Overwrites /Differences encoding array in Simple Font."""
    if "differences" not in font_entry:
        return

    raw_diffs = font_entry["differences"]
    if not raw_diffs:
        return

    pdf_diffs = []
    for item in raw_diffs:
        if isinstance(item, str) and item.startswith("/"):
            pdf_diffs.append(pikepdf.Name(item))
        else:
            try:
                pdf_diffs.append(int(item))
            except ValueError:
                pdf_diffs.append(pikepdf.Name("/" + str(item)))

    if "/Encoding" not in font_obj:
        font_obj["/Encoding"] = pikepdf.Dictionary()

    font_obj["/Encoding"]["/Differences"] = pikepdf.Array(pdf_diffs)


def import_encoding_cmap(font_obj: Any, font_entry: dict, pikepdf) -> bool:
    """
    Restores/updates a Type0 font's /Encoding when the manifest carries an
    `encoding_cmap` value, mirroring how import_cid_to_gid_map restores
    /CIDToGIDMap and import_differences restores /Differences.

    `encoding_cmap` (see detect_predefined_identity_encoding, export side)
    is only ever written when the font's *original* /Encoding named one of
    the two predefined identity CMaps, /Identity-H or /Identity-V -- CID
    equals code under either, and switching between the two only changes
    writing mode (horizontal vs vertical), never the code-to-CID mapping
    itself. That makes an Identity-H <-> Identity-V edit the one safe,
    well-defined `encoding_cmap` edit this function actually applies.

    Any other value -- a user editing the field to name some other CMap
    entirely (e.g. a CJK ordering like UniGB-UCS2-H, or free text) -- is
    explicitly rejected rather than silently applied or silently ignored:
    there is no CID-space information available here to correctly
    re-encode into an arbitrary CMap, and writing one anyway would
    silently corrupt every content stream that assumed the font's original
    CID mapping. A rejected edit leaves /Encoding exactly as it already is
    (its natural round-trip default, since /Encoding is never touched
    otherwise), with a warning so the user knows the edit didn't take
    effect.

    Returns True if /Encoding was written, False otherwise (no
    `encoding_cmap` key present, or an edit was rejected).
    """
    mode = font_entry.get("encoding_cmap")
    if mode is None:
        return False

    if mode not in _PREDEFINED_IDENTITY_CMAPS:
        logger.warning(
            "Ignoring 'encoding_cmap' value '%s' for %s: only an edit between "
            "Identity-H and Identity-V is supported, since there is no "
            "CID-space information available to re-encode into any other "
            "CMap. /Encoding left untouched.",
            mode,
            font_entry.get("base_font", ""),
        )
        return False

    font_obj["/Encoding"] = pikepdf.Name(f"/{mode}")
    return True


def import_cid_to_gid_map(
    font_obj: Any, font_entry: dict, out_dir: Path, pikepdf, pdf: Any = None
) -> bool:
    """
    Restores /CIDToGIDMap on a Type0 font's descendant CIDFont from the
    manifest, mirroring how import_tounicode_and_get_mappings and
    import_differences restore their respective encoding-layer tables.
    Returns True if the CIDFont's /CIDToGIDMap was actually written.
    """
    from pdftl.fonts.widths_utils import update_cid_to_gid_map

    mode = font_entry.get("cid_to_gid_map")
    if mode is None:
        return False

    if mode == "Identity":
        update_cid_to_gid_map(font_obj, "Identity", pikepdf)
        return True

    filename = font_entry.get("cid_to_gid_map_file")
    if not filename:
        return False

    filepath = out_dir / filename
    if not filepath.is_file():
        logger.warning("CIDToGIDMap sidecar file %s not found in directory.", filename)
        return False

    try:
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
        hex_mapping = data.get("cid_to_gid", {})
        mapping = {int(cid_hex, 16): int(gid_hex, 16) for cid_hex, gid_hex in hex_mapping.items()}
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to read CIDToGIDMap sidecar %s: %s", filepath, exc)
        return False

    update_cid_to_gid_map(font_obj, mapping, pikepdf, pdf)
    return True
