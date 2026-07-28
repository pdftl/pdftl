# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/fonts/font_encoding_tables.py

"""
Pure PDF character-code -> glyph-name resolution helpers, shared by the
sfnt (TrueType/OpenType-CFF), bare-CFF, and Type 1 metric/vector-sync code
paths.

Split out of font_binary_utils.py: nothing here touches
fontTools.ttLib.TTFont or any actual font *program* bytes at all -- this
module only knows how to turn a PDF character code into the glyph name it
should map to, given a /Differences array and/or a /BaseEncoding name. That
makes it independently testable (and reusable by a bare-CFF or Type 1 path,
should one ever need the same /Differences/BaseEncoding priority chain)
without pulling in any sfnt-specific machinery.

Glyph resolution normally trusts the font's own internal cmap table (Unicode
code point -> glyph name), treating a PDF character code as if it were
directly a Unicode code point. That heuristic only holds for plain ASCII;
for other codes it depends on which encoding the PDF actually declared.
`_resolve_glyph_name` resolves the true glyph name for a PDF character code
in priority order:

  1. A Simple font's /Encoding /Differences array (an explicit, per-code
     override -- see `_expand_differences_map` below).
  2. A Simple font's /Encoding /BaseEncoding name (WinAnsiEncoding,
     MacRomanEncoding, or StandardEncoding -- see `_get_base_encoding_table`
     below), resolved via the Adobe Glyph List so the correct glyph is
     targeted even when it doesn't share a code point with the font's own
     Unicode cmap.
  3. The font's own cmap-derived glyph name, as a last-resort fallback for
     codes not covered by either of the above (e.g. symbolic fonts with no
     meaningful /Encoding).

Using the wrong source here can silently patch or squash the wrong glyph.
"""

from __future__ import annotations

import logging
from functools import cache

logger = logging.getLogger(__name__)


def _expand_differences_map(differences: list) -> dict[str, str]:
    """
    Expands a raw PDF /Differences array, as serialized in the export manifest
    (e.g. `[1, "/A", 2, "/B"]`), into a flat hex-code -> glyph-name map.

    A code entry (int) sets the current code; every glyph-name entry that
    follows is assigned to the current code and implicitly increments it,
    per the /Differences array format (ISO 32000-2 §9.6.6.2).
    """
    result: dict[str, str] = {}
    current_code: int | None = None
    for item in differences:
        if isinstance(item, bool):
            # Explanatory comment: bool is a subclass of int in Python, but PDF
            # code entries are never booleans. Skip defensively rather than
            # misinterpreting it as a code; this path shouldn't occur with a
            # manifest produced by _extract_differences_list.
            continue
        if isinstance(item, int):
            current_code = item
            continue
        if current_code is None:
            # Explanatory comment: a glyph name appearing before any code entry
            # indicates a malformed /Differences array. Skip it rather than guessing.
            continue
        # str(item) covers both a plain Python str (as in a manifest-derived
        # /Differences list) and a pikepdf.Name (as read live off a PDF,
        # e.g. pikepdf.Name("/A")) -- the two representations this array
        # can arrive in. Critically, pikepdf.Name is NOT a str subclass, so
        # an isinstance(item, str) branch here would silently miss it and
        # leave the leading "/" in every resolved glyph name (a pikepdf.Name's
        # own __str__ includes it), which then fails to match any real glyph
        # name in the font and makes /Differences-based resolution a no-op.
        gname = str(item).lstrip("/")
        result[f"{current_code:02X}"] = gname
        current_code += 1
    return result


def _build_codec_based_encoding_table(codec_name: str) -> dict[int, str]:
    """
    Builds a code -> Adobe Glyph List name table for a single-byte encoding by
    decoding each byte 0-255 through a stdlib codec to its Unicode code point,
    then looking that code point up in the Adobe Glyph List (AGL). This is the
    standard technique for deriving a glyph-name table from a Unicode-based
    single-byte encoding; codes the codec has no mapping for are simply absent
    from the result.
    """
    from fontTools import agl

    table: dict[int, str] = {}
    for code in range(256):
        try:
            char = bytes([code]).decode(codec_name)
        except UnicodeDecodeError:
            continue
        name = agl.UV2AGL.get(ord(char))
        if name:
            table[code] = name
    return table


