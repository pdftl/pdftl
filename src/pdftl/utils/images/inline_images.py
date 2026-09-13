# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/images/inline_images.py
"""Rewrites inline images (BI ... ID ... EI blocks) found by
pdftl.utils.images.finders.extract_pdf_images back into their host
content stream.

An inline image has no indirect object of its own -- its dictionary and
raw bytes live literally inside whichever content stream drew it (a
page's own /Contents stream, one element of a /Contents array, or a Form
XObject's content stream). There is therefore nothing analogous to
delete_images._overwrite_with_stub()'s `obj.write(...)` to call on it
directly: the *instruction* has to be located again inside its host
stream, replaced, and the whole stream re-serialized and written back.

extract_pdf_images() tags every inline entry with `host_objgen` (the
objgen of the exact Stream object the BI/ID/EI block was found in) and
`instruction_index` (its position within that stream's own instruction
list, as parsed by pikepdf.parse_content_stream) so this module can
re-locate the same instruction without needing byte offsets, which would
be invalidated the moment any earlier instruction in the stream changed
size.
"""

import logging
import io
import zlib

logger = logging.getLogger(__name__)


# Raw BI/ID/EI bytes for a minimal, spec-legal inline image: a 1x1
# ImageMask, fully transparent (Decode [0 1] with a single 0xFF data
# byte -- PDF 32000-1 8.9.6.2 -- matches delete_images.
# _overwrite_with_stub's own choice of an ImageMask stub, so the two
# code paths leave visually consistent results).
_STUB_INLINE_IMAGE_BYTES = b"BI /W 1 /H 1 /BPC 1 /IM true /D [0 1] ID \xff EI"


# _parse_single_inline_instruction (below) replaces what was formerly a
# single-purpose _stub_inline_instruction(), generalized so the same
# "round-trip raw BI/ID/EI bytes through pikepdf's own parser" trick can
# build a stub OR a real replacement (recolored / reimported pixel data).


def _parse_single_inline_instruction(pdf, raw_bytes: bytes):
    """Round-trips literal BI/ID/EI bytes through pikepdf's own
    content-stream parser to get back a real INLINE IMAGE instruction --
    see the original _stub_inline_instruction's docstring for why this
    (rather than hand-constructing a PdfInlineImage) is the reliable path.
    """
    import pikepdf

    tmp_stream = pdf.make_stream(raw_bytes)
    parsed = list(pikepdf.parse_content_stream(tmp_stream))
    if not parsed or str(parsed[0].operator) != "INLINE IMAGE":
        raise RuntimeError("Failed to construct inline image instruction from raw bytes")
    return parsed[0]


# Canonical (mixed-case, spec-standard) names for the filter/colorspace
# strings this codebase stores lowercased (e.g. img["format"] ==
# "flatedecode") -- inline image dictionaries want the properly-cased
# full name (/FlateDecode), not the lowercase internal spelling.
_CANONICAL_FILTER_NAMES = {
    "flatedecode": "FlateDecode",
    "dctdecode": "DCTDecode",
    "lzwdecode": "LZWDecode",
    "runlengthdecode": "RunLengthDecode",
    "asciihexdecode": "ASCIIHexDecode",
    "ascii85decode": "ASCII85Decode",
    "ccittfaxdecode": "CCITTFaxDecode",
}
_CANONICAL_COLORSPACE_NAMES = {
    "devicegray": "DeviceGray",
    "devicergb": "DeviceRGB",
    "devicecmyk": "DeviceCMYK",
}

# Shared PIL-mode -> PDF-colorspace-name mapping used by every caller that
# re-encodes a decoded inline image's pixels (recolor/resample/reimport).
# Kept in one place so a mode added/removed here doesn't silently drift
# out of sync between those three callers.
_MODE_TO_COLORSPACE = {
    "L": "DeviceGray",
    "RGB": "DeviceRGB",
    "CMYK": "DeviceCMYK",
    "1": "DeviceGray",
}


def build_inline_image_bytes(
    width, height, bits, colorspace_name, filter_name, data: bytes
) -> bytes:
    """Builds literal BI/ID/EI bytes for a real (non-stub) inline image
    carrying `data` as its (already filtered/compressed) pixel payload.
    Used by recolor and reimport paths, which need to write actual pixel
    data back rather than delete_images' fixed 1x1 stub.

    `colorspace_name`/`filter_name` are matched case-insensitively
    against the canonical name tables above and fall back to whatever
    was passed in if unrecognized (still spec-legal -- an unusual but
    valid /Filter or /ColorSpace name).
    """
    cs = _CANONICAL_COLORSPACE_NAMES.get(
        colorspace_name.lower().lstrip("/"), colorspace_name.lstrip("/")
    )
    parts = [
        f"BI /Width {int(width)} /Height {int(height)} /BitsPerComponent {int(bits)}",
        f"/ColorSpace /{cs}",
    ]
    if filter_name:
        filt = _CANONICAL_FILTER_NAMES.get(
            filter_name.lower().lstrip("/"), filter_name.lstrip("/")
        )
        parts.append(f"/Filter /{filt}")
    header = " ".join(parts) + " ID "
    return header.encode("ascii") + data + b" EI"


