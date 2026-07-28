# src/pdftl/operations/subset_fonts.py

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
subset_fonts: shrinks embedded font programs down to only the glyphs a
document actually uses.

Unlike export_fonts/import_fonts (which round-trip a font through an
external directory for manual editing), this operates entirely in
memory: font_subset_scan.py determines which raw character codes are
actually painted under each font resource, and pdftl.fonts.font_subsetting
re-subsets the embedded font program to keep only the glyphs those codes
resolve to.

Binary format dispatch mirrors font_binary_utils.classify_binary_format:
  - "sfnt" (ttf/otf/cff2): subsetted via a real TTFont directly.
  - "bare_cff" (Type1C/CIDFontType0C /FontFile3): wrapped in a minimal
    sfnt shell for subsetting purposes (see font_subsetting.py), then
    unwrapped back to a bare CFF table afterwards.
  - "type1" (/FontFile, PFB): fontTools.subset has no Type 1 support, so
    this is instead converted to a subsetted CFF program (Type 1C) via
    pdftl.fonts.type1_to_cff, and the descriptor's stream is swapped from
    /FontFile to /FontFile3 accordingly.

/Widths (or /W) and /CIDToGIDMap are resynced after a successful rewrite
using the same widths_utils machinery import_fonts uses for its
patch_font_metrics width-sync mode, so a subsetted font's metrics can
never drift out of sync with its own surviving glyph set.
"""

from __future__ import annotations

import random
import string
import dataclasses
import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.fonts import font_subsetting as fs
from pdftl.fonts import type1_to_cff as t1cff
from pdftl.fonts.font_binary_utils import (
    classify_binary_format,
    get_font_widths_from_file,
    rekey_name_widths_to_hex_codes,
)
from pdftl.fonts.font_extraction_utils import find_font_descriptor
from pdftl.fonts.widths_utils import (
    extract_cid_to_gid_map,
    extract_font_widths,
    update_cid_to_gid_map,
    update_font_widths,
)
from pdftl.operations.helpers.font_ops_shared import get_target_pages
from pdftl.operations.helpers.font_subset_scan import collect_used_codes
from pdftl.utils.dependencies import ensure_dependencies
from pdftl.utils.keyval_parser import parse_keyval_list
from pdftl.fonts.font_extraction_utils import SUBSET_PREFIX_RE

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)


_SUBSET_FONTS_LONG_DESC = """
The `subset_fonts` operation shrinks every embedded font program in the
document down to only the glyphs that are actually painted, by scanning
content streams for used character codes and re-subsetting each font's
binary program in-memory via fontTools.

This is purely a size-reduction operation: it never changes which glyphs
render, only which *unused* glyphs are dropped from the font program
itself. /Widths, /W, and /CIDToGIDMap are all resynced afterwards so they
stay consistent with the subsetted glyph set.

### Arguments

* `[page_range]`: Optional page ranges to limit which content streams are
  scanned for used character codes. Fonts referenced only outside this
  range are left untouched. Omit to scan the whole document.
* `[keep_names]`: If given, glyph names are preserved in the subsetted
  program (larger output, useful for further manual editing). By default
  glyph names are dropped for maximum size reduction.

### Limitations

* Classic Type 1 (`/FontFile`) programs are converted to a subsetted CFF
  (Type 1C) program rather than subsetted in place, since fontTools has
  no Type 1 subsetter; the descriptor's `/FontFile` is replaced with a
  `/FontFile3` accordingly.
* Type 3 fonts have no font *program* to subset and are skipped.
* A font program referenced identically from more than one `/Font`
  dictionary (the same underlying `/FontFile*` stream) is subsetted once,
  to the union of codes collected across every dictionary that shares it.