# The WinAnsiEncoding code -> glyph-name table, indexed directly by code
# (None where WinAnsiEncoding leaves a code undefined). Transcribed
# mechanically from reportlab.pdfbase._fontdata_enc_winansi.WinAnsiEncoding
# (a mature, independent PDF library whose own output correctness depends
# on this table being right) rather than derived via a codec + Adobe Glyph
# List round-trip: that heuristic silently drops any codepoint absent from
# fontTools' bundled AGLFN reverse map, which turned out to include several
# codes WinAnsiEncoding legitimately defines -- 0xA0 (nbspace -> "space"),
# 0xAD (softhyphen -> "hyphen"), and 0xB2/0xB3/0xB9 (the superscript
# digits) all silently vanished under the old approach. This table already
# bakes in WinAnsiEncoding's own "bullet" fallback for codes with no other
# assigned glyph (0x81, 0x8D, 0x8F, 0x90, 0x9D), so no separate fallback
# step is needed on top of it.
_WINANSI_ENCODING_NAMES: tuple[str | None, ...] = (
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    "space",
    "exclam",
    "quotedbl",
    "numbersign",
    "dollar",
    "percent",
    "ampersand",
    "quotesingle",
    "parenleft",
    "parenright",
    "asterisk",
    "plus",
    "comma",
    "hyphen",
    "period",
    "slash",
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "colon",
    "semicolon",
    "less",
    "equal",
    "greater",
    "question",
    "at",
    "A",
    "B",
    "C",
    "D",
    "E",
    "F",
    "G",
    "H",
    "I",
    "J",
    "K",
    "L",
    "M",
    "N",
    "O",
    "P",
    "Q",
    "R",
    "S",
    "T",
    "U",
    "V",
    "W",
    "X",
    "Y",
    "Z",
    "bracketleft",
    "backslash",
    "bracketright",
    "asciicircum",
    "underscore",
    "grave",
    "a",
    "b",
    "c",
    "d",
    "e",
    "f",
    "g",
    "h",
    "i",
    "j",
    "k",
    "l",
    "m",
    "n",
    "o",
    "p",
    "q",
    "r",
    "s",
    "t",
    "u",
    "v",
    "w",
    "x",
    "y",
    "z",
    "braceleft",
    "bar",
    "braceright",
    "asciitilde",
    "bullet",
    "Euro",
    "bullet",
    "quotesinglbase",
    "florin",
    "quotedblbase",
    "ellipsis",
    "dagger",
    "daggerdbl",
    "circumflex",
    "perthousand",
    "Scaron",
    "guilsinglleft",
    "OE",
    "bullet",
    "Zcaron",
    "bullet",
    "bullet",
    "quoteleft",
    "quoteright",
    "quotedblleft",
    "quotedblright",
    "bullet",
    "endash",
    "emdash",
    "tilde",
    "trademark",
    "scaron",
    "guilsinglright",
    "oe",
    "bullet",
    "zcaron",
    "Ydieresis",
    "space",
    "exclamdown",
    "cent",
    "sterling",
    "currency",
    "yen",
    "brokenbar",
    "section",
    "dieresis",
    "copyright",
    "ordfeminine",
    "guillemotleft",
    "logicalnot",
    "hyphen",
    "registered",
    "macron",
    "degree",
    "plusminus",
    "twosuperior",
    "threesuperior",
    "acute",
    "mu",
    "paragraph",
    "periodcentered",
    "cedilla",
    "onesuperior",
    "ordmasculine",
    "guillemotright",
    "onequarter",
    "onehalf",
    "threequarters",
    "questiondown",
    "Agrave",
    "Aacute",
    "Acircumflex",
    "Atilde",
    "Adieresis",
    "Aring",
    "AE",
    "Ccedilla",
    "Egrave",
    "Eacute",
    "Ecircumflex",
    "Edieresis",
    "Igrave",
    "Iacute",
    "Icircumflex",
    "Idieresis",
    "Eth",
    "Ntilde",
    "Ograve",
    "Oacute",
    "Ocircumflex",
    "Otilde",
    "Odieresis",
    "multiply",
    "Oslash",
    "Ugrave",
    "Uacute",
    "Ucircumflex",
    "Udieresis",
    "Yacute",
    "Thorn",
    "germandbls",
    "agrave",
    "aacute",
    "acircumflex",
    "atilde",
    "adieresis",
    "aring",
    "ae",
    "ccedilla",
    "egrave",
    "eacute",
    "ecircumflex",
    "edieresis",
    "igrave",
    "iacute",
    "icircumflex",
    "idieresis",
    "eth",
    "ntilde",
    "ograve",
    "oacute",
    "ocircumflex",
    "otilde",
    "odieresis",
    "divide",
    "oslash",
    "ugrave",
    "uacute",
    "ucircumflex",
    "udieresis",
    "yacute",
    "thorn",
    "ydieresis",
)