def mode_to_colorspace(mode: str) -> str:
    """Maps a PIL image mode to the PDF colorspace name used when
    re-encoding it as an inline image. Falls back to DeviceGray for any
    mode not in the safe set (matching the recolor/resample callers'
    prior behavior of only ever writing back L/RGB/CMYK/1-bit data)."""
    return _MODE_TO_COLORSPACE.get(mode, "DeviceGray")


def encode_inline_replacement(
    pil_img, fmt: str | None, quality: int, cs_name: str | None = None, bits: int | None = None
) -> bytes:
    """Encodes `pil_img`'s current pixel data as inline BI...EI
    replacement bytes, choosing DCTDecode when `fmt` indicates the
    original was JPEG-compressed (and the image isn't 1-bit, which is
    never re-encoded as JPEG), otherwise FlateDecode.

    `cs_name`/`bits` default from `pil_img.mode` when omitted -- pass
    them explicitly only when a caller needs to override the mode-derived
    default (e.g. writing back a fixed colorspace regardless of the
    decoded mode).

    This consolidates what was previously three near-identical
    fmt-branch/encode blocks duplicated across
    grayscale.recolor_inline_images, resample_images._build_inline_replacement,
    and export_import_images._import_single_inline_image.
    """
    if cs_name is None:
        cs_name = mode_to_colorspace(pil_img.mode)
    if bits is None:
        bits = 1 if pil_img.mode == "1" else 8

    if (fmt or "").lower() == "dctdecode" and pil_img.mode != "1":
        buf = io.BytesIO()
        pil_img.convert("RGB").save(buf, format="JPEG", quality=quality)
        return build_inline_image_bytes(
            width=pil_img.width,
            height=pil_img.height,
            bits=8,
            colorspace_name="DeviceRGB",
            filter_name="DCTDecode",
            data=buf.getvalue(),
        )

    data = zlib.compress(pil_img.tobytes(), level=6)
    return build_inline_image_bytes(
        width=pil_img.width,
        height=pil_img.height,
        bits=bits,
        colorspace_name=cs_name,
        filter_name="FlateDecode",
        data=data,
    )


def decode_inline_pil(pdf, ref: dict):
    """Best-effort decode of an inline image's ACTUAL pixel data to a PIL
    Image, by re-locating its PdfInlineImage operand in the host stream
    (via ref's host_objgen/instruction_index) and asking pikepdf to
    decode it.

    NOTE: assumes PdfInlineImage exposes an `.as_pil_image()` (mirroring
    PdfImage's decode API) -- verify against your pikepdf version; if the
    method is named differently, swap it in here only. Wrapped in a
    broad except because decoding is inherently best-effort: pikepdf's
    own inline-image byte-reading helpers are documented (see
    finders._read_inline_data_bytes) to raise for pathologically tiny
    (<3pt) images via their internal throwaway-PDF round-trip, and any
    other decode failure should degrade to "skip this one image" rather
    than aborting a whole export/recolor pass.

    Returns None on any failure -- callers must treat that as "could not
    decode this inline image", not attempt a fallback re-interpretation.
    """
    import pikepdf

    try:
        objgen = tuple(ref["host_objgen"])
        idx = ref["instruction_index"]
        stream_obj = pdf.get_object(objgen)
        instructions = list(pikepdf.parse_content_stream(stream_obj))
        inst = instructions[idx]
        if str(inst.operator) != "INLINE IMAGE":
            return None
        iimage = inst.operands[0]
        return iimage.as_pil_image()
    except (
        pikepdf.PdfError,
        KeyError,
        IndexError,
        AttributeError,
        TypeError,
        ValueError,
    ) as exc:
        logger.debug("Could not decode inline image pixel data: %s", exc)
        return None


