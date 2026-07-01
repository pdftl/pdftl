# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/helpers/type3_extraction_helpers.py

"""
Helpers implementing Type 3 font extraction: pulling glyph character
procedures out of a PDF's /CharProcs dictionary into an ad-hoc text block
format, including lossless extraction of embedded inline bitmaps into
editable TIFF files.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================================
# Type 3 Font Extraction Logic
# ============================================================================


def _parse_inline_dict(dict_bytes: bytes) -> dict:
    """Parses inline image dictionary keys and maps them to human-readable names."""
    import re

    meta = {}
    text = dict_bytes.decode("utf-8", errors="replace")
    tokens = re.findall(r"/[A-Za-z0-9]+|\[[^\]]*\]|[^\s\[\]]+", text)

    idx = 0
    while idx < len(tokens) - 1:
        key_token = tokens[idx]
        if key_token.startswith("/"):
            val_token = tokens[idx + 1]
            key = key_token.lstrip("/")
            val = val_token
            norm_map = {
                "W": "Width",
                "H": "Height",
                "CS": "ColorSpace",
                "BPC": "BitsPerComponent",
                "F": "Filter",
                "D": "Decode",
            }
            meta[norm_map.get(key, key)] = val
            idx += 2
        else:
            idx += 1
    return meta


def _normalize_inline_meta(meta: dict) -> dict:
    """Translates PDF abbreviation properties back to standardized object names."""
    MAP_KEYS = {
        "BPC": "BitsPerComponent",
        "CS": "ColorSpace",
        "D": "Decode",
        "DP": "DecodeParms",
        "F": "Filter",
        "H": "Height",
        "IM": "ImageMask",
        "Intent": "Intent",
        "W": "Width",
    }

    MAP_VALS = {
        "/AHx": "/ASCIIHexDecode",
        "/A85": "/ASCII85Decode",
        "/LZW": "/LZWDecode",
        "/Fl": "/FlateDecode",
        "/RL": "/RunLengthDecode",
        "/CCF": "/CCITTFaxDecode",
        "/DCT": "/DCTDecode",
        "/G": "/DeviceGray",
        "/RGB": "/DeviceRGB",
        "/CMYK": "/DeviceCMYK",
        "/I": "/Indexed",
    }

    normalized_meta = {}
    for k, v in meta.items():
        norm_key = MAP_KEYS.get(k, k)
        norm_val = MAP_VALS.get(v, v) if isinstance(v, str) and v.startswith("/") else v
        normalized_meta[norm_key] = norm_val
    return normalized_meta


def _clean_filter_meta(meta: dict) -> dict:
    """Removes compression properties from metadata so images save as standard raw TIFFs."""
    clean_meta = meta.copy()
    for key in ("Filter", "DecodeParms", "F", "DP"):
        clean_meta.pop(key, None)
    return clean_meta


def _decode_ccitt_via_tiff_header(
    data: bytes, w: int, h: int, k_val: int, decoder: str, normalized_meta: dict
) -> bytes:
    """Constructs a fully compliant single-strip TIFF file in-memory."""
    import struct

    header = bytearray(b"II\x2a\x00")
    img_offset = 8
    img_len = len(data)
    ifd_offset = img_offset + img_len
    if ifd_offset % 2 != 0:
        ifd_offset += 1

    header.extend(struct.pack("<I", ifd_offset))
    tiff_bytes = bytearray(header)
    tiff_bytes.extend(data)

    if len(tiff_bytes) < ifd_offset:
        tiff_bytes.extend(b"\x00" * (ifd_offset - len(tiff_bytes)))

    compression = 4 if decoder == "group4" else 3
    black_is_1 = normalized_meta.get("BlackIs1", False)
    if isinstance(black_is_1, str):
        black_is_1 = black_is_1.lower() == "true"
    photometric = 1 if black_is_1 else 0

    tags = [
        (256, 4, 1, w),  # ImageWidth
        (257, 4, 1, h),  # ImageLength
        (258, 3, 1, 1),  # BitsPerSample
        (259, 3, 1, compression),  # Compression
        (262, 3, 1, photometric),  # PhotometricInterpretation
        (273, 4, 1, img_offset),  # StripOffsets
        (278, 4, 1, h),  # RowsPerStrip
        (279, 4, 1, img_len),  # StripByteCounts
    ]
    if compression == 3:
        t4_opt = 1 if k_val > 0 else 0
        tags.append((292, 4, 1, t4_opt))
    elif compression == 4:
        tags.append((293, 4, 1, 0))

    tags.sort(key=lambda x: x[0])

    ifd = bytearray()
    ifd.extend(struct.pack("<H", len(tags)))
    for tag, ttype, count, val in tags:
        if ttype == 3:
            val_field = struct.pack("<HH", val, 0)
        else:
            val_field = struct.pack("<I", val)
        ifd.extend(struct.pack("<HHI", tag, ttype, count))
        ifd.extend(val_field)
    ifd.extend(struct.pack("<I", 0))

    tiff_bytes.extend(ifd)
    return bytes(tiff_bytes)


def _decode_ccitt_image(data: bytes, w: int, h: int, normalized_meta: dict) -> tuple[bytes, dict]:
    """Handles decompression specifically for Group 3 / Group 4 CCITT encoded images."""
    import re
    import struct
    from io import BytesIO
    from PIL import Image

    k_val = -1
    if "K" in normalized_meta:
        try:
            k_val = int(re.sub(r"[^\d-]", "", str(normalized_meta["K"])))
        except ValueError:
            pass
    elif "DecodeParms" in normalized_meta:
        dp_str = str(normalized_meta["DecodeParms"])
        match_k = re.search(r"/K\s+([-\d]+)", dp_str)
        if match_k:
            k_val = int(match_k.group(1))

    decoder = "group4" if k_val < 0 else "group3"
    try:
        tiff_bytes = _decode_ccitt_via_tiff_header(data, w, h, k_val, decoder, normalized_meta)
        img = Image.open(BytesIO(tiff_bytes))
        decoded_data = img.tobytes()
        return decoded_data, _clean_filter_meta(normalized_meta)
    except (ValueError, TypeError, OSError, struct.error) as e:
        # Explanatory comment: Pillow's libtiff interface or the struct packer may fail
        # on malformed dimensions (like 0x0 or corrupted strips). We cleanly revert to raw bytes.
        logger.warning("PIL CCITT decompression failed, falling back to raw: %s", e)
        return data, normalized_meta


def _decode_dct_image(data: bytes, normalized_meta: dict) -> tuple[bytes, dict]:
    """Handles decompression specifically for JPEG / DCT encoded images."""
    from io import BytesIO
    from PIL import Image

    try:
        img = Image.open(BytesIO(data))
        decoded_data = img.tobytes()
        return decoded_data, _clean_filter_meta(normalized_meta)
    except (ValueError, TypeError, OSError) as e:
        # Explanatory comment: Incomplete or corrupted JPEG bitstreams will fail PIL's
        # Image.open decoding phase. We cleanly revert to the raw bytes.
        logger.warning("PIL JPEG decompression failed, falling back to raw: %s", e)
        return data, normalized_meta


def _decode_native_pdf_image(
    data: bytes, w: int, h: int, filt: str, normalized_meta: dict
) -> tuple[bytes, dict]:
    """Handles robust decompression for natively supported PDF filters (Flate, LZW, etc)."""
    import pikepdf

    pdf = pikepdf.new()
    stream_dict = pikepdf.Dictionary()
    stream_dict.Width = w
    stream_dict.Height = h
    stream_dict.BitsPerComponent = int(normalized_meta.get("BitsPerComponent", 8))

    if "ColorSpace" in normalized_meta:
        stream_dict.ColorSpace = pikepdf.Name(normalized_meta["ColorSpace"])

    if "ImageMask" in normalized_meta:
        val = normalized_meta["ImageMask"]
        stream_dict.ImageMask = True if (val == "true" or val is True) else False
        if stream_dict.ImageMask:
            stream_dict.BitsPerComponent = 1

    if filt.startswith("["):
        names = [pikepdf.Name(n.strip()) for n in filt.strip("[]").split()]
        stream_dict.Filter = pikepdf.Array(names)
    else:
        stream_dict.Filter = pikepdf.Name(filt)

    errors_to_catch = (pikepdf.PdfError, ValueError, TypeError, KeyError, OSError)
    if hasattr(pikepdf, "_core") and hasattr(pikepdf._core, "DataDecodingError"):
        errors_to_catch += (pikepdf._core.DataDecodingError,)

    try:
        stream = pdf.make_stream(data)
        stream.stream_dict.update(stream_dict)
        decoded_data = stream.read_bytes()
        return decoded_data, _clean_filter_meta(normalized_meta)
    except errors_to_catch as e:
        # Explanatory comment: Zlib or LZW decoding will fail if the stored bitstream
        # is truncated or syntactically invalid. We cleanly revert to the raw bytes.
        logger.warning("pikepdf native decompression failed, falling back to raw: %s", e)
        return data, normalized_meta


def _is_native_filter(filt: str) -> bool:
    """Helper to detect if a filter is natively supported by pikepdf."""
    return any(
        nf in filt
        for nf in (
            "FlateDecode",
            "LZWDecode",
            "ASCIIHexDecode",
            "ASCII85Decode",
            "RunLengthDecode",
        )
    )


def _decode_inline_image(data: bytes, meta: dict) -> tuple[bytes, dict]:
    """Decodes compressed inline image data using PIL or pikepdf C++ decoders.
    Decoupled via helper functions to maintain minimal cognitive complexity.
    """
    normalized_meta = _normalize_inline_meta(meta)

    w = int(normalized_meta.get("Width", 8))
    h = int(normalized_meta.get("Height", 8))
    filt = normalized_meta.get("Filter", "")

    # If no filter is specified, the data is already raw/uncompressed
    if not filt:
        return data, normalized_meta

    # Path A: CCITTFaxDecode (Group 3 or Group 4)
    if "CCITTFaxDecode" in filt or "CCF" in filt:
        return _decode_ccitt_image(data, w, h, normalized_meta)

    # Path B: DCTDecode (JPEG)
    if "DCTDecode" in filt or "DCT" in filt:
        return _decode_dct_image(data, normalized_meta)

    # Path C: Native PDF filters supported by pikepdf (qpdf)
    if _is_native_filter(filt):
        return _decode_native_pdf_image(data, w, h, filt, normalized_meta)

    # Path D: Fallback for any unsupported or unhandled filters
    return data, normalized_meta


def _save_raw_to_tiff(data: bytes, meta: dict, dest_path: Path) -> None:
    """Saves raw bitmap pixel bytes to an editable TIFF file based on the color space."""
    from PIL import Image

    w = int(meta.get("Width", 8))
    h = int(meta.get("Height", 8))
    cs = meta.get("ColorSpace", "/DeviceGray")
    bpc = int(meta.get("BitsPerComponent", 8))

    if cs in ("/DeviceGray", "/G"):
        mode = "1" if bpc == 1 else "L"
    elif cs in ("/DeviceRGB", "/RGB"):
        mode = "RGB"
    elif cs in ("/DeviceCMYK", "/CMYK"):
        mode = "CMYK"
    elif cs.startswith("/Indexed"):
        mode = "L"
    else:
        mode = "L"

    # PDFs pack bits tightly to 1-byte row boundaries. PIL defaults to 32-bit (4-byte)
    # padding. We calculate the strict PDF stride below to bypass the PIL mismatch.
    if mode == "1":
        stride = (w + 7) // 8
    elif mode == "RGB":
        stride = w * 3
    elif mode == "CMYK":
        stride = w * 4
    else:
        stride = w

    expected_len = stride * h
    if len(data) < expected_len:
        data = data.ljust(expected_len, b"\x00")

    # Use PIL raw decoder to specify the precise 1-byte padded stride
    img = Image.frombytes(mode, (w, h), data, "raw", mode, stride, 1)
    img.save(dest_path, format="TIFF")


def _components_per_pixel(color_space: str) -> int:
    """
    Returns the number of color components per pixel for a normalized
    inline-image /ColorSpace value, mirroring the mode mapping in
    `_save_raw_to_tiff`. Used to compute an unfiltered inline image's exact
    raw byte length ahead of time (see `_exact_unfiltered_data_length`),
    the same stride math `_save_raw_to_tiff` already relies on to unpack
    the bytes correctly.
    """
    if color_space in ("/DeviceRGB", "/RGB"):
        return 3
    if color_space in ("/DeviceCMYK", "/CMYK"):
        return 4
    # /DeviceGray, /Indexed, and anything unrecognized are all 1
    # component-per-pixel for this purpose (an Indexed image's samples are
    # palette indices, one per pixel, regardless of the palette's own
    # component count).
    return 1


def _exact_unfiltered_data_length(normalized_meta: dict) -> int | None:
    """
    Computes the exact raw byte length of an *unfiltered* inline image's
    pixel data from its /W, /H, /BPC, and /CS entries, using the same
    1-byte-per-row-padded stride convention `_save_raw_to_tiff` unpacks
    with. Returns None if /Width or /Height is missing or not a plain
    integer literal (e.g. built from a PDF expression this simple text
    parser doesn't evaluate), since no exact length can be computed in
    that case.

    A filtered image's *compressed* length can't be derived this way at
    all -- only the decoded length is knowable ahead of time -- so this is
    only ever called for the no-/Filter case.
    """
    try:
        w = int(normalized_meta["Width"])
        h = int(normalized_meta["Height"])
    except (KeyError, ValueError, TypeError):
        return None

    bpc = int(normalized_meta.get("BitsPerComponent", 8))
    components = _components_per_pixel(normalized_meta.get("ColorSpace", "/DeviceGray"))
    stride = (w * components * bpc + 7) // 8
    return stride * h


def _locate_inline_image_data_end(
    stream_bytes: bytes, data_start: int, normalized_meta: dict
) -> tuple[int, int] | None:
    """
    Finds where a single inline image's pixel data ends and its trailing
    `EI` operator's own bytes end, starting the search at `data_start`
    (the first byte immediately after the `ID` operator and its one
    required separating whitespace byte, per PDF's own inline-image
    grammar).

    For an unfiltered image, the data's exact length is fully determined
    by /W, /H, /BPC, and /CS (see `_exact_unfiltered_data_length`) -- so
    that length is used directly, sidestepping any content-based `EI`
    search entirely. This is the only way to safely handle a raw pixel
    payload that happens to itself contain a whitespace-bounded byte
    sequence spelling "ID" or "EI", which a naive regex scan over the
    payload bytes cannot distinguish from the genuine terminator.

    For a filtered image, the compressed length isn't derivable ahead of
    time without actually decoding it, so this falls back to scanning for
    the first `\\s+EI` after `data_start` -- the same behavior this
    function replaces, and the same known limitation: a compressed
    bitstream that happens to contain a coincidental whitespace+"EI"
    sequence can still be misparsed. Every filter this codebase's own
    `_decode_inline_image` supports (Flate, LZW, ASCIIHex, ASCII85,
    RunLength, CCITTFax, DCT) has escape-hatch coverage.

    Returns (data_end, remainder_start), where `remainder_start` is the
    stream offset immediately after the terminating `EI` operator itself,
    or None if no terminator could be located at all (a genuinely
    malformed or truncated inline image).
    """
    import re

    exact_len = _exact_unfiltered_data_length(normalized_meta)
    if exact_len is not None and not normalized_meta.get("Filter"):
        data_end = data_start + exact_len
        trailing = re.match(rb"\s*EI", stream_bytes[data_end:])
        if trailing is not None:
            return data_end, data_end + trailing.end()
        # Explanatory comment: the computed length didn't land on a real
        # EI terminator -- e.g. a malformed /W or /H that doesn't actually
        # match the embedded data. Fall through to the scan-based fallback
        # below rather than silently trusting a mismatched exact length.
        logger.debug(
            "Computed inline image length %d did not align with a "
            "trailing EI operator; falling back to scanning for EI.",
            exact_len,
        )

    match = re.search(rb"\s+EI", stream_bytes[data_start:])
    if match is None:
        return None
    return data_start + match.start(), data_start + match.end()


def _process_inline_images_on_export(
    stream_bytes: bytes, font_key: str, glyph_name: str, bitmaps_dir: Path, img_registry: dict
) -> str:
    """
    Scans a glyph content stream for inline images, extracts them losslessly into TIFF files,
    and replaces the binary streams with clean structural text tags.
    """
    import hashlib
    import re

    # The dict portion between BI and ID is always textual PDF syntax (key/value
    # tokens), never arbitrary binary payload, so a non-greedy scan for the
    # first `\s+ID\s+` boundary is safe here -- unlike the payload itself
    # (see _locate_inline_image_data_end for why that can't use the same
    # non-greedy-regex approach).
    header_pattern = re.compile(rb"BI\s+(.*?)\s+ID\s+", re.DOTALL)

    out_parts: list[bytes] = []
    pos = 0
    for match in header_pattern.finditer(stream_bytes):
        if match.start() < pos:
            # Explanatory comment: this header falls inside a previously
            # consumed image's own payload (a coincidental "BI ... ID "
            # byte sequence in binary pixel data) -- not a real inline
            # image start. Skip it rather than double-processing.
            continue

        out_parts.append(stream_bytes[pos : match.start()])

        dict_bytes = match.group(1)
        data_start = match.end()
        img_meta = _parse_inline_dict(dict_bytes)
        normalized_meta = _normalize_inline_meta(img_meta)

        bounds = _locate_inline_image_data_end(stream_bytes, data_start, normalized_meta)
        if bounds is None:
            # Explanatory comment: no terminating EI could be found at all --
            # a genuinely truncated or malformed inline image. Emit the BI
            # header verbatim and let the rest of the stream be scanned
            # normally rather than losing data by guessing a boundary.
            logger.warning(
                "Could not locate EI terminator for inline image in glyph '%s'; "
                "leaving this inline image unextracted.",
                glyph_name,
            )
            out_parts.append(stream_bytes[match.start() : data_start])
            pos = data_start
            continue

        data_end, remainder_start = bounds
        data_bytes = stream_bytes[data_start:data_end]

        img_id = len(img_registry)
        img_filename = f"char_{glyph_name}_img{img_id}.tiff"

        if not bitmaps_dir.exists():
            bitmaps_dir.mkdir(parents=True, exist_ok=True)

        decoded_data, clean_meta = _decode_inline_image(data_bytes, img_meta)
        _save_raw_to_tiff(decoded_data, clean_meta, bitmaps_dir / img_filename)

        original_md5 = hashlib.md5(data_bytes).hexdigest()

        img_registry[f"{glyph_name}_{img_id}"] = {
            "meta": clean_meta,
            "filename": f"font_{font_key}_bitmaps/{img_filename}",
            "original_md5": original_md5,
        }

        meta_str = json.dumps(clean_meta)
        replacement = (
            f"%BEGIN_INLINE_IMAGE%\n"
            f"%META: {meta_str}\n"
            f"%REF: font_{font_key}_bitmaps/{img_filename}\n"
            f"%ID: {glyph_name}_{img_id}\n"
            f"%END_INLINE_IMAGE%"
        ).encode()
        out_parts.append(replacement)
        pos = remainder_start

    out_parts.append(stream_bytes[pos:])
    processed = b"".join(out_parts)
    return processed.decode("latin-1")


def export_type3_font(
    font_obj: Any,
    obj_id: int,
    gen_id: int,
    base_name: str,
    out_dir: Path,
    font_entry: dict,
) -> None:
    """
    Extracts Type 3 character procedures into an ad-hoc text block format (.charprocs)
    and handles lossless extraction of embedded inline bitmaps into editable TIFF files.
    """
    import pikepdf

    charprocs = font_obj.get("/CharProcs")
    if not charprocs:
        return

    font_key = f"{obj_id}_{gen_id}_{base_name}"
    charprocs_filename = f"font_{font_key}.charprocs"
    charprocs_path = out_dir / charprocs_filename
    bitmaps_dir = out_dir / f"font_{font_key}_bitmaps"

    font_entry["charprocs_file"] = charprocs_filename
    font_entry["inline_images"] = {}

    with open(charprocs_path, "w", encoding="utf-8") as f_out:
        for glyph_name, stream_obj in charprocs.items():
            glyph_str = str(glyph_name).lstrip("/")
            if not isinstance(stream_obj, pikepdf.Stream):
                continue

            f_out.write("=" * 72 + "\n")
            f_out.write(f"=== Font {font_key} / CharProcs /{glyph_str}\n")
            f_out.write("=" * 72 + "\n")

            raw_bytes = stream_obj.read_bytes()
            processed_text = _process_inline_images_on_export(
                raw_bytes, font_key, glyph_str, bitmaps_dir, font_entry["inline_images"]
            )
            f_out.write(processed_text + "\n\n")