def _build_winansi_encoding_table() -> dict[int, str]:
    """Builds the WinAnsiEncoding code -> glyph-name table directly from
    _WINANSI_ENCODING_NAMES, matching _build_standard_encoding_table's and
    _build_macroman_encoding_table's pattern -- no codec round-trip or AGL
    reverse lookup involved."""
    return {
        code: name
        for code, name in enumerate(_WINANSI_ENCODING_NAMES)
        if name and name != ".notdef"
    }


def _build_macroman_encoding_table() -> dict[int, str]:
    """Builds the MacRomanEncoding code -> glyph-name table directly from
    fontTools' own canonical MacRoman data, matching
    _build_standard_encoding_table's pattern -- no codec round-trip or AGL
    reverse lookup involved, so there's no possibility of a codepoint
    silently falling through an incomplete reverse-mapping table (see
    the 'fi'/'fl' ligature gap that affected the old codec-based
    approach: mac_roman codec correctly decodes 0xDE -> U+FB01, but
    fontTools.agl.UV2AGL has no entry for U+FB01 at all, since it's
    built from AGLFN, which deliberately excludes ligature glyphs)."""
    from fontTools.encodings.MacRoman import MacRoman

    return {code: name for code, name in enumerate(MacRoman) if name and name != ".notdef"}


def _build_standard_encoding_table() -> dict[int, str]:
    """Builds the (Adobe) StandardEncoding code -> glyph-name table directly
    from fontTools' own canonical StandardEncoding data."""
    from fontTools.encodings.StandardEncoding import StandardEncoding

    return {code: name for code, name in enumerate(StandardEncoding) if name and name != ".notdef"}


