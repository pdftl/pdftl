# src/pdftl/fonts/font_subsetting.py

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
Binary-format-aware mechanics for subsetting embedded font programs via
fontTools.subset.

Mirrors the format dispatch already established in font_binary_utils.py
(classify_binary_format) and cff_binary_utils.py: an sfnt-wrapped program
(ttf/otf/cff2) is opened via fontTools.ttLib.TTFont directly; a bare CFF
program (Type1C/CIDFontType0C /FontFile3) is not TTFont-openable on its
own, so it is wrapped in a minimal single-table sfnt shell for the
subsetter's benefit and unwrapped back to a bare CFF table stream
afterwards. Classic Type 1 (/FontFile) has no fontTools.subset support at
all and is not handled here at all -- see pdftl.fonts.type1_to_cff for
that conversion path (Type 1 -> subsetted CFF via redrawn charstrings
rather than fontTools.subset); callers should check
font_binary_utils.classify_binary_format themselves and dispatch "type1"
there instead of reaching this module.

CID resolution for a Type0 font's used CIDs, prior to calling into this
module, is the caller's responsibility (via widths_utils.extract_cid_to_gid_map
for CIDFontType2, or cff_binary_utils._resolve_cff_cid_to_gid for a
CIDFontType0C/"cff_native" descendant) -- this module only knows how to
turn a resolved {gid} set (or {unicode} set, for Simple fonts) into a
subsetted font program.
"""

from __future__ import annotations

import logging
import struct
from io import BytesIO
from typing import Any

logger = logging.getLogger(__name__)


def wrap_bare_cff_in_sfnt(cff_bytes: bytes) -> Any:
    """
    Wraps a bare CFF table byte stream in a minimal sfnt shell so
    fontTools.subset (which, like TTFont, cannot open a bare CFF table on
    its own -- see cff_binary_utils.py's module docstring) has something
    it can operate on. Only the 'CFF ' table is populated; fontTools.subset
    only ever reads/writes the CFF table itself for a CFF-flavored font
    with no 'glyf' table, so no other sfnt tables are needed for a
    round trip through the subsetter.
    """
    from fontTools.ttLib import TTFont, newTable

    tt = TTFont(sfntVersion="OTTO")
    tt["CFF "] = newTable("CFF ")
    tt["CFF "].decompile(cff_bytes, tt)
    return tt


def unwrap_bare_cff_from_sfnt(tt: Any, original_matrices: dict[str, tuple] | None = None) -> bytes:
    """Recompiles just the 'CFF ' table back out of the sfnt shell built
    by wrap_bare_cff_in_sfnt, discarding the shell itself.

    Unlike most sfnt table classes, table_C_F_F_.compile() does not take
    a file-like buffer to write into -- it takes only the owning TTFont
    and returns the compiled bytes directly.

    If `original_matrices` is supplied (see capture_font_matrix), the Top
    DICT's FontMatrix is force-spliced back into the compiled bytes
    whenever it was explicitly present pre-subset -- see
    cff_fontmatrix_splice.splice_top_font_matrix's docstring for why this
    must be a post-compile byte patch rather than a rawDict edit.
    """
    compiled = tt["CFF "].compile(tt)
    if original_matrices and "top" in original_matrices:
        from pdftl.fonts.cff_fontmatrix_splice import splice_top_font_matrix

        compiled = splice_top_font_matrix(compiled, original_matrices["top"])
    return compiled


def open_font_for_subsetting(raw_bytes: bytes, is_bare_cff: bool, context: str = "") -> Any | None:
    """
    Opens an embedded font program for subsetting, returning a TTFont
    (real or a bare-CFF sfnt shell), or None if the program can't be
    parsed at all.
    """
    from fontTools.ttLib import TTFont

    try:
        if is_bare_cff:
            tt = wrap_bare_cff_in_sfnt(raw_bytes)
        else:
            tt = TTFont(BytesIO(raw_bytes))
        # TTFont lazy-loads tables, so a malformed table (e.g. a 'post'
        # table with a garbage version number) won't raise here -- it
        # only surfaces later, off in _collect_gids_for_group, once
        # something finally calls tt.getGlyphOrder(). Force that
        # resolution now, inside this same try/except, so a corrupt font
        # is rejected up front like any other unparseable program rather
        # than crashing deep inside subsetting.
        tt.getGlyphOrder()
        return tt
    # fontTools raises many different exception types
    except Exception as e:  # noqa: BLE001
        label = f" ({context})" if context else ""
        logger.warning(
            "Failed to open embedded font for subsetting%s (font left un-subsetted): %s",
            label,
            e,
        )
        return None


def get_cff_topdict_if_present(tt: Any) -> Any | None:
    """Returns the single Top DICT of `tt`'s 'CFF ' table, or None if this
    font has no CFF table at all (a glyf-flavored TrueType font, which
    never had a FontMatrix operator to begin with)."""
    if "CFF " not in tt:
        return None
    cff = tt["CFF "].cff
    return cff[cff.fontNames[0]]


def capture_font_matrix(tt: Any) -> dict[str, tuple] | None:
    """Snapshots every FontMatrix operand relevant to a CFF program's
    effective rendering matrix before subsetting: the Top DICT's own
    (keyed "top"), plus each FDArray entry's own (keyed "fd:<index>") for
    a CID-keyed font with per-FD FontDicts. A CID-keyed CFF's effective
    matrix is the composition of whichever of these are actually present
    -- either can be the one that gets silently reverted during
    subsetting, so both need to be tracked, not just the top-level one.
    Returns None entirely for a non-CFF font. An individual key is
    omitted (not stored as None) when that particular dict has no
    explicit FontMatrix operator, since that's a legitimate "use the CFF
    default" state, not something to protect.
    """
    topdict = get_cff_topdict_if_present(tt)
    if topdict is None:
        return None
    snapshot: dict[str, tuple] = {}
    top_matrix = topdict.rawDict.get("FontMatrix")
    if top_matrix is not None:
        snapshot["top"] = tuple(top_matrix)
    if hasattr(topdict, "FDArray"):
        for i, fd in enumerate(topdict.FDArray):
            fd_matrix = fd.rawDict.get("FontMatrix")
            if fd_matrix is not None:
                snapshot[f"fd:{i}"] = tuple(fd_matrix)
    return snapshot or None


def restore_font_matrix_if_dropped(
    tt: Any, original_matrices: dict[str, tuple] | None, context: str = ""
) -> None:
    """
    SUPERSEDED -- do not call. Confirmed by direct extraction that
    fontTools' compiled bytes still omit the Top DICT FontMatrix operator
    even with an epsilon-perturbed, non-default value written into
    rawDict before compile(); whatever compile() does, it is not a simple
    value-equality check against the CFF spec default.

    The real fix is cff_fontmatrix_splice.splice_top_font_matrix, applied
    to the already-compiled bytes -- see unwrap_bare_cff_from_sfnt and
    cff_binary_utils.patch_cff_widths for the call sites. Body removed;
    kept as a documented dead end.
    """
    raise NotImplementedError(
        "restore_font_matrix_if_dropped is superseded by "
        "cff_fontmatrix_splice.splice_top_font_matrix; remove this call "
        "site and call unwrap_bare_cff_from_sfnt(tt, original_matrices) "
        "instead."
    )


def _build_subsetter_options(keep_names: bool = False, retain_gids: bool = False) -> Any:
    from fontTools import subset

    options = subset.Options()
    # Workaround for a fontTools CFF-subsetting bug where local/global
    # subroutine indices can be renumbered incorrectly when the subr
    # array shrinks across a bias threshold (Adobe TN#5177 SS4.7: 107 /
    # 1131 / 32768), causing a charstring's callsubr/callgsubr to
    # resolve to the WRONG subroutine post-subset -- producing a
    # garbled/wildly-mis-scaled glyph outline for just that glyph. This
    # symptom is otherwise silent (no exception, no warning) and can
    # look exactly like a corrupted-matrix bug when rendered. Setting
    # desubroutinize=True inlines every subroutine call at subset time
    # instead of renumbering, eliminating the bias-remap step entirely
    # -- at the cost of some size (less shared-subroutine reuse), which
    # is an acceptable trade at pdftl's boundary since correctness beats
    # a few extra bytes here.
    options.desubroutinize = True
    options.retain_gids = retain_gids
    options.notdef_outline = True
    options.recalc_bounds = True
    options.recalc_timestamp = False
    options.glyph_names = keep_names
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.legacy_kern = True

    # handles legacy/symbol subtables in general; format-0 subtables specifically need
    # _promote_legacy_format0_cmap_subtables below, since fontTools drops format1 0 unconditionally
    # regardless of these options.
    options.legacy_cmap = True
    options.symbol_cmap = True
    return options


def run_subsetter(
    tt: Any,
    unicodes: set[str],
    gids: set[int],
    keep_names: bool = False,
    retain_gids: bool = False,
    context: str = "",
) -> bool:
    """
    Runs fontTools.subset.Subsetter against a TTFont in place. Returns
    True if the subsetter produced a non-empty glyph set (more than just
    .notdef) worth writing back; False if there was nothing to keep or
    the subsetter itself failed.
    """
    from fontTools import subset

    if not gids and not unicodes:
        return False

    options = _build_subsetter_options(keep_names=keep_names, retain_gids=retain_gids)
    subsetter = subset.Subsetter(options=options)
    if gids:
        subsetter.populate(gids=sorted(gids))
    if unicodes:
        unicode_ints = {ord(char) for s in unicodes for char in s}
        subsetter.populate(unicodes=sorted(unicode_ints))

    _promote_legacy_format0_cmap_subtables(tt)

    # fontTools.subset's _prune_post_subset unconditionally calls
    # OS/2.recalcUnicodeRanges(), which does `for table in
    # ttFont["cmap"].tables` with no guard for a missing 'cmap' table --
    # it simply assumes one exists whenever 'OS/2' does. That assumption
    # is false for a legitimate, common case: a CIDFontType2 font
    # addressed purely via /CIDToGIDMap (Identity-H encoding) has no
    # reason to carry a 'cmap' table at all, since PDF text is shown by
    # CID, never looked up by Unicode/character code. Rather than patch
    # fontTools itself, temporarily supply an empty 'cmap' table (zero
    # subtables) so recalcUnicodeRanges's loop is simply a no-op, then
    # remove it again afterward so subsetting never adds a table the
    # original font didn't have.
    had_cmap = "cmap" in tt
    if not had_cmap and "OS/2" in tt:
        from fontTools.ttLib import newTable

        empty_cmap = newTable("cmap")
        empty_cmap.tableVersion = 0
        empty_cmap.tables = []
        tt["cmap"] = empty_cmap

    try:
        subsetter.subset(tt)
    except (KeyError, ValueError, TypeError, struct.error, IndexError) as e:
        # A malformed or unusually-structured font program (e.g. broken
        # layout tables, an inconsistent glyph order) can make the
        # subsetter itself raise rather than just retaining fewer glyphs
        # than requested; leave the original font program untouched
        # rather than propagate this out of a single-font failure.
        label = f" ({context})" if context else ""
        logger.warning("fontTools subsetting failed%s: %s", label, e)
        logger.debug("Traceback:", exc_info=True)
        return False
    finally:
        if not had_cmap and "cmap" in tt:
            del tt["cmap"]

    return len(tt.getGlyphOrder()) > 1


def gids_for_simple_font_via_cmap(tt: Any, codes: set[int]) -> set[int]:
    """
    Resolves a Simple font's used character codes to GIDs via the font's
    own best cmap (the same (platform, encoding) preference chain, with
    the Windows-Symbol fallback, that font_binary_sfnt.py's width-sync
    path already uses). Only meaningful for sfnt-flavored fonts that
    actually carry a 'cmap' table -- a bare-CFF shell built by
    wrap_bare_cff_in_sfnt has no 'cmap' at all, so callers must not
    reach this for fmt == "bare_cff".
    """
    from fontTools.ttLib import TTLibError

    from pdftl.fonts.font_binary_sfnt import _effective_cmap_code, _get_best_cmap

    if "cmap" not in tt:
        return set()
    try:
        cmap = _get_best_cmap(tt) or {}
    except (OSError, ValueError, KeyError, AttributeError, TypeError, TTLibError) as e:
        logger.debug("Failed to read cmap for glyph retention: %s", e)
        return set()

    glyph_order = tt.getGlyphOrder()
    name_to_gid = {name: i for i, name in enumerate(glyph_order)}

    gids: set[int] = set()
    for raw_code, gname in cmap.items():
        if _effective_cmap_code(raw_code) in codes and gname in name_to_gid:
            gids.add(name_to_gid[gname])
    return gids


def _get_best_cmap_safe(tt: Any) -> Any | None:
    if "cmap" not in tt:
        return None
    try:
        return tt.getBestCmap()
    except Exception:  # noqa: BLE001 - defensive
        return None


def _resolve_code_to_gid(
    code: int,
    differences_map: Any,
    base_encoding_map: Any,
    name_to_gid: dict[str, int],
    best_cmap: Any | None,
) -> int | None:
    from fontTools import agl

    from pdftl.fonts.font_encoding_tables import _resolve_glyph_name

    gname = _resolve_glyph_name(code, None, differences_map, base_encoding_map)
    if gname is None:
        return None
    if gname in name_to_gid:
        return name_to_gid[gname]
    if best_cmap is None:
        return None
    unicode_val = agl.AGL2UV.get(gname)
    if unicode_val is None:
        return None
    font_name = best_cmap.get(unicode_val)
    if font_name is not None and font_name in name_to_gid:
        return name_to_gid[font_name]
    return None


def gids_for_simple_font_via_encoding(
    tt: Any, codes: set[int], differences: list | None, base_encoding: str | None
) -> set[int]:
    """
    Resolves a Simple font's used character codes to GIDs via the PDF's
    own /Encoding (/BaseEncoding + /Differences), mapping code -> glyph
    name -> GID via the font's glyph order. This is the only correct
    path for a bare-CFF shell (no 'cmap' table exists to fall back on)
    and is also required whenever /Differences is present, since a
    symbolic/custom-encoded simple font's code-to-glyph mapping is
    defined by the PDF, not by whatever the font's own cmap happens to
    contain.

    The glyph name resolved here (via AGL for a /BaseEncoding table, or
    directly from /Differences) is only guaranteed to match
    tt.getGlyphOrder() when the font's own 'post' table actually carries
    real per-glyph names (format 1.0/2.0). A 'post' format 3.0 font (no
    names at all -- common for TrueType, not some rare edge case) has
    every name synthesized by fontTools itself, using whatever
    (platform, encoding) cmap subtable it happens to prefer internally
    -- which can produce a name like "uni0093" for a glyph that AGL
    would call "quotedblleft", simply because some (1,0) Mac-ish cmap
    subtable maps the *raw byte code* 0x93 to that glyph and fontTools'
    naming heuristic used that over the semantically-correct (3,1)
    Unicode cmap entry. A direct name_to_gid.get("quotedblleft") then
    fails outright, even though the font unambiguously has the right
    glyph -- just under a name nobody could have predicted.

    When the direct name match misses, fall back through the font's own
    Unicode cmap: look up the AGL name's Unicode value, then ask the
    font's OWN best cmap what IT calls the glyph at that codepoint, and
    use that name instead. This is self-consistent by construction --
    both name_to_gid and the cmap's resolved name come from the same
    tt.getGlyphOrder() -- so it succeeds regardless of how nonsensical
    fontTools' synthesized name looks.
    """
    from pdftl.fonts.font_encoding_tables import _get_maps

    differences_map, base_encoding_map = _get_maps(differences, base_encoding)
    glyph_order = tt.getGlyphOrder()
    name_to_gid = {name: i for i, name in enumerate(glyph_order)}
    best_cmap = _get_best_cmap_safe(tt)

    gids: set[int] = set()
    for code in codes:
        gid = _resolve_code_to_gid(
            code, differences_map, base_encoding_map, name_to_gid, best_cmap
        )
        if gid is not None:
            gids.add(gid)
    return gids


def gids_for_cid_font(tt: Any, cids: set[int], cid_to_gid_map: dict[int, int] | str) -> set[int]:
    """Resolves a CIDFontType2 (TrueType-flavored) Type0 font's used CIDs
    to GIDs via /CIDToGIDMap, matching font_binary_sfnt.py's
    _resolve_cid_glyph_name convention."""
    glyph_order = tt.getGlyphOrder()
    gids: set[int] = set()
    for cid in cids:
        if cid_to_gid_map == "Identity":
            gid = cid
        elif isinstance(cid_to_gid_map, dict):
            gid = cid_to_gid_map.get(cid)
        else:
            gid = None
        if gid is not None and 0 <= gid < len(glyph_order):
            gids.add(gid)
    return gids


def gids_for_cff_native_cid_font(tt: Any, cids: set[int]) -> set[int]:
    """Resolves a CIDFontType0C (CFF-native CID-keyed) font's used CIDs to
    GIDs via the CFF's own ROS/charset mechanism, matching
    cff_binary_utils._resolve_cff_cid_to_gid."""
    from pdftl.fonts.cff_binary_utils import _resolve_cff_cid_to_gid

    cff = tt["CFF "].cff
    topdict = cff[cff.fontNames[0]]
    gids: set[int] = set()
    for cid in cids:
        gid = _resolve_cff_cid_to_gid(topdict, cid)
        if gid is not None:
            gids.add(gid)
    return gids


def subset_simple_font(
    tt: Any,
    codes: set[int],
    unicodes: set[str],
    keep_names: bool,
    *,
    is_bare_cff: bool = False,
    differences: list | None = None,
    base_encoding: str | None = None,
    retain_gids: bool = False,
) -> bool:
    """Subsets a Simple font in place, retaining glyphs reachable via the
    font's own cmap for `codes` plus anything reachable via `unicodes`
    (covers symbolic fonts whose only cmap subtable maps raw codes to
    glyph names unrelated to their /ToUnicode meaning).

    For a bare-CFF program (no 'cmap' table exists) or whenever
    /Differences is present (the PDF's own encoding, not the font's
    cmap, is authoritative for which glyph a code paints), resolution
    goes through /Encoding instead of the font's cmap.
    """
    if is_bare_cff or differences:
        gids = gids_for_simple_font_via_encoding(tt, codes, differences, base_encoding)
    else:
        gids = gids_for_simple_font_via_cmap(tt, codes)
    return run_subsetter(tt, unicodes, gids, keep_names=keep_names, retain_gids=retain_gids)


def subset_cid_font(
    tt: Any, cids: set[int], cid_to_gid_map: dict[int, int] | str, keep_names: bool
) -> tuple[bool, dict[int, int]]:
    """Subsets a CIDFontType2 Type0 font in place, retaining glyphs
    reachable via /CIDToGIDMap for `cids`. Returns (success, updated_cid_to_gid_map)."""
    glyph_order = tt.getGlyphOrder()
    cid_to_name: dict[int, str] = {}
    for cid in cids:
        if cid_to_gid_map == "Identity":
            gid = cid
        elif isinstance(cid_to_gid_map, dict):
            gid = cid_to_gid_map.get(cid)
        else:
            gid = None
        if gid is not None and 0 <= gid < len(glyph_order):
            cid_to_name[cid] = glyph_order[gid]

    gids = gids_for_cid_font(tt, cids, cid_to_gid_map)
    if not run_subsetter(tt, set(), gids, keep_names=keep_names):
        return False, {}

    new_glyph_order = tt.getGlyphOrder()
    name_to_new_gid = {name: i for i, name in enumerate(new_glyph_order)}

    new_cid_to_gid_map: dict[int, int] = {}
    for cid, name in cid_to_name.items():
        if name in name_to_new_gid:
            new_cid_to_gid_map[cid] = name_to_new_gid[name]

    return True, new_cid_to_gid_map


def subset_cff_native_cid_font(tt: Any, cids: set[int], keep_names: bool) -> bool:
    """Subsets a CIDFontType0C (CFF-native CID-keyed) font in place,
    retaining glyphs reachable via the CFF's own ROS/charset mechanism
    for `cids`."""
    gids = gids_for_cff_native_cid_font(tt, cids)
    return run_subsetter(tt, set(), gids, keep_names=keep_names)


def _promote_legacy_format0_cmap_subtables(tt: Any) -> None:
    """
    fontTools.subset unconditionally drops every format-0 cmap subtable
    during prune_pre_subset (`self.tables = [t for t in self.tables if
    t.format != 0]`) -- independent of options.legacy_cmap/symbol_cmap,
    which only gate the *platform/encoding* filters above that line, not
    this format check. A symbolic font whose only cmap subtable is a
    legacy Macintosh (1, 0) format-0 table (see _build_subsetter_options'
    own docstring on this exact shape) would otherwise lose its cmap
    entirely even with legacy_cmap/symbol_cmap set.

    Format 6 ("trimmed table mapping") encodes the identical
    code->glyph-name data as format 0 (single-byte codes, one glyph per
    code, no ranges) and is NOT subject to any of prune_pre_subset's
    filters, so converting in place here lets the subtable's actual
    mapping survive subsetting intact. fontTools derives firstCode/
    entryCount from the `.cmap` dict automatically on compile, so this
    is a lossless, purely mechanical format swap.
    """
    if "cmap" not in tt:
        return
    from fontTools.ttLib.tables._c_m_a_p import CmapSubtable

    cmap_table = tt["cmap"]
    existing_triples = {
        (t.platformID, t.platEncID, t.language) for t in cmap_table.tables if t.format != 0
    }
    new_tables = []
    for t in cmap_table.tables:
        if t.format == 0 and (t.platformID, t.platEncID, t.language) not in existing_triples:
            replacement = CmapSubtable.newSubtable(6)
            replacement.platformID = t.platformID
            replacement.platEncID = t.platEncID
            replacement.language = t.language
            replacement.cmap = dict(t.cmap)
            new_tables.append(replacement)
        else:
            new_tables.append(t)
    cmap_table.tables = new_tables