def _group_by_host(refs: list[dict]) -> dict[tuple, set[int]]:
    """Groups inline-image entries (as returned by extract_pdf_images) by
    the host stream objgen they came from, collecting the set of
    instruction indices to replace within each host -- mapped to the
    ORIGINAL ref dict for that index, not just a bare set, so a caller's
    raw_bytes_fn(ref) can be invoked per-instruction with the right ref.

    Entries missing `inline`, `host_objgen`, or `instruction_index` are
    skipped with a warning rather than silently ignored -- rewriting is
    opt-in per-entry, and a caller passing an untaggable entry (e.g. one
    built by hand, or an XObject-image entry passed in by mistake) is
    worth surfacing rather than quietly dropping.
    """
    by_host: dict[tuple, dict[int, dict]] = {}
    for ref in refs:
        host_objgen = ref.get("host_objgen")
        idx = ref.get("instruction_index")
        if not ref.get("inline") or host_objgen is None or idx is None:
            logger.warning(
                "Skipping non-rewritable image entry (missing inline/"
                "host_objgen/instruction_index tags): page=%s name=%s",
                ref.get("page"),
                ref.get("name"),
            )
            continue
        by_host.setdefault(tuple(host_objgen), {})[idx] = ref
    return by_host


def _rewrite_host_stream(pdf, objgen, idx_to_ref, raw_bytes_fn) -> int:
    """Re-parses one host stream, replaces its matched instructions, and
    writes it back if anything changed. Returns the number of instructions
    replaced in this stream. Split out of apply_inline_rewrites to keep
    that function's branching within complexity limits."""
    import pikepdf

    try:
        stream_obj = pdf.get_object(objgen)
    except (pikepdf.PdfError, KeyError) as err:
        logger.warning("Could not resolve host stream %s: %s", objgen, err)
        return 0
    if not isinstance(stream_obj, pikepdf.Stream):
        logger.warning("Could not resolve host stream %s: not a stream object", objgen)
        return 0

    try:
        instructions = list(pikepdf.parse_content_stream(stream_obj))
    except (pikepdf.PdfError, TypeError, ValueError) as err:
        logger.warning("Could not re-parse host stream %s: %s", objgen, err)
        return 0

    changed = 0
    for i, inst in enumerate(instructions):
        ref = idx_to_ref.get(i)
        if ref is None:
            continue
        if str(inst.operator) != "INLINE IMAGE":
            logger.warning(
                "Instruction %d in stream %s is no longer an inline "
                "image (found %s); leaving it untouched.",
                i,
                objgen,
                inst.operator,
            )
            continue
        try:
            raw = raw_bytes_fn(ref)
        except (RuntimeError, ValueError, TypeError, KeyError) as exc:
            logger.warning(
                "Failed to build replacement for inline image on page %s: %s",
                ref.get("page"),
                exc,
            )
            continue
        if raw is None:
            continue
        instructions[i] = _parse_single_inline_instruction(pdf, raw)
        changed += 1

    if changed:
        stream_obj.write(pikepdf.unparse_content_stream(instructions))
    return changed


def apply_inline_rewrites(pdf, refs: list[dict], raw_bytes_fn) -> int:
    """Like apply_inline_replacements, but the replacement content for
    each matched inline image comes from `raw_bytes_fn(ref) -> bytes`
    (full literal BI...EI bytes), rather than always the deletion stub.
    `raw_bytes_fn` returning None for a given ref leaves that instruction
    untouched. Used by recolor/reimport paths that need to write real
    pixel data back into an inline image rather than blanking it out.

    Entries are grouped by host stream first so a stream holding several
    inline images being replaced in the same pass is re-parsed and
    rewritten exactly ONCE. Replacing an instruction in place (rather
    than deleting it outright) is what keeps every other instruction's
    index in that same stream stable across the whole batch -- deleting
    would shift every later index after the first removal, breaking any
    subsequent lookup in the same pass.

    Returns the number of inline images actually replaced. An entry
    whose (host_objgen, instruction_index) no longer points at an
    "INLINE IMAGE" instruction -- e.g. the stream was mutated by
    something else since extraction, or a stale ref was passed in -- is
    left untouched and logged rather than corrupting an unrelated
    instruction.
    """
    by_host = _group_by_host(refs)
    changed = 0
    for objgen, idx_to_ref in by_host.items():
        changed += _rewrite_host_stream(pdf, objgen, idx_to_ref, raw_bytes_fn)
    return changed


def apply_inline_replacements(pdf, refs: list[dict]) -> int:
    """Replaces each inline image described by `refs` with a tiny
    transparent stub. Thin wrapper around apply_inline_rewrites kept for
    the delete_images use case and existing callers/tests.
    """
    return apply_inline_rewrites(pdf, refs, lambda _ref: _STUB_INLINE_IMAGE_BYTES)