# The MacExpertEncoding code -> glyph-name table, indexed directly by code
# (None where MacExpertEncoding leaves a code undefined). Transcribed
# mechanically from reportlab.pdfbase._fontdata_enc_macexpert.MacExpertEncoding,
# same provenance and rationale as _WINANSI_ENCODING_NAMES above.
# MacExpertEncoding is the fourth named PDF /BaseEncoding (ISO 32000-2
# Annex D) -- for classic Type 1 "expert set" fonts (old-style figures,
# small caps, superior/inferior figures, fractions). Rare in modern PDFs
# but part of the spec, so it's implemented on the same footing as the
# other three rather than left to silently fall through to the font's
# own cmap.
_MACEXPERT_ENCODING_NAMES: tuple[str | None, ...] = (
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    None,
    "space",
    "exclamsmall",
    "Hungarumlautsmall",
    "centoldstyle",
    "dollaroldstyle",
    "dollarsuperior",
    "ampersandsmall",
    "Acutesmall",
    "parenleftsuperior",
    "parenrightsuperior",
    "twodotenleader",
    "onedotenleader",
    "comma",
    "hyphen",
    "period",
    "fraction",
    "zerooldstyle",
    "oneoldstyle",
    "twooldstyle",
    "threeoldstyle",
    "fouroldstyle",
    "fiveoldstyle",
    "sixoldstyle",
    "sevenoldstyle",
    "eightoldstyle",
    "nineoldstyle",
    "colon",
    "semicolon",
    None,
    "threequartersemdash",
    None,
    "questionsmall",
    None,
    None,
    None,
    None,
    "Ethsmall",
    None,
    None,
    "onequarter",
    "onehalf",
    "threequarters",
    "oneeighth",
    "threeeighths",
    "fiveeighths",
    "seveneighths",
    "onethird",
    "twothirds",
    None,
    None,
    None,
    None,
    None,
    None,
    "ff",
    "fi",
    "fl",
    "ffi",
    "ffl",
    "parenleftinferior",
    None,
    "parenrightinferior",
    "Circumflexsmall",
    "hypheninferior",
    "Gravesmall",
    "Asmall",
    "Bsmall",
    "Csmall",
    "Dsmall",
    "Esmall",
    "Fsmall",
    "Gsmall",
    "Hsmall",
    "Ismall",
    "Jsmall",
    "Ksmall",
    "Lsmall",
    "Msmall",
    "Nsmall",
    "Osmall",
    "Psmall",
    "Qsmall",
    "Rsmall",
    "Ssmall",
    "Tsmall",
    "Usmall",
    "Vsmall",
    "Wsmall",
    "Xsmall",
    "Ysmall",
    "Zsmall",
    "colonmonetary",
    "onefitted",
    "rupiah",
    "Tildesmall",
    None,
    None,
    "asuperior",
    "centsuperior",
    None,
    None,
    None,
    None,
    "Aacutesmall",
    "Agravesmall",
    "Acircumflexsmall",
    "Adieresissmall",
    "Atildesmall",
    "Aringsmall",
    "Ccedillasmall",
    "Eacutesmall",
    "Egravesmall",
    "Ecircumflexsmall",
    "Edieresissmall",
    "Iacutesmall",
    "Igravesmall",
    "Icircumflexsmall",
    "Idieresissmall",
    "Ntildesmall",
    "Oacutesmall",
    "Ogravesmall",
    "Ocircumflexsmall",
    "Odieresissmall",
    "Otildesmall",
    "Uacutesmall",
    "Ugravesmall",
    "Ucircumflexsmall",
    "Udieresissmall",
    None,
    "eightsuperior",
    "fourinferior",
    "threeinferior",
    "sixinferior",
    "eightinferior",
    "seveninferior",
    "Scaronsmall",
    None,
    "centinferior",
    "twoinferior",
    None,
    "Dieresissmall",
    None,
    "Caronsmall",
    "osuperior",
    "fiveinferior",
    None,
    "commainferior",
    "periodinferior",
    "Yacutesmall",
    None,
    "dollarinferior",
    None,
    None,
    "Thornsmall",
    None,
    "nineinferior",
    "zeroinferior",
    "Zcaronsmall",
    "AEsmall",
    "Oslashsmall",
    "questiondownsmall",
    "oneinferior",
    "Lslashsmall",
    None,
    None,
    None,
    None,
    None,
    None,
    "Cedillasmall",
    None,
    None,
    None,
    None,
    None,
    "OEsmall",
    "figuredash",
    "hyphensuperior",
    None,
    None,
    None,
    None,
    "exclamdownsmall",
    None,
    "Ydieresissmall",
    None,
    "onesuperior",
    "twosuperior",
    "threesuperior",
    "foursuperior",
    "fivesuperior",
    "sixsuperior",
    "sevensuperior",
    "ninesuperior",
    "zerosuperior",
    None,
    "esuperior",
    "rsuperior",
    "tsuperior",
    None,
    None,
    "isuperior",
    "ssuperior",
    "dsuperior",
    None,
    None,
    None,
    None,
    None,
    "lsuperior",
    "Ogoneksmall",
    "Brevesmall",
    "Macronsmall",
    "bsuperior",
    "nsuperior",
    "msuperior",
    "commasuperior",
    "periodsuperior",
    "Dotaccentsmall",
    "Ringsmall",
    None,
    None,
    None,
    None,
)