"""

_SUBSET_FONTS_EXAMPLES = [
    {
        "cmd": "in.pdf subset_fonts output out.pdf",
        "desc": "Subset every embedded font in the document to only the glyphs it uses.",
    },
    {
        "cmd": "in.pdf subset_fonts 1-10 output out.pdf",
        "desc": "Subset fonts based on usage on pages 1-10 only.",
    },
]


def _get_embedded_stream_key(descriptor: Any) -> str | None:
    for key in ("/FontFile", "/FontFile2", "/FontFile3"):
        if key in descriptor:
            return key
    return None


def _embedded_format_for(descriptor: Any, stream_key: str) -> str | None:
    if stream_key == "/FontFile2":
        return "ttf"
    if stream_key == "/FontFile":
        return "pfb"
    if stream_key == "/FontFile3":
        subtype = str(descriptor["/FontFile3"].get("/Subtype", "")).lstrip("/")
        if subtype == "OpenType":
            return "cff2"
        if subtype in ("Type1C", "CIDFontType0C"):
            return "cff"
    return None


def _codes_to_extra_gids_via_tounicode(font_obj: Any, codes: set[int], tt: Any) -> set[int]:
    """
    Resolves a Simple font's used character codes to GIDs via /ToUnicode,
    for retention purposes only. /ToUnicode is a copy/paste-and-search
    aid, not the font's real encoding -- it is legitimate (and common,
    e.g. for symbolic annotation-icon fonts) for it to map a raw code to
    an unrelated Unicode codepoint. Resolving to a GID here and returning
    it via `gids` (never via subsetter.populate(unicodes=...)) is
    required: passing those codepoints as `unicodes` causes fontTools to
    rebuild the retained cmap keyed by the ToUnicode codepoint instead of
    the font's original raw code, silently breaking raw-code lookup for
    exactly the codes the PDF content stream actually uses.
    """
    if "/ToUnicode" not in font_obj:
        return set()
    from pdftl.fonts.cmap_utils import parse_to_unicode_cmap

    try:
        raw = font_obj["/ToUnicode"].read_bytes()
    except (AttributeError, TypeError) as e:
        logger.debug("Failed to read /ToUnicode for code resolution: %s", e)
        return set()

    mapping = parse_to_unicode_cmap(raw)
    unicode_chars = {mapping[f"{code:02X}"] for code in codes if f"{code:02X}" in mapping}
    if not unicode_chars:
        return set()
    unicode_ints = {ord(ch) for s in unicode_chars for ch in s}
    return fs.gids_for_simple_font_via_cmap(tt, unicode_ints)


def _get_simple_font_encoding(font_obj: Any) -> tuple[list | None, str | None]:
    """
    Reads /Encoding/Differences and /Encoding/BaseEncoding off a simple
    font, if present -- the same data _resync_widths_after_subset already
    pulls for its rekey_name_widths_to_hex_codes call, but needed here
    up front so glyph retention (not just width rekeying) can honor the
    PDF's own encoding rather than trusting the font's internal cmap.
    """
    if "/Encoding" not in font_obj:
        return None, None
    enc = font_obj["/Encoding"]

    import pikepdf

    if isinstance(enc, pikepdf.Name):
        # /Encoding /WinAnsiEncoding directly (no /Differences dict at
        # all) is legal PDF -- only the Dictionary form ("/Encoding
        # /BaseEncoding ... /Differences ... >>") supports "in" checks.
        return None, str(enc).lstrip("/")

    differences = None
    base_encoding = None
    if "/Differences" in enc:
        differences = list(enc["/Differences"])
    if "/BaseEncoding" in enc:
        base_encoding = str(enc["/BaseEncoding"]).lstrip("/")
    return differences, base_encoding


def _stream_identity(descriptor: Any) -> Any:
    """
    A stable identity for a font descriptor's embedded font-program
    stream, suitable for grouping multiple /Font dictionaries that all
    reference the very same physical /FontFile* object -- a common
    real-world pattern (e.g. several differently-/Encoding-d slices of
    one symbol font, all pointing at one shared embedded program to
    avoid duplicating the binary). Uses .objgen (matching
    font_subset_scan._font_key's reasoning: pikepdf hands back a fresh
    wrapper object on every access, so id() alone is not stable across
    repeated lookups of the same underlying indirect object).
    """
    stream_key = _get_embedded_stream_key(descriptor)
    if stream_key is None:
        return None
    stream_obj = descriptor[stream_key]
    objgen = getattr(stream_obj, "objgen", None)
    if objgen and objgen != (0, 0):
        return objgen
    return id(stream_obj)


@dataclasses.dataclass
class _SubsetStat:
    """One row of subsetting outcome, for the end-of-run summary."""

    label: str
    before_bytes: int
    after_bytes: int
    before_glyphs: int = -1
    after_glyphs: int = -1


def _descriptor_identity(descriptor: Any) -> Any:
    objgen = getattr(descriptor, "objgen", None)
    if objgen and objgen != (0, 0):
        return objgen
    return id(descriptor)


def _generate_subset_tag() -> str:
    """A random 6-uppercase-letter subset tag per ISO 32000-2 9.6.4.3
    ("AAAAAA+FontName"). Six letters gives 26**6 (~309M) combinations,
    which is plenty to make an accidental collision between two
    different subsets in the same document negligible."""
    return "".join(random.choices(string.ascii_uppercase, k=6))


def _tag_name(raw_name: str, tag: str) -> str:
    """Prepends `tag` to `raw_name`, replacing any existing subset tag
    rather than stacking a second one on top of it."""
    base = raw_name.split("+", 1)[-1] if SUBSET_PREFIX_RE.match(raw_name) else raw_name
    return f"{tag}+{base}"


def _update_descendant_font(font_obj: Any, new_name: str | None, pikepdf_mod: Any) -> None:
    if (
        not new_name
        or str(font_obj.get("/Subtype", "")) != "/Type0"
        or "/DescendantFonts" not in font_obj
    ):
        return
    try:
        descendant = font_obj.DescendantFonts[0]
        descendant["/BaseFont"] = pikepdf_mod.Name(f"/{new_name}")
    except (AttributeError, IndexError, TypeError):
        pass


def _update_descriptor(
    descriptor: Any,
    raw_base: str,
    tag: str,
    seen_descriptors: dict[Any, None],
    pikepdf_mod: Any,
) -> None:
    if descriptor is None:
        return
    desc_key = _descriptor_identity(descriptor)
    if desc_key in seen_descriptors:
        return
    seen_descriptors[desc_key] = None

    desc_raw = str(descriptor.get("/FontName", "")).lstrip("/") or raw_base
    desc_new = _tag_name(desc_raw, tag) if desc_raw else None
    if desc_new:
        descriptor["/FontName"] = pikepdf_mod.Name(f"/{desc_new}")


def _apply_subset_tag(group_entries: list[tuple[Any, Any, set[int]]], pikepdf_mod: Any) -> None:
    """
    Ensures every distinct /Font dict (and its /FontDescriptor, and any
    Type0 descendant CIDFont) in a just-subsetted group carries a subset
    tag reflecting that its embedded program no longer contains the full
    original glyph set -- per ISO 32000-2 9.6.4.3. A single tag is
    generated ONCE per group (not per /Font dict), since every entry in
    group_entries shares one physical, now-unioned font program: giving
    them different tags would violate the spec's "same tag <=> same
    glyph set" requirement just as surely as leaving them untagged does.

    A /Font dict that already carries a tag is re-tagged too, since
    subsetting further shrinks its underlying program's glyph set to a
    (possibly different) subset of what the old tag was assigned for.
    """
    tag = _generate_subset_tag()

    seen_descriptors: dict[Any, None] = {}
    for font_obj, descriptor, _codes in group_entries:
        raw_base = str(font_obj.get("/BaseFont", "")).lstrip("/")
        new_name = _tag_name(raw_base, tag) if raw_base else None

        # BaseFont lives on the individual /Font dict -- write it for
        # EVERY entry, even when several entries share one descriptor.
        if new_name:
            font_obj["/BaseFont"] = pikepdf_mod.Name(f"/{new_name}")

        _update_descendant_font(font_obj, new_name, pikepdf_mod)
        _update_descriptor(descriptor, raw_base, tag, seen_descriptors, pikepdf_mod)


def _subset_type1_font_group_binary(group_entries: list[tuple[Any, Any, set[int]]]) -> bool:
    """
    Converts a classic Type 1 (/FontFile) embedded program -- shared by
    every (font_obj, descriptor, codes) entry in `group_entries` -- to a
    subsetted CFF (Type 1C) stream, keeping the UNION of glyphs each
    entry's own /Encoding resolves its own codes to (not just the first
    entry's), and swaps every distinct descriptor in the group from
    /FontFile to /FontFile3 on success.
    """
    first_font_obj, first_descriptor, _ = group_entries[0]
    stream_obj = first_descriptor["/FontFile"]
    try:
        raw_bytes = stream_obj.read_bytes()
    except (AttributeError, TypeError, OSError) as e:
        logger.debug("Failed to read embedded Type 1 font stream: %s", e)
        return False

    font = t1cff.open_type1_font_bytes(raw_bytes)
    if font is None:
        return False

    glyph_names: set[str] = set()
    code_to_name: dict[int, str] = {}
    for font_obj, _descriptor, codes in group_entries:
        differences, base_encoding = _get_simple_font_encoding(font_obj)
        glyph_names |= t1cff.resolve_glyph_names(font, codes, differences, base_encoding)
        # Merge in a code->name mapping regardless of whether this /Font
        # dict has its own /Encoding: resolve_code_to_glyph_names already
        # applies the correct per-code priority (/Differences, then
        # /BaseEncoding, then the font's own built-in encoding), so a
        # code NOT covered by this dict's /Differences still needs its
        # built-in-encoding fallback baked into the shared CFF's own
        # Encoding table -- a symbolic font's /Differences array
        # routinely covers only some codes, leaving the rest to fall
        # back to the font program's built-in encoding per PDF 32000-1
        # 9.6.6.2. A code a dict's own /Differences DOES cover simply
        # round-trips back to the same name here, so this is safe to do
        # unconditionally rather than only when /Encoding is wholly
        # absent.
        code_to_name.update(
            t1cff.resolve_code_to_glyph_names(font, codes, differences, base_encoding)
        )

    cff_bytes = t1cff.build_cff_from_glyph_names(font, glyph_names, code_to_name)
    if not cff_bytes:
        return False

    import pikepdf

    stream_obj.write(cff_bytes)
    logger.debug(
        "%s -> %s bytes (Type 1 converted to CFF, %d /Font dict(s) sharing this program)",
        len(raw_bytes),
        len(cff_bytes),
        len(group_entries),
    )

    seen_descriptors: dict[Any, None] = {}
    for _font_obj, descriptor, _codes in group_entries:
        key = _descriptor_identity(descriptor)
        if key in seen_descriptors:
            continue
        seen_descriptors[key] = None
        if "/FontFile" not in descriptor:
            continue
        del descriptor["/FontFile"]
        descriptor["/FontFile3"] = stream_obj
        descriptor["/FontFile3"]["/Subtype"] = pikepdf.Name("/Type1C")
    return True


def _collect_gids_for_group(
    tt: Any, group_entries: list[tuple[Any, Any, set[int]]], fmt: str
) -> tuple[set[int], bool]:
    """Resolves the UNION of GIDs every entry in `group_entries` needs
    retained, based on each entry's own /Subtype, /CIDToGIDMap, and
    /Encoding. `retain_gids` comes back True whenever any entry is a
    CIDFontType2 Type0 font, since its /CIDToGIDMap indexes by GID
    position and can't tolerate the subsetter renumbering glyphs."""
    gids: set[int] = set()
    retain_gids = False
    for font_obj, _descriptor, codes in group_entries:
        is_type0 = str(font_obj.get("/Subtype", "")) == "/Type0"
        if is_type0:
            if fmt == "bare_cff":
                gids |= fs.gids_for_cff_native_cid_font(tt, codes)
                retain_gids = True

                # from pdftl.fonts.cff_binary_utils import is_cid_keyed

                # cff = tt["CFF "].cff
                # topdict = cff[cff.fontNames[0]]
                # if not is_cid_keyed(topdict):
                #     retain_gids = True
            else:
                cid_to_gid_map = extract_cid_to_gid_map(font_obj)
                gids |= fs.gids_for_cid_font(tt, codes, cid_to_gid_map)
                retain_gids = True
        else:
            gids |= _codes_to_extra_gids_via_tounicode(font_obj, codes, tt)
            differences, base_encoding = _get_simple_font_encoding(font_obj)
            if fmt == "bare_cff" or differences or base_encoding:
                gids |= fs.gids_for_simple_font_via_encoding(tt, codes, differences, base_encoding)
            else:
                gids |= fs.gids_for_simple_font_via_cmap(tt, codes)
    return gids, retain_gids


