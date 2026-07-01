# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/fonts/font_binary_sfnt.py

"""
sfnt-specific (fontTools.ttLib.TTFont-based) mechanics for reading and
mutating advance-width metrics and glyph vectors inside TrueType and
OpenType (TTF/OTF/OpenType-CFF/CFF2) font programs.

Split out of font_binary_utils.py, which now holds only the public,
format-agnostic dispatch surface (`get_font_widths_from_file`,
`patch_font_file_metrics`, `squash_font_file_vectors`,
`classify_binary_format`); everything in *this* module assumes it has
already been handed a real, TTFont-openable sfnt file and just needs to do
the actual reading/patching/squashing.

For Type0 (CID-keyed) fonts, /Differences never applies and a CID-keyed
font's own Unicode cmap is frequently absent, partial, or numerically
unrelated to its CIDs (especially for subsetted CJK fonts), so resolving
glyphs via that cmap can silently patch nothing, or the wrong glyph
entirely. The CID-keyed functions here (`_get_cid_widths`,
`_patch_cid_metrics`, `_squash_cid_glyphs`, and their per-glyph helpers)
resolve the true glyph via the font's own /CIDToGIDMap-derived GID instead
of any cmap.
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
from typing import Any

from pdftl.fonts.font_encoding_tables import (
    _collect_target_codes,
    _get_maps,
    _resolve_glyph_name,
)

logger = logging.getLogger(__name__)

# fontTools' own TTFont.getBestCmap() default cmapPreferences stops at
# (0, 0) and never tries (3, 0) -- the Windows-Symbol platform/encoding
# pair a real symbolic font (ISO 32000-2 9.6.6.2's "Symbolic" Flags case)
# commonly carries as its ONLY cmap subtable, with no Unicode cmap at
# all. Without this fallback, such a font's width sync always reads back
# an empty cmap and silently does nothing.
_CMAP_PREFERENCES_WITH_SYMBOL_FALLBACK = (
    (3, 10),
    (0, 6),
    (0, 4),
    (3, 1),
    (0, 3),
    (0, 2),
    (0, 1),
    (0, 0),
    (3, 0),
    (1, 0),
)

# Per ISO 32000-2 9.6.6.4, a symbolic TrueType font's (3, 0) cmap
# conventionally maps codes in this Windows-Symbol private-use range
# rather than the raw PDF character code itself (e.g. PDF code 0x41 is
# embedded in the font's own cmap as 0xF041). A plain code is used as-is
# for any code outside this range.
_SYMBOL_CMAP_OFFSET_LOW = 0xF000
_SYMBOL_CMAP_OFFSET_HIGH = 0xF0FF


def _get_best_cmap(tt: Any) -> dict[int, str] | None:
    """
    Resolves the font's best-guess cmap, extending fontTools' own default
    cmapPreferences with the (3, 0) Windows-Symbol and (1, 0) Macintosh
    platform/encoding pairs -- see _CMAP_PREFERENCES_WITH_SYMBOL_FALLBACK.
    """
    return tt.getBestCmap(cmapPreferences=_CMAP_PREFERENCES_WITH_SYMBOL_FALLBACK)


def _effective_cmap_code(code: int) -> int:
    """Un-offsets a Windows-Symbol (3, 0) cmap code back to the plain PDF
    character code it represents, per ISO 32000-2 9.6.6.4 -- see module
    docstring on _SYMBOL_CMAP_OFFSET_LOW/HIGH."""
    if _SYMBOL_CMAP_OFFSET_LOW <= code <= _SYMBOL_CMAP_OFFSET_HIGH:
        return code - _SYMBOL_CMAP_OFFSET_LOW
    return code


def _resolve_cid_glyph_name(glyph_order: list, gid: int) -> str | None:
    """
    Resolves a glyph name from a GID via the font's own glyph order table.

    Returns None if the GID falls outside the font's actual glyph set --
    e.g. a /CIDToGIDMap entry referencing a GID the embedded font program
    doesn't actually define, which can happen with a mismatched or
    corrupted font/PDF pairing.
    """
    if 0 <= gid < len(glyph_order):
        return glyph_order[gid]
    return None


def _get_cid_widths(
    tt: Any, hmtx: Any, cid_to_gid_map: dict[int, int] | str, scale: float
) -> dict[str, float]:
    """
    Reads CID-keyed advance widths from a CIDFontType2 font, resolved via
    /CIDToGIDMap rather than the font's own Unicode cmap.
    """
    widths: dict[str, float] = {}
    glyph_order = tt.getGlyphOrder()

    if cid_to_gid_map == "Identity":
        # Under Identity, CID == GID for every glyph, and GID is simply
        # the position of that glyph in the font's own glyph order table.
        cid_gid_pairs = ((gid, gid) for gid in range(len(glyph_order)))
    else:
        cid_gid_pairs = cid_to_gid_map.items()

    for cid, gid in cid_gid_pairs:
        gname = _resolve_cid_glyph_name(glyph_order, gid)
        if gname is None:
            continue
        try:
            raw_w, _ = hmtx[gname]
        except KeyError:
            continue
        widths[f"{cid:04X}"] = raw_w * scale

    return widths


def get_font_widths_via_ttfont(
    filepath: Path, cid_to_gid_map: dict[int, int] | str | None = None
) -> dict[str, float]:
    """The sfnt-only (TTFont-based) width-reading logic; dispatched to by
    pdftl.fonts.font_binary_utils.get_font_widths_from_file."""
    widths: dict[str, float] = {}
    try:
        from fontTools.ttLib import TTFont, TTLibError
    except ImportError:
        return widths

    try:
        tt = TTFont(filepath)
        hmtx = tt["hmtx"]

        # Zero / Division guard check
        units_per_em = tt["head"].unitsPerEm
        if not units_per_em:
            logger.warning(
                "Font in %s has missing or invalid unitsPerEm (0). Skipping metrics load.",
                filepath.name,
            )
            return widths

        scale = 1000.0 / units_per_em

        if cid_to_gid_map is not None:
            return _get_cid_widths(tt, hmtx, cid_to_gid_map, scale)

        cmap = _get_best_cmap(tt)
        if cmap is None:
            # No usable cmap at all (any platform/encoding), including the (3, 0)
            # symbol fallback -- nothing to key widths by. Skip rather than raise.
            logger.debug("No usable cmap found in font file %s.", filepath.name)
            return widths
        for code, gname in cmap.items():
            hex_code = f"{_effective_cmap_code(code):02X}"
            raw_w, _ = hmtx[gname]
            widths[hex_code] = raw_w * scale
    except (
        OSError,
        ValueError,
        KeyError,
        AttributeError,
        TypeError,
        TTLibError,
    ) as e:
        logger.debug("Failed to read metrics from font file: %s", e)
    return widths


def _patch_cff_table_in_sfnt(
    tt: Any,
    pdf_widths: dict[str, float],
    differences: list | None,
    base_encoding: str | None,
    cid_to_gid_map: dict[int, int] | str | None,
) -> bool:
    """
    Patches the advance width recorded in an sfnt-wrapped CFF table's own
    charstrings, alongside hmtx -- an OpenType/CFF program's real advance
    width lives redundantly in both tables, and CFF-aware consumers (a
    charstring's own `.width`, some PDF-embedded-font validators) read the
    CFF table's copy, not hmtx.

    For a CID-keyed sfnt-wrapped CFF (`cid_to_gid_map` given), CID->glyph
    resolution goes through the CFF's own ROS/charset mechanism rather
    than /CIDToGIDMap -- the same two-branch logic
    pdftl.fonts.cff_binary_utils applies to a bare CID-keyed CFF, since
    the CFF table's internal CID-keying is identical whether or not it's
    wrapped in an sfnt container. `cid_to_gid_map` itself is not
    consulted here at all; it only gates which of the two resolution
    paths below runs.
    """
    if "CFF " not in tt:
        return False

    from pdftl.fonts.cff_binary_utils import _patch_single_cff_width

    cff = tt["CFF "].cff
    topdict = cff[cff.fontNames[0]]

    if cid_to_gid_map is not None:
        return _patch_cff_cid_table_in_sfnt(topdict, pdf_widths)

    differences_map, base_encoding_map = _get_maps(differences, base_encoding)
    cmap = _get_best_cmap(tt) or {}

    patched_any = False
    for code in _collect_target_codes(cmap, differences_map, base_encoding_map):
        gname = cmap.get(code)
        resolved_gname = _resolve_glyph_name(code, gname, differences_map, base_encoding_map)
        if resolved_gname is None or resolved_gname not in topdict.CharStrings.charStrings:
            continue
        hex_code = f"{code:02X}"
        target_hex = hex_code if hex_code in pdf_widths else f"{code:04X}"
        if target_hex not in pdf_widths:
            continue
        if _patch_single_cff_width(topdict, resolved_gname, pdf_widths[target_hex]):
            patched_any = True
    return patched_any


def _patch_cff_cid_table_in_sfnt(topdict: Any, pdf_widths: dict[str, float]) -> bool:
    """
    Patches a CID-keyed sfnt-wrapped CFF table's charstring widths,
    resolved via the CFF's own ROS/charset mechanism (ISO 32000-2
    9.7.4.2), exactly as pdftl.fonts.cff_binary_utils._patch_cid_widths
    does for a bare CID-keyed CFF -- see that function's docstring for
    the CID-keyed vs. non-CID-keyed (direct-GID) branch distinction.
    `pdf_widths` is keyed by 4-digit-hex CID, matching the /W convention.
    """
    from pdftl.fonts.cff_binary_utils import (
        _glyph_name_for_gid,
        _patch_single_cff_width,
        _resolve_cff_cid_to_gid,
    )

    patched_any = False
    for hex_cid, new_width in pdf_widths.items():
        try:
            cid = int(hex_cid, 16)
        except ValueError:
            continue
        gid = _resolve_cff_cid_to_gid(topdict, cid)
        if gid is None:
            continue
        glyph_name = _glyph_name_for_gid(topdict, gid)
        if glyph_name is None:
            continue
        if _patch_single_cff_width(topdict, glyph_name, new_width):
            patched_any = True
    return patched_any


def _patch_single_metric(
    code: int,
    gname: str | None,
    hmtx: Any,
    pdf_widths: dict[str, float],
    scale: float,
    differences_map: dict[str, str] | None = None,
    base_encoding_map: dict[int, str] | None = None,
) -> bool:
    """Helper to patch the advance width of a single glyph based on its code mappings."""
    effective_code = _effective_cmap_code(code)
    hex_code = f"{effective_code:02X}"
    hex_code_4 = f"{effective_code:04X}"

    target_hex = None
    if hex_code in pdf_widths:
        target_hex = hex_code
    elif hex_code_4 in pdf_widths:
        target_hex = hex_code_4

    if target_hex is None:
        return False

    resolved_gname = _resolve_glyph_name(code, gname, differences_map, base_encoding_map)
    if resolved_gname is None:
        return False

    try:
        current_metric = hmtx[resolved_gname]
    except KeyError:
        # A /Differences or /BaseEncoding-resolved glyph name (or a code with no cmap entry)
        # may reference a glyph name that doesn't actually exist in this font's glyph set,
        # e.g. a mismatched PDF/font pairing. Skip patching that code rather than raising.
        logger.debug(
            "Glyph '%s' for code 0x%s not found in font; skipping metric patch.",
            resolved_gname,
            hex_code,
        )
        return False

    raw_w = pdf_widths[target_hex]
    hmtx[resolved_gname] = (int(round(raw_w * scale)), current_metric[1])
    return True


def _patch_single_cid_metric(
    cid: int,
    hmtx: Any,
    glyph_order: list,
    pdf_widths: dict[str, float],
    scale: float,
    cid_to_gid_map: dict[int, int] | str,
) -> bool:
    """
    Patches the advance width of a single CID-keyed glyph, resolved via the
    CIDFontType2 /CIDToGIDMap (ISO 32000-2 9.7.4.3) rather than the font's
    own Unicode cmap.
    """
    hex_cid = f"{cid:04X}"
    if hex_cid not in pdf_widths:
        return False

    gid = cid if cid_to_gid_map == "Identity" else cid_to_gid_map.get(cid, 0)
    if gid == 0 and cid_to_gid_map != "Identity":
        # Per spec, a CID absent from an explicit /CIDToGIDMap table maps
        # to GID 0 (.notdef) -- there is no real glyph here to patch. Under Identity,
        # GID 0 is a legitimate, addressable glyph (conventionally .notdef itself),
        # so this skip only applies to the explicit-mapping case.
        return False

    gname = _resolve_cid_glyph_name(glyph_order, gid)
    if gname is None:
        logger.debug(
            "CID %s resolved to GID %s, outside the font's glyph set; skipping metric patch.",
            hex_cid,
            gid,
        )
        return False

    try:
        current_metric = hmtx[gname]
    except KeyError:
        logger.debug(
            "Glyph '%s' for CID %s (GID %s) not found in font; skipping metric patch.",
            gname,
            hex_cid,
            gid,
        )
        return False

    raw_w = pdf_widths[hex_cid]
    hmtx[gname] = (int(round(raw_w * scale)), current_metric[1])
    return True


def _patch_cid_metrics(
    tt: Any,
    hmtx: Any,
    pdf_widths: dict[str, float],
    scale: float,
    cid_to_gid_map: dict[int, int] | str,
) -> bool:
    """Patches advance widths for a CID-keyed (Type0/CIDFontType2) font."""
    glyph_order = tt.getGlyphOrder()
    patched_any = False

    if cid_to_gid_map == "cff_native":
        # A CIDFontType0C (CFF-based) CIDFont has no /CIDToGIDMap at all
        # (ISO 32000-2 Table 115 restricts it to Type 2 CIDFonts) --
        # resolve CID -> GID via the CFF's own ROS/charset mechanism
        # instead, matching cff_binary_utils._patch_cid_widths.
        if "CFF " not in tt:
            return False
        from pdftl.fonts.cff_binary_utils import _resolve_cff_cid_to_gid

        cff = tt["CFF "].cff
        topdict = cff[cff.fontNames[0]]
        for hex_cid in pdf_widths:
            try:
                cid = int(hex_cid, 16)
            except ValueError:
                continue
            gid = _resolve_cff_cid_to_gid(topdict, cid)
            if gid is None:
                continue
            gname = _resolve_cid_glyph_name(glyph_order, gid)
            if gname is None:
                continue
            try:
                current_metric = hmtx[gname]
            except KeyError:
                continue
            hmtx[gname] = (int(round(pdf_widths[hex_cid] * scale)), current_metric[1])
            patched_any = True
        return patched_any

    for hex_cid in pdf_widths:
        try:
            cid = int(hex_cid, 16)
        except ValueError:
            continue
        if _patch_single_cid_metric(cid, hmtx, glyph_order, pdf_widths, scale, cid_to_gid_map):
            patched_any = True
    return patched_any


def _get_initial_data(filepath, TTFont):
    tt = TTFont(filepath)
    cmap = _get_best_cmap(tt) or {}
    hmtx = tt["hmtx"]
    units_per_em = tt["head"].unitsPerEm
    return tt, cmap, hmtx, units_per_em


def _patch_internal(
    filepath, pdf_widths, differences, base_encoding, cid_to_gid_map, TTFont, TTLibError
):
    tt, cmap, hmtx, units_per_em = _get_initial_data(filepath, TTFont)
    if not units_per_em:
        logger.warning(
            "Font in %s has missing or invalid unitsPerEm (0). Aborting metric patch.",
            filepath.name,
        )
        return None

    scale = units_per_em / 1000.0

    if cid_to_gid_map is not None:
        patched_any = _patch_cid_metrics(tt, hmtx, pdf_widths, scale, cid_to_gid_map)
    else:
        differences_map, base_encoding_map = _get_maps(differences, base_encoding)
        patched_any = False
        for code in _collect_target_codes(cmap, differences_map, base_encoding_map):
            gname = cmap.get(code)
            if _patch_single_metric(
                code, gname, hmtx, pdf_widths, scale, differences_map, base_encoding_map
            ):
                patched_any = True

    if not patched_any:
        return None

    stream = BytesIO()
    tt.save(stream)
    logger.info("Successfully patched advance widths in memory for %s", filepath.name)
    return stream.getvalue()


def patch_font_file_metrics_via_ttfont(
    filepath: Path,
    pdf_widths: dict[str, float],
    differences: list | None = None,
    base_encoding: str | None = None,
    cid_to_gid_map: dict[int, int] | str | None = None,
) -> bytes | None:
    """The sfnt-only (TTFont-based) metric-patching logic; dispatched to by
    pdftl.fonts.font_binary_utils.patch_font_file_metrics."""
    try:
        from fontTools.ttLib import TTFont, TTLibError
    except ImportError:
        return None

    try:
        return _patch_internal(
            filepath, pdf_widths, differences, base_encoding, cid_to_gid_map, TTFont, TTLibError
        )
    except (
        OSError,
        ValueError,
        KeyError,
        AttributeError,
        TypeError,
        TTLibError,
    ) as e:
        logger.warning("Failed to patch font file metrics for %s: %s", filepath.name, e)
        return None


def _squash_single_glyph(glyph: Any, ratio: float) -> None:
    """Applies horizontal scale transformations directly to TrueType glyph structures."""
    if hasattr(glyph, "coordinates"):
        coords = glyph.coordinates
        for idx in range(len(coords)):
            coords[idx] = (coords[idx][0] * ratio, coords[idx][1])
    elif hasattr(glyph, "components"):
        for comp in glyph.components:
            comp.transform = comp.transform.scale(ratio, 1.0)


def _process_glyph_squash(
    code: int,
    gname: str | None,
    hmtx: Any,
    glyf: Any,
    pdf_widths: dict[str, float],
    units_per_em: float,
    scale: float,
    differences_map: dict[str, str] | None = None,
    base_encoding_map: dict[int, str] | None = None,
) -> bool:
    """Performs bounds check, ratio check, visual transformation, and updates metrics."""
    effective_code = _effective_cmap_code(code)
    hex_code = f"{effective_code:02X}"
    target_hex = hex_code if hex_code in pdf_widths else f"{effective_code:04X}"
    if target_hex not in pdf_widths:
        return False

    resolved_gname = _resolve_glyph_name(code, gname, differences_map, base_encoding_map)
    if resolved_gname is None:
        return False

    try:
        font_metric = hmtx[resolved_gname]
        glyph = glyf[resolved_gname]
    except KeyError:
        # A /Differences or /BaseEncoding-resolved glyph name (or a code with no cmap entry)
        # may reference a glyph name absent from this font's hmtx/glyf tables.
        # Skip squashing that code rather than raising.
        logger.debug(
            "Glyph '%s' for code 0x%s not found in font; skipping vector squash.",
            resolved_gname,
            hex_code,
        )
        return False

    pdf_w = pdf_widths[target_hex]
    font_w = font_metric[0] * (1000.0 / units_per_em)

    if font_w <= 0 or pdf_w <= 0:
        return False

    ratio = pdf_w / font_w
    if abs(ratio - 1.0) < 1e-3:
        return False

    _squash_single_glyph(glyph, ratio)
    hmtx[resolved_gname] = (int(round(pdf_w * scale)), font_metric[1])
    return True


def _process_cid_glyph_squash(
    cid: int,
    hmtx: Any,
    glyf: Any,
    glyph_order: list,
    pdf_widths: dict[str, float],
    units_per_em: float,
    scale: float,
    cid_to_gid_map: dict[int, int] | str,
) -> bool:
    """
    Performs bounds check, ratio check, visual transformation, and
    updates metrics for a single CID-keyed glyph resolved via
    /CIDToGIDMap."""
    hex_cid = f"{cid:04X}"
    if hex_cid not in pdf_widths:
        return False

    gid = cid if cid_to_gid_map == "Identity" else cid_to_gid_map.get(cid, 0)
    if gid == 0 and cid_to_gid_map != "Identity":
        return False

    gname = _resolve_cid_glyph_name(glyph_order, gid)
    if gname is None:
        logger.debug(
            "CID %s resolved to GID %s, outside the font's glyph set; skipping vector squash.",
            hex_cid,
            gid,
        )
        return False

    try:
        font_metric = hmtx[gname]
        glyph = glyf[gname]
    except KeyError:
        logger.debug(
            "Glyph '%s' for CID %s (GID %s) not found in font; skipping vector squash.",
            gname,
            hex_cid,
            gid,
        )
        return False

    pdf_w = pdf_widths[hex_cid]
    font_w = font_metric[0] * (1000.0 / units_per_em)

    if font_w <= 0 or pdf_w <= 0:
        return False

    ratio = pdf_w / font_w
    if abs(ratio - 1.0) < 1e-3:
        return False

    _squash_single_glyph(glyph, ratio)
    hmtx[gname] = (int(round(pdf_w * scale)), font_metric[1])
    return True


def _squash_cid_glyphs(
    tt: Any,
    hmtx: Any,
    glyf: Any,
    pdf_widths: dict[str, float],
    units_per_em: float,
    scale: float,
    cid_to_gid_map: dict[int, int] | str,
) -> bool:
    """Squashes glyph vectors for a CID-keyed (Type0/CIDFontType2) font."""
    glyph_order = tt.getGlyphOrder()
    squashed_any = False
    for hex_cid in pdf_widths:
        try:
            cid = int(hex_cid, 16)
        except ValueError:
            continue
        if _process_cid_glyph_squash(
            cid, hmtx, glyf, glyph_order, pdf_widths, units_per_em, scale, cid_to_gid_map
        ):
            squashed_any = True
    return squashed_any


def _squash_internal(
    filepath, pdf_widths, differences, base_encoding, cid_to_gid_map, TTFont, TTLibError
):
    tt, cmap, hmtx, units_per_em = _get_initial_data(filepath, TTFont)
    if not units_per_em:
        logger.warning(
            "Font in %s has missing or invalid unitsPerEm (0). Aborting vector squashing.",
            filepath.name,
        )
        return None

    scale = units_per_em / 1000.0

    if "glyf" not in tt:
        # An sfnt-wrapped CFF or CFF2 program (OpenType/CFF, OpenType/CFF2)
        # has no flat, directly-scalable outline table -- the same
        # structural limitation bare_cff/type1 have (see
        # squash_font_file_vectors's docstring in font_binary_utils.py).
        # Degrade to a metrics-only patch rather than silently dropping
        # the requested edit: the font program's advance width still ends
        # up matching /Widths or /W, even though its outline is left
        # visually unscaled.
        logger.info(
            "Font in %s has no 'glyf' table (OpenType/CFF or CFF2); "
            "patching its advance-width metrics only instead of "
            "visually rescaling glyph outlines.",
            filepath.name,
        )
        patched_metrics = _patch_internal(
            filepath, pdf_widths, differences, base_encoding, cid_to_gid_map, TTFont, TTLibError
        )
        if patched_metrics is None:
            return None

        tt_for_cff_patch = TTFont(BytesIO(patched_metrics))
        if _patch_cff_table_in_sfnt(
            tt_for_cff_patch, pdf_widths, differences, base_encoding, cid_to_gid_map
        ):
            stream = BytesIO()
            tt_for_cff_patch.save(stream)
            return stream.getvalue()
        return patched_metrics

    glyf = tt["glyf"]

    if cid_to_gid_map is not None:
        squashed_any = _squash_cid_glyphs(
            tt, hmtx, glyf, pdf_widths, units_per_em, scale, cid_to_gid_map
        )
    else:
        differences_map, base_encoding_map = _get_maps(differences, base_encoding)
        squashed_any = False
        for code in _collect_target_codes(cmap, differences_map, base_encoding_map):
            gname = cmap.get(code)
            if _process_glyph_squash(
                code,
                gname,
                hmtx,
                glyf,
                pdf_widths,
                units_per_em,
                scale,
                differences_map,
                base_encoding_map,
            ):
                squashed_any = True

    if not squashed_any:
        return None

    stream = BytesIO()
    tt.save(stream)
    logger.info("Successfully squashed glyph vectors in memory for %s.", filepath.name)
    return stream.getvalue()


def squash_font_file_vectors_via_ttfont(
    filepath: Path,
    pdf_widths: dict[str, float],
    differences: list | None = None,
    base_encoding: str | None = None,
    cid_to_gid_map: dict[int, int] | str | None = None,
) -> bytes | None:
    """The sfnt/TrueType-only (glyf-based) vector-squash logic; dispatched
    to by pdftl.fonts.font_binary_utils.squash_font_file_vectors.

    Note this can never succeed for a CFF-flavored sfnt (OpenType/CFF or
    OpenType/CFF2 has no `glyf` table at all) -- `_squash_internal`'s own
    `"glyf" not in tt` guard covers that, returning None rather than
    raising, the same way it always has.
    """
    try:
        from fontTools.ttLib import TTFont, TTLibError
    except ImportError:
        return None

    try:
        return _squash_internal(
            filepath, pdf_widths, differences, base_encoding, cid_to_gid_map, TTFont, TTLibError
        )
    except (
        OSError,
        ValueError,
        KeyError,
        AttributeError,
        TypeError,
        TTLibError,
    ) as e:
        logger.warning("Failed to squash font vectors for %s: %s", filepath.name, e)
        return None