def _build_macexpert_encoding_table() -> dict[int, str]:
    """Builds the MacExpertEncoding code -> glyph-name table directly from
    _MACEXPERT_ENCODING_NAMES, same pattern as WinAnsi/MacRoman/Standard."""
    return {
        code: name
        for code, name in enumerate(_MACEXPERT_ENCODING_NAMES)
        if name and name != ".notdef"
    }


_BASE_ENCODING_BUILDERS = {
    "WinAnsiEncoding": _build_winansi_encoding_table,
    "MacRomanEncoding": _build_macroman_encoding_table,
    "StandardEncoding": _build_standard_encoding_table,
    "MacExpertEncoding": _build_macexpert_encoding_table,
}


@cache
def _get_base_encoding_table(base_encoding: str) -> dict[int, str]:
    """
    Returns the code -> glyph-name table for a named PDF /BaseEncoding
    (WinAnsiEncoding, MacRomanEncoding, or StandardEncoding). Unrecognized
    names return an empty table rather than raising, so an unusual or future
    /BaseEncoding value simply falls back to the font's own cmap resolution
    instead of aborting metric/vector synchronization for the whole font.
    Results are cached since these tables are static and code-independent.
    """
    builder = _BASE_ENCODING_BUILDERS.get(base_encoding)
    if builder is None:
        return {}
    try:
        return builder()
    except (ImportError, LookupError) as e:
        # Explanatory comment: LookupError covers an unavailable stdlib codec
        # (shouldn't happen for cp1252/mac_roman, but guarded defensively);
        # ImportError covers a fontTools submodule not being present. Either
        # way, an empty table simply disables base-encoding resolution.
        logger.debug("Failed to build base encoding table for '%s': %s", base_encoding, e)
        return {}


def _resolve_glyph_name(
    code: int,
    gname: str | None,
    differences_map: dict[str, str] | None,
    base_encoding_map: dict[int, str] | None = None,
) -> str | None:
    """
    Resolves the true glyph name for a PDF character code, in priority order:
    a /Differences override, then a /BaseEncoding table entry, then the
    font's own cmap-derived name. Returns None if no glyph name can be
    determined at all (code absent from every source).
    """
    if differences_map:
        hex_code = f"{code:02X}"
        if hex_code in differences_map:
            return differences_map[hex_code]
    if base_encoding_map and code in base_encoding_map:
        return base_encoding_map[code]
    return gname


def _collect_target_codes(
    cmap: dict,
    differences_map: dict[str, str] | None,
    base_encoding_map: dict[int, str] | None = None,
) -> set[int]:
    """Merges codes reachable via the font's own cmap with any codes only
    reachable through a /Differences override or a /BaseEncoding table, so
    glyphs that exist purely via those explicit mappings are not silently
    skipped."""
    codes = set(cmap.keys())
    if differences_map:
        codes |= {int(hex_code, 16) for hex_code in differences_map}
    if base_encoding_map:
        codes |= set(base_encoding_map.keys())
    return codes


def _get_maps(differences, base_encoding):
    """Convenience bundling of the two possible glyph-name resolution
    sources into the (differences_map, base_encoding_map) pair every
    sfnt-side patch/squash internal expects."""
    differences_map = _expand_differences_map(differences) if differences else None
    base_encoding_map = _get_base_encoding_table(base_encoding) if base_encoding else None
    return differences_map, base_encoding_map