def _serialize_subsetted_font(
    tt: Any, fmt: str, original_font_matrices: dict[str, tuple] | None = None
) -> bytes | None:
    """Recompiles a just-subsetted TTFont (or bare-CFF sfnt shell) back
    into raw bytes, returning None if fontTools chokes on the rewritten
    table set rather than propagating the exception out of a
    single-font failure.

    `original_font_matrices` (see font_subsetting.capture_font_matrix) is
    threaded through to unwrap_bare_cff_from_sfnt, which splices the Top
    DICT FontMatrix back into the compiled bytes if fontTools dropped it
    -- see cff_fontmatrix_splice.splice_top_font_matrix."""

    from fontTools.ttLib import TTLibError

    try:
        if fmt == "bare_cff":
            return fs.unwrap_bare_cff_from_sfnt(tt, original_font_matrices)
        from io import BytesIO

        buf = BytesIO()
        tt.save(buf)
        return buf.getvalue()
    except (TTLibError, KeyError, ValueError) as e:
        logger.warning("Failed to serialize subsetted font program: %s", e)
        return None


def _write_back_subsetted_stream(
    stream_obj: Any,
    new_bytes: bytes,
    raw_bytes: bytes,
    stream_key: str,
    group_entries: list[tuple[Any, Any, set[int]]],
) -> tuple[bool, int, int]:
    """Writes a subsetted font program back to its embedded stream,
    skipping the rewrite entirely if it didn't actually shrink, and
    resyncing /Length1 on every distinct descriptor sharing this stream
    for the two stream keys (/FontFile, /FontFile2) that carry one."""
    if len(new_bytes) >= len(raw_bytes):
        logger.debug("Subsetted font was not smaller than original; skipping rewrite.")
        return False, len(raw_bytes), len(raw_bytes)

    logger.debug(
        "%s -> %s bytes (%d /Font dict(s) sharing this program %s)",
        len(raw_bytes),
        len(new_bytes),
        len(group_entries),
        stream_obj.objgen,
    )
    stream_obj.write(new_bytes)
    if stream_key in ("/FontFile", "/FontFile2"):
        seen_descriptors: dict[Any, None] = {}
        for _font_obj, descriptor, _codes in group_entries:
            key = _descriptor_identity(descriptor)
            if key in seen_descriptors:
                continue
            seen_descriptors[key] = None
            descriptor[stream_key].Length1 = len(new_bytes)
    return True, len(raw_bytes), len(new_bytes)


