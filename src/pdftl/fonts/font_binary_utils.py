# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/fonts/font_binary_utils.py

"""
Format-agnostic dispatch layer for reading and mutating advance-width
metrics directly inside font binary files (.ttf/.otf/.cff/.pfb) using
fontTools.

These operations are performed purely in-memory and return modified byte
streams. Callers are responsible for reconciling the results with a PDF's
own /Widths or /W metrics and writing the bytes to the PDF (see
pdftl.fonts.widths_utils).

## Module split

This module used to contain all of the sfnt (TrueType/OpenType) reading,
patching, and squashing mechanics directly. That logic now lives in two
sibling modules, split along a "does this touch a real font program's
bytes" line:

  - pdftl.fonts.font_encoding_tables: pure PDF character-code -> glyph-name
    resolution (the /Differences / /BaseEncoding / cmap priority chain).
    Never touches fontTools.ttLib.TTFont or any font program bytes at all.
  - pdftl.fonts.font_binary_sfnt: the actual TTFont-based reading,
    metric-patching, and glyf-vector-squashing mechanics, for CID-keyed and
    Simple sfnt fonts alike.

This module re-imports the private helpers from both (see the imports
below) purely so existing call sites and tests that reference e.g.
`pdftl.fonts.font_binary_utils._resolve_glyph_name` keep working
unchanged; new code should generally import directly from whichever of the
three modules actually defines what it needs.

*This* module retains:
  - `classify_binary_format`, the format-string classifier described below.
  - The three public dispatch entry points (`get_font_widths_from_file`,
    `patch_font_file_metrics`, `squash_font_file_vectors`), which decide
    *which* underlying reader/patcher/squasher a given embedded font
    program needs and delegate to it.
  - `rekey_name_widths_to_hex_codes` / `rekey_hex_widths_to_names`, the
    Simple-font code<->name translation needed at the bare-CFF and Type 1
    dispatch points (see "Simple-font key-space translation" below).

## Binary format dispatch

Not every embedded font program is sfnt-wrapped (i.e. openable via
`fontTools.ttLib.TTFont`). A bare CFF program (`/FontFile3` with
`/Subtype /Type1C` or `/CIDFontType0C`) and a classic Type 1 program
(`/FontFile`, PFB) are never sfnt-wrapped, and `TTFont()` cannot open them
at all. `classify_binary_format` maps the `embedded_format` string already
recorded on export (see `font_export_helpers.py`'s `_get_font_suffix`) to
one of four buckets so the three public entry points below can dispatch to
the right underlying reader *before* attempting to open anything, rather
than reactively catching whatever exception `TTFont()` happens to raise
and treating every failure as indistinguishable from a genuine parse
error. All four buckets now have a real reader/patcher: `sfnt` via
`pdftl.fonts.font_binary_sfnt`, `bare_cff` via
`pdftl.fonts.cff_binary_utils`, and `type1` via
`pdftl.fonts.type1_binary_utils`. Only `unknown` (an unrecognized or
absent `embedded_format`) has no reader at all, since there is nothing
meaningful to dispatch to.

Vector squash (glyph contour scaling) has no true equivalent for
`bare_cff`/`type1`: neither format has a flat, directly-scalable outline
table the way `glyf` does (see FR-5 in notes/font_second_plan-v2.md). Since
these formats' own advance-width metric can still be corrected even when
the outline itself can't be visually rescaled, `squash_font_file_vectors`
degrades to a metrics-only patch for these two formats rather than doing
nothing at all -- see its docstring below for the full rationale.

## Simple-font key-space translation

A Simple font's `pdf_widths`/`widths_map` dict is always keyed by 2-digit
hex PDF character code (`"41"`) at every call site in this codebase
(`font_import_helpers.py`'s sidecar mappings, `update_font_widths`, etc.).
The sfnt path never needs a translation step because
`font_binary_sfnt.py`'s Simple-font functions resolve a glyph *by that
same code*, via /Differences, /BaseEncoding, or the font's own cmap. Bare
CFF and Type 1 have no such per-code resolution mechanism at all -- their
own `get_widths_from_cff`/`get_widths_from_type1` and
`patch_cff_widths`/`patch_type1_widths` are keyed directly by the font's
own **glyph name** (see their docstrings), since neither format carries
anything resembling a PDF-code-indexed table internally.

That means every bare-CFF or Type 1 Simple-font call site here must
translate between the two key spaces using exactly the same
/Differences-then-/BaseEncoding priority chain the sfnt path applies via
`_resolve_glyph_name` -- just in the opposite direction (write: hex code ->
glyph name so the CFF/Type 1 patcher can find the right charstring; read:
glyph name -> hex code so a `{name: width}` map becomes the
`{"XX": width}` shape every other caller of `get_font_widths_from_file`
expects). `rekey_hex_widths_to_names` and `rekey_name_widths_to_hex_codes`
below are that shared translation, built from the same `_get_maps` table
construction `font_binary_sfnt.py` already uses.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

# Re-exported for backward compatibility: existing call sites and tests
# reference these as pdftl.fonts.font_binary_utils.<name>. See the module
# docstring's "Module split" section above for where each now actually
# lives. (Referencing them here also means `ruff`/linters see them as used
# via `__all__`-style re-export rather than flagging unused imports.)
from pdftl.fonts.font_binary_sfnt import (  # noqa: F401
    _get_cid_widths,
    _get_initial_data,
    _patch_cid_metrics,
    _patch_internal,
    _patch_single_cid_metric,
    _patch_single_metric,
    _process_cid_glyph_squash,
    _process_glyph_squash,
    _resolve_cid_glyph_name,
    _squash_cid_glyphs,
    _squash_internal,
    _squash_single_glyph,
    get_font_widths_via_ttfont as _get_font_widths_via_ttfont,
    patch_font_file_metrics_via_ttfont as _patch_font_file_metrics_via_ttfont,
    squash_font_file_vectors_via_ttfont as _squash_font_file_vectors_via_ttfont,
)
from pdftl.fonts.font_encoding_tables import (  # noqa: F401
    _BASE_ENCODING_BUILDERS,
    _build_macroman_encoding_table,
    _build_standard_encoding_table,
    _build_winansi_encoding_table,
    _collect_target_codes,
    _expand_differences_map,
    _get_base_encoding_table,
    _get_maps,
    _resolve_glyph_name,
)

logger = logging.getLogger(__name__)

# "cff2" here means OpenType-wrapped CFF2 (an sfnt container with a CFF2
# table), which TTFont() already opens fine -- see _sniff_is_cff2 in
# font_export_helpers.py. "cff" is bare CFF (Type1C/CIDFontType0C, no sfnt
# wrapper) -- NOT TTFont-compatible. "pfb" is classic Type 1 -- also NOT
# TTFont-compatible.
_SFNT_COMPATIBLE_FORMATS = frozenset({"ttf", "otf", "cff2"})
_BARE_CFF_FORMATS = frozenset({"cff"})
_TYPE1_FORMATS = frozenset({"pfb"})


def classify_binary_format(embedded_format: str | None) -> str:
    """
    Classifies an `embedded_format` string (as recorded in the export
    manifest's `font_entry["embedded_format"]`, e.g. "ttf", "otf", "cff",
    "cff2", "pfb") into one of four dispatch buckets:

      - "sfnt": openable via fontTools.ttLib.TTFont as-is (ttf/otf/cff2).
      - "bare_cff": a bare (non-sfnt-wrapped) CFF program (Type1C or
        CIDFontType0C); needs pdftl.fonts.cff_binary_utils, not TTFont.
      - "type1": a classic Type 1 (/FontFile, PFB) program; needs
        pdftl.fonts.type1_binary_utils, not TTFont.
      - "unknown": `embedded_format` is None, empty, or an unrecognized
        value (e.g. a future format this codebase doesn't know about yet,
        or a font with no embedded binary at all).

    This is a pure string classifier: it never opens or reads the actual
    font bytes, and never raises.
    """
    if embedded_format in _SFNT_COMPATIBLE_FORMATS:
        return "sfnt"
    if embedded_format in _BARE_CFF_FORMATS:
        return "bare_cff"
    if embedded_format in _TYPE1_FORMATS:
        return "type1"
    return "unknown"


def rekey_hex_widths_to_names(
    pdf_widths: dict[str, float],
    differences: list | None = None,
    base_encoding: str | None = None,
) -> dict[str, float]:
    """
    Translates a hex-PDF-code-keyed width map (`{"41": 500.0}`) into a
    glyph-name-keyed one (`{"A": 500.0}`), via the same /Differences-then-
    /BaseEncoding priority chain `_resolve_glyph_name` applies for the sfnt
    path -- see the module docstring's "Simple-font key-space translation"
    section for why bare CFF and Type 1 need this at all.

    A code with no /Differences or /BaseEncoding entry is silently
    dropped: unlike the sfnt path, there is no font-native cmap fallback
    to fall through to for bare CFF or Type 1 (neither format carries a
    PDF-code-indexed table at all), so a code this function can't resolve
    genuinely can't be translated to a glyph name by any means available
    here.
    """
    differences_map, base_encoding_map = _get_maps(differences, base_encoding)
    named_widths: dict[str, float] = {}
    for hex_code, width in pdf_widths.items():
        try:
            code = int(hex_code, 16)
        except ValueError:
            continue
        gname = _resolve_glyph_name(code, None, differences_map, base_encoding_map)
        if gname is None:
            continue
        named_widths[gname] = width
    return named_widths


def _coerce_code_to_int(code: Any) -> int | None:
    """
    Normalizes a single code value from `differences_map`/`base_encoding_map`
    (built by `_get_maps` in `font_encoding_tables.py`) to a plain int.

    Those tables' key type is that module's own implementation detail, not
    a contract this module should assume holds. A code that turns out to
    already be a hex-formatted *string* (e.g. "41") rather than an int
    (0x41) would otherwise reach `f"{code:02X}"` below and raise
    `ValueError: Unknown format code 'X' for object of type 'str'`,
    aborting width sync for every glyph on this font rather than just the
    one unresolvable entry. Coercing here (accepting an int already, or a
    base-16 string) keeps that failure mode local to a single
    skipped glyph.
    """
    if isinstance(code, int):
        return code
    if isinstance(code, str):
        try:
            return int(code, 16)
        except ValueError:
            return None
    return None


def rekey_name_widths_to_hex_codes(
    widths_by_name: dict[str, float],
    differences: list | None = None,
    base_encoding: str | None = None,
) -> dict[str, float]:
    """
    Translates a glyph-name-keyed width map (`{"A": 500.0}`, the shape
    `get_widths_from_cff`/`get_widths_from_type1` return) into a
    2-digit-hex-PDF-code-keyed one (`{"41": 500.0}`, the shape every other
    caller of `get_font_widths_from_file` -- and `update_font_widths` --
    expects), by inverting the same /Differences-then-/BaseEncoding tables
    `rekey_hex_widths_to_names` uses in the opposite direction.

    A glyph name with no corresponding /Differences or /BaseEncoding entry
    is silently dropped, for the same reason as `rekey_hex_widths_to_names`
    above: there is no font-native cmap to fall back to for these two
    formats. /Differences takes priority over /BaseEncoding when both map
    a code to the same glyph name (matching the read-side priority), by
    building the inverse name->code table from /BaseEncoding first and
    letting /Differences entries overwrite it.

    Code values pulled from either table are coerced defensively via
    `_coerce_code_to_int` before formatting as hex -- see that helper's
    docstring for why.
    """
    differences_map, base_encoding_map = _get_maps(differences, base_encoding)
    name_to_code: dict[str, int] = {}
    if base_encoding_map:
        for code, gname in base_encoding_map.items():
            int_code = _coerce_code_to_int(code)
            if int_code is not None:
                name_to_code[gname] = int_code
    if differences_map:
        for code, gname in differences_map.items():
            int_code = _coerce_code_to_int(code)
            if int_code is not None:
                name_to_code[gname] = int_code

    hex_widths: dict[str, float] = {}
    for gname, width in widths_by_name.items():
        code = name_to_code.get(gname)
        if code is None:
            continue
        hex_widths[f"{code:02X}"] = width
    return hex_widths


def get_font_widths_from_file(
    filepath: Path,
    cid_to_gid_map: dict[int, int] | str | None = None,
    embedded_format: str | None = None,
    base_font: str = "",
) -> dict[str, float]:
    """
    Measures font widths of an embedded font file using fontTools.

    By default, widths are keyed by the font's own Unicode cmap code
    points (appropriate for Simple fonts). Pass `cid_to_gid_map` --
    either the string "Identity" or a flat {cid: gid} dict, matching
    pdftl.fonts.widths_utils.extract_cid_to_gid_map's return convention --
    to instead read CID-keyed widths for a Type0 (CID-keyed) font,
    resolved via /CIDToGIDMap rather than the font's own cmap. For a bare
    CID-keyed CFF (CIDFontType0C), pass `cid_to_gid_map="cff_native"`
    instead (see pdftl.fonts.cff_binary_utils).

    `embedded_format` (the manifest's `font_entry["embedded_format"]`, e.g.
    "ttf", "otf", "cff", "cff2", "pfb") selects which underlying reader is
    used, via `classify_binary_format`. Omitting it (the default) preserves
    prior behavior: always attempt to open via TTFont, matching every
    existing caller that predates this format-aware dispatch.

    For a bare Simple font (`embedded_format` "cff" or "pfb", with
    `cid_to_gid_map` omitted), widths are keyed by the font's own glyph
    name rather than by PDF code -- see the module docstring's
    "Simple-font key-space translation" section. Callers needing a
    hex-code-keyed result for such a font (e.g. to feed
    `update_font_widths`) must translate it themselves via
    `rekey_name_widths_to_hex_codes`, since only the caller has the
    /Differences and /BaseEncoding needed to do so. `base_font` is only
    used for logging and is otherwise unused here.
    """
    if embedded_format is None:
        return _get_font_widths_via_ttfont(filepath, cid_to_gid_map)

    fmt = classify_binary_format(embedded_format)
    if fmt == "sfnt":
        return _get_font_widths_via_ttfont(filepath, cid_to_gid_map)
    if fmt == "bare_cff":
        from pdftl.fonts import cff_binary_utils

        return cff_binary_utils.get_widths_from_cff(filepath, cid_to_gid_map=cid_to_gid_map)
    if fmt == "type1":
        from pdftl.fonts import type1_binary_utils

        return type1_binary_utils.get_widths_from_type1(filepath)

    logger.warning("Unrecognized font binary format '%s'; skipping.", embedded_format)
    return {}


def _patch_bare_cff_or_type1_metrics(
    fmt: str,
    filepath: Path,
    pdf_widths: dict[str, float],
    differences: list | None,
    base_encoding: str | None,
    cid_to_gid_map: dict[int, int] | str | None,
) -> bytes | None:
    """
    Dispatches a metric patch to `cff_binary_utils`/`type1_binary_utils`,
    translating `pdf_widths` from hex-PDF-code keys to glyph-name keys
    first for the Simple-font case (see the module docstring's
    "Simple-font key-space translation" section) -- CID-keyed CFF is
    already hex-CID-keyed on both sides and needs no translation.
    """
    if fmt == "bare_cff":
        from pdftl.fonts import cff_binary_utils

        if cid_to_gid_map is not None:
            return cff_binary_utils.patch_cff_widths(
                filepath, pdf_widths, cid_to_gid_map=cid_to_gid_map
            )
        name_keyed = rekey_hex_widths_to_names(pdf_widths, differences, base_encoding)
        return cff_binary_utils.patch_cff_widths(filepath, name_keyed, cid_to_gid_map=None)

    from pdftl.fonts import type1_binary_utils

    # Type 1 is never CID-keyed (ISO 32000-2 Table 121 restricts /FontFile
    # to non-composite Simple fonts), so this translation always applies.
    name_keyed = rekey_hex_widths_to_names(pdf_widths, differences, base_encoding)
    return type1_binary_utils.patch_type1_widths(filepath, name_keyed)


def patch_font_file_metrics(
    filepath: Path,
    pdf_widths: dict[str, float],
    differences: list | None = None,
    base_encoding: str | None = None,
    cid_to_gid_map: dict[int, int] | str | None = None,
    embedded_format: str | None = None,
    base_font: str = "",
) -> bytes | None:
    """Patches advance widths in a font file's own binary metrics table
    in-memory.

    For Simple fonts, `differences` (the raw /Differences array as
    serialized in the export manifest) and `base_encoding` (a
    /BaseEncoding name) take priority over the font's own cmap when
    resolving which glyph to patch for a given character code;
    /Differences takes priority over /BaseEncoding.

    For Type0 (CID-keyed) fonts, pass `cid_to_gid_map` instead -- either
    the string "Identity" or a flat {cid: gid} dict, matching
    pdftl.fonts.widths_utils.extract_cid_to_gid_map's return convention.
    When given, `pdf_widths` keys are interpreted as CIDs and resolved to
    real glyphs via /CIDToGIDMap rather than the font's own cmap.
    `differences`/`base_encoding` are ignored when `cid_to_gid_map` is
    given, since /Differences never applies to Type0 fonts. For a bare
    CID-keyed CFF (CIDFontType0C), pass `cid_to_gid_map="cff_native"`
    instead (see pdftl.fonts.cff_binary_utils).

    `embedded_format` selects which underlying patcher is used, via
    `classify_binary_format`. Omitting it (the default) preserves prior
    behavior: always attempt to patch via TTFont.

    For a bare Simple font (`embedded_format` "cff" or "pfb"), `pdf_widths`
    is translated from hex-PDF-code keys to the font's own glyph-name keys
    via `differences`/`base_encoding` before dispatching -- see the module
    docstring's "Simple-font key-space translation" section for why bare
    CFF and Type 1 need this translation at all.
    """
    if embedded_format is None:
        return _patch_font_file_metrics_via_ttfont(
            filepath, pdf_widths, differences, base_encoding, cid_to_gid_map
        )

    fmt = classify_binary_format(embedded_format)
    if fmt == "sfnt":
        return _patch_font_file_metrics_via_ttfont(
            filepath, pdf_widths, differences, base_encoding, cid_to_gid_map
        )
    if fmt in ("bare_cff", "type1"):
        return _patch_bare_cff_or_type1_metrics(
            fmt, filepath, pdf_widths, differences, base_encoding, cid_to_gid_map
        )

    logger.warning("Unrecognized font binary format '%s'; skipping.", embedded_format)
    return None


def squash_font_file_vectors(
    filepath: Path,
    pdf_widths: dict[str, float],
    differences: list | None = None,
    base_encoding: str | None = None,
    cid_to_gid_map: dict[int, int] | str | None = None,
    embedded_format: str | None = None,
    base_font: str = "",
) -> bytes | None:
    """Transforms glyph contours in-memory to visually fit PDF metrics.

    For Simple fonts, `differences` and `base_encoding` behave as in
    `patch_font_file_metrics`. For Type0 fonts, pass `cid_to_gid_map`
    instead -- see `patch_font_file_metrics` for the full contract.

    True vector squash (glyph contour deformation) is structurally only
    possible for TrueType (`glyf`-based) outlines: a CFF-flavored program
    (bare or sfnt-wrapped) has no `glyf` table to deform at all, and a
    Type 1 program's charstrings have the same interpret/re-emit problem
    as CFF, compounded by eexec encryption -- see FR-5 in
    notes/font_second_plan-v2.md. Rather than dropping the requested edit
    entirely for these two formats, `embedded_format` classified as
    "bare_cff" or "type1" degrades to a **metrics-only** patch -- the same
    dispatch `patch_font_file_metrics` uses -- so the embedded font
    program's own advance width still matches /Widths or /W, even though
    its outline is left visually unscaled. This is a permanent, documented
    scope decision, not a placeholder: a metrics-mismatch (font program
    and /Widths disagreeing) is a worse fidelity failure than an
    unsquashed outline, since PDF renderers use /Widths for layout, not
    the font program's own metrics.

    An sfnt-wrapped CFF/CFF2 program (classified "sfnt") is dispatched to
    `_squash_font_file_vectors_via_ttfont` the same as TrueType, but that
    function's own `"glyf" not in tt` guard (in
    pdftl.fonts.font_binary_sfnt._squash_internal) still catches it and
    returns None -- OpenType/CFF and CFF2 have no `glyf` table either, they
    just aren't detectable from the `embedded_format` string alone the way
    bare CFF is, and there is no metrics-only fallback for that specific
    case since it is handled entirely inside the sfnt path.

    Regardless of `embedded_format`, every sfnt-classified (or
    unspecified/legacy-caller) path funnels through the same
    `_squash_font_file_vectors_via_ttfont` helper -- there is exactly one
    sfnt vector-squash code path, not two independently-maintained copies
    of the same try/except/import logic.
    """
    if embedded_format is not None:
        fmt = classify_binary_format(embedded_format)
        if fmt in ("bare_cff", "type1"):
            logger.info(
                "Vector squash is not supported for CFF/Type1 font programs "
                "(font '%s' uses charstring-based outlines, not a flat glyf "
                "table); patching the embedded font's own advance-width "
                "metric instead of visually rescaling its outline.",
                base_font,
            )
            return _patch_bare_cff_or_type1_metrics(
                fmt, filepath, pdf_widths, differences, base_encoding, cid_to_gid_map
            )
        if fmt == "unknown":
            logger.warning("Unrecognized font binary format '%s'; skipping.", embedded_format)
            return None

    return _squash_font_file_vectors_via_ttfont(
        filepath, pdf_widths, differences, base_encoding, cid_to_gid_map
    )