def _subset_sfnt_or_cff_font_group_binary(
    group_entries: list[tuple[Any, Any, set[int]]], keep_names: bool, fmt: str
) -> tuple[bool, int, int, int, int]:
    """
    Subsets a shared sfnt or bare-CFF embedded program -- shared by every
    entry in `group_entries` -- keeping the UNION of GIDs/unicodes each
    entry's own /Subtype, /CIDToGIDMap, and /Encoding resolve its own
    codes to.

    Returns (rewrote, before_bytes, after_bytes, before_glyphs, after_glyphs).
    """
    first_font_obj, first_descriptor, _ = group_entries[0]
    stream_key = _get_embedded_stream_key(first_descriptor)
    stream_obj = first_descriptor[stream_key]
    try:
        raw_bytes = stream_obj.read_bytes()
    except (AttributeError, TypeError, OSError) as e:
        logger.debug("Failed to read embedded font stream: %s", e)
        return False, 0, 0, 0, 0

    # BaseFont name (falling back to the stream's object/generation
    # number if absent/unreadable) so a failed-to-open font can actually
    # be identified in the log, rather than just "some font, somewhere".
    base_font_name = str(first_font_obj.get("/BaseFont", "")).lstrip("/") or None
    context = base_font_name or f"stream {stream_obj.objgen}"

    tt = fs.open_font_for_subsetting(raw_bytes, is_bare_cff=(fmt == "bare_cff"), context=context)
    if tt is None:
        return False, len(raw_bytes), len(raw_bytes), 0, 0

    before_glyphs = len(tt.getGlyphOrder())

    original_font_matrices = fs.capture_font_matrix(tt)  # pre-subset snapshot(s),
    # used post-subset by _serialize_subsetted_font via
    # cff_fontmatrix_splice, since fontTools can drop an explicit Top
    # DICT FontMatrix during subset/recompile.

    gids, retain_gids = _collect_gids_for_group(tt, group_entries, fmt)
    if not fs.run_subsetter(tt, set(), gids, keep_names, retain_gids=retain_gids, context=context):
        return False, len(raw_bytes), len(raw_bytes), before_glyphs, before_glyphs

    after_glyphs = len(tt.getGlyphOrder())

    new_bytes = _serialize_subsetted_font(tt, fmt, original_font_matrices)
    if new_bytes is None:
        return False, len(raw_bytes), len(raw_bytes), before_glyphs, before_glyphs

    rewrote, before_bytes, after_bytes = _write_back_subsetted_stream(
        stream_obj, new_bytes, raw_bytes, stream_key, group_entries
    )
    return rewrote, before_bytes, after_bytes, before_glyphs, after_glyphs


def _widths_cid_to_gid_map(
    font_obj: Any, embedded_format: str | None
) -> dict[int, int] | str | None:
    """The /CIDToGIDMap (or the "cff_native" sentinel) get_font_widths_from_file
    needs to key a Type0 font's re-derived widths by CID; None for a
    Simple font, which needs no such mapping."""
    if str(font_obj.get("/Subtype", "")) != "/Type0":
        return None
    if embedded_format == "cff":
        return "cff_native"
    return extract_cid_to_gid_map(font_obj)


def _extract_widths_from_subsetted_stream(
    stream_obj: Any, cid_to_gid_map: dict[int, int] | str | None, embedded_format: str | None
) -> dict[str, float]:
    """Writes the just-rewritten embedded font stream out to a temp file
    and derives its glyph widths via get_font_widths_from_file, which
    (unlike pikepdf's own stream objects) needs a real filesystem path."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp) / "font_bin"
        tmp_path.write_bytes(stream_obj.read_bytes())
        return get_font_widths_from_file(
            tmp_path, cid_to_gid_map=cid_to_gid_map, embedded_format=embedded_format
        )


def _rekey_simple_cff_widths(font_obj: Any, new_widths: dict[str, float]) -> dict[str, float]:
    """A bare-CFF Simple font's widths come back from
    get_font_widths_from_file keyed by glyph name, not hex code -- rekey
    them via the /Font dict's own /Encoding, matching
    _get_simple_font_encoding's resolution priority elsewhere in this
    module."""
    differences, base_encoding = _get_simple_font_encoding(font_obj)
    return rekey_name_widths_to_hex_codes(new_widths, differences, base_encoding)


def _resync_widths_after_subset(
    font_obj: Any, descriptor: Any, embedded_format: str | None, pikepdf_mod: Any
) -> None:
    """Additively fills in any /Widths (or /W) entry missing for a code/CID
    that's newly derivable from the subsetted font program, WITHOUT
    overwriting any code/CID that already has a width in the PDF.

    Subsetting never changes which glyph an already-used code/CID selects
    (it only drops the *other*, unused glyphs from the font program), so
    every surviving code/CID's own width is unaffected by subsetting and
    the PDF's existing /Widths or /W value for it remains correct as-is.
    Overwriting it with a value re-derived from the rebuilt font program
    is not just unnecessary but actively harmful: a PDF producer's
    /Widths entries are commonly deliberately tuned to differ slightly
    from the font's own nominal advance widths (e.g. pdfTeX's microtype
    package implements character protrusion/expansion purely via such
    per-character /Widths tweaks, with no corresponding change to the
    font program itself); blindly overwriting them here silently discards
    that tuning and introduces visible spacing drift, even though nothing
    about the font's own metrics for those glyphs actually changed.
    """

    stream_key = _get_embedded_stream_key(descriptor)
    if stream_key is None:
        return
    stream_obj = descriptor[stream_key]
    is_type0 = str(font_obj.get("/Subtype", "")) == "/Type0"

    cid_to_gid_map = _widths_cid_to_gid_map(font_obj, embedded_format)
    new_widths = _extract_widths_from_subsetted_stream(stream_obj, cid_to_gid_map, embedded_format)
    if not new_widths:
        return

    if embedded_format == "cff" and not is_type0:
        new_widths = _rekey_simple_cff_widths(font_obj, new_widths)

    old_widths = extract_font_widths(font_obj)
    # Additive-only: every code/CID already present in old_widths keeps its
    # existing (possibly deliberately-tuned) value untouched; only a
    # code/CID with NO existing width entry at all gets one filled in from
    # the subsetted font program. Note update_font_widths/update_font_W
    # rebuild the whole /Widths or /W array from the map they're given
    # (any code/CID absent from it is dropped, not merely left alone), so
    # the full old_widths set must always be included verbatim here rather
    # than passing just the additions.
    merged = dict(old_widths)
    for code, width in new_widths.items():
        merged.setdefault(code, width)
    if merged != old_widths:
        update_font_widths(font_obj, merged, pikepdf_mod)


def _resync_cid_to_gid_after_subset(
    pdf: pikepdf.Pdf, font_obj: Any, embedded_format: str | None, pikepdf_mod: Any
) -> None:
    """
    Rebuilds /CIDToGIDMap for a CIDFontType2 descendant after subsetting if needed.
    Not applicable to a "cff_native" descendant, which has no
    /CIDToGIDMap at all.
    """
    if embedded_format == "cff":
        return
    if str(font_obj.get("/Subtype", "")) != "/Type0":
        return
    old_map = extract_cid_to_gid_map(font_obj)
    if isinstance(old_map, dict):
        update_cid_to_gid_map(font_obj, old_map, pikepdf_mod, pdf)


def _subset_and_resync_group(
    pdf: pikepdf.Pdf,
    group_entries: list[tuple[Any, Any, set[int]]],
    keep_names: bool,
    pikepdf_mod: Any,
) -> tuple[int, _SubsetStat | None]:
    """
    Subsets ONE physical embedded font program -- shared by every
    (font_obj, descriptor, codes) entry in `group_entries` -- to the
    union of glyphs all entries need, then resyncs each entry's own
    /Widths and /CIDToGIDMap individually (since each /Font dictionary
    can still have its own /FirstChar-/LastChar range, /Encoding, and
    /CIDToGIDMap into that one shared, now-unioned glyph set). Returns
    how many /Font dictionaries were successfully resynced.
    """
    first_font_obj, first_descriptor, _ = group_entries[0]
    if str(first_font_obj.get("/Subtype", "")) == "/Type3":
        return 0, None

    stream_key = _get_embedded_stream_key(first_descriptor)
    if stream_key is None:
        return 0, None
    embedded_format = _embedded_format_for(first_descriptor, stream_key)
    fmt = classify_binary_format(embedded_format)

    base_font_name = str(first_font_obj.get("/BaseFont", "")).lstrip("/") or None
    label = base_font_name or f"stream {first_descriptor[stream_key].objgen}"

    if fmt == "type1":
        rewrote = _subset_type1_font_group_binary(group_entries)
        # Type 1 -> CFF conversion doesn't currently track before/after
        # byte or glyph counts the way the sfnt/bare_cff path does;
        # report the outcome without a delta rather than a misleading
        # 0 -> 0.
        stat = _SubsetStat(label, -1, -1) if rewrote else None
    elif fmt in ("sfnt", "bare_cff"):
        rewrote, before_b, after_b, before_g, after_g = _subset_sfnt_or_cff_font_group_binary(
            group_entries, keep_names, fmt
        )
        stat = _SubsetStat(label, before_b, after_b, before_g, after_g) if rewrote else None
    else:
        logger.debug("Skipping subset for unrecognized font binary format.")
        return 0, None

    if not rewrote:
        return 0, None

    _apply_subset_tag(group_entries, pikepdf_mod)

    resynced = 0
    for font_obj, descriptor, _codes in group_entries:
        # Re-fetch stream key/embedded format post-conversion (e.g. Type 1 -> /FontFile3 CFF)
        new_stream_key = _get_embedded_stream_key(descriptor)
        new_embedded_format = (
            _embedded_format_for(descriptor, new_stream_key) if new_stream_key else None
        )
        _resync_widths_after_subset(font_obj, descriptor, new_embedded_format, pikepdf_mod)
        _resync_cid_to_gid_after_subset(pdf, font_obj, new_embedded_format, pikepdf_mod)
        resynced += 1
    return resynced, stat


def _group_fonts_by_stream(
    codes_by_font_id: dict[Any, set[int]],
    resolved_fonts: dict[Any, Any],
) -> dict[Any, list[tuple[Any, Any, set[int]]]]:
    from collections import defaultdict

    groups: dict[Any, list[tuple[Any, Any, set[int]]]] = defaultdict(list)
    for font_id, codes in codes_by_font_id.items():
        font_obj = resolved_fonts.get(font_id)
        if font_obj is None:
            continue
        descriptor = find_font_descriptor(font_obj)
        if descriptor is None:
            continue
        stream_id = _stream_identity(descriptor)
        if stream_id is None:
            continue
        groups[stream_id].append((font_obj, descriptor, codes))
    return groups


def _log_subset_stat(s: _SubsetStat) -> None:
    if s.before_bytes < 0:
        logger.info("  %s: subsetted (Type 1 -> CFF)", s.label)
        return

    byte_pct = 100 * (1 - s.after_bytes / s.before_bytes) if s.before_bytes else 0
    if s.before_glyphs >= 0:
        glyph_pct = 100 * (1 - s.after_glyphs / s.before_glyphs) if s.before_glyphs else 0
        logger.info(
            "  %s: %s -> %s bytes (-%.0f%%), %d -> %d glyphs (-%.0f%%)",
            s.label,
            s.before_bytes,
            s.after_bytes,
            byte_pct,
            s.before_glyphs,
            s.after_glyphs,
            glyph_pct,
        )
    else:
        logger.info(
            "  %s: %s -> %s bytes (-%.0f%%)", s.label, s.before_bytes, s.after_bytes, byte_pct
        )


def _log_subset_summary(subsetted_count: int, stats: list[_SubsetStat]) -> None:
    if not stats:
        logger.info("Subsetted %d font program(s).", subsetted_count)
        return

    sized = [s for s in stats if s.before_bytes >= 0]
    total_before_bytes = sum(s.before_bytes for s in sized)
    total_after_bytes = sum(s.after_bytes for s in sized)
    glyph_sized = [s for s in sized if s.before_glyphs >= 0]
    total_before_glyphs = sum(s.before_glyphs for s in glyph_sized)
    total_after_glyphs = sum(s.after_glyphs for s in glyph_sized)

    for s in stats:
        _log_subset_stat(s)

    if not sized:
        logger.info("Subsetted %d font program(s).", subsetted_count)
        return

    total_saved_pct = (
        100 * (1 - total_after_bytes / total_before_bytes) if total_before_bytes else 0
    )
    summary = (
        f"Subsetted {subsetted_count} font program(s), "
        f"saving {total_before_bytes - total_after_bytes} bytes (-{total_saved_pct:.0f}%)"
    )
    if glyph_sized:
        glyph_saved_pct = (
            100 * (1 - total_after_glyphs / total_before_glyphs) if total_before_glyphs else 0
        )
        summary += (
            f", {total_before_glyphs - total_after_glyphs} glyph(s) removed "
            f"(-{glyph_saved_pct:.0f}%)"
        )
    logger.info(summary + ".")


@register_operation(
    "subset_fonts",
    tags=["in_place", "fonts", "subset", "optimize"],
    type="single input operation",
    desc="Shrink embedded fonts to only the glyphs actually used",
    long_desc=_SUBSET_FONTS_LONG_DESC,
    usage="<input> subset_fonts [<page_range>] [keep_names] output <out.pdf>",
    examples=_SUBSET_FONTS_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def subset_fonts(pdf: pikepdf.Pdf, specs: list[str]) -> OpResult:
    import pikepdf

    raw_tokens: list[str] = []
    parse_keyval_list(specs or [], bare_tokens=raw_tokens, allowed_keys=[], context="subset_fonts")

    ensure_dependencies(
        feature_name="subset_fonts",
        dependencies={"fontTools": "fonttools"},
        extra_tag="subset-fonts",
    )

    keep_names = "keep_names" in raw_tokens
    page_specs = [t for t in raw_tokens if t != "keep_names"]

    target_pages = get_target_pages(pdf, page_specs)
    pages = (
        [pdf.pages[i - 1] for i in target_pages] if target_pages is not None else list(pdf.pages)
    )

    codes_by_font_id, resolved_fonts = collect_used_codes(pages)
    if not codes_by_font_id:
        logger.info("No text-showing operators found in the target pages; nothing to subset.")
        return OpResult(success=True, pdf=pdf)

    groups = _group_fonts_by_stream(codes_by_font_id, resolved_fonts)

    subsetted_count = 0
    stats: list[_SubsetStat] = []
    for group_entries in groups.values():
        resynced, stat = _subset_and_resync_group(pdf, group_entries, keep_names, pikepdf)
        subsetted_count += resynced
        if stat is not None:
            stats.append(stat)

    _log_subset_summary(subsetted_count, stats)
    return OpResult(success=True, pdf=pdf)
