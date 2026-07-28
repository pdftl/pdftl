# src/pdftl/fonts/type1_to_cff.py

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
Converts a classic Type 1 (/FontFile) font program into a subsetted CFF
(Type 1C) byte stream, keeping only the glyphs reachable from a given set
of PDF character codes plus .notdef.

fontTools.subset has no Type 1 support at all (see font_subsetting.py's
module docstring), so a Type 1 program can't be handed to the ordinary
subsetter the way an sfnt or bare-CFF program can. Instead this module:
parses the program via fontTools' own T1Font, resolves the requested
codes to glyph names, redraws each surviving glyph's outline as a CFF
(Type 2) charstring via a pen, and assembles a fresh CFF table via
FontBuilder.

This deliberately reuses the exact fontTools.t1Lib.T1Font(path,
kind="OTHER").parse() pattern already proven against real embedded Type
1 programs by pdftl.fonts.type1_binary_utils (see that module's
docstring for why kind="OTHER" is required specifically for a PDF
/FontFile stream), rather than hand-rolling eexec decryption a second
time. An earlier version of Type 1 subsetting support did hand-roll its
own decrypt and then parsed the decrypted eexec section as `str` under
encoding="ascii" -- that breaks on any real font, since eexec-encrypted
bytes are essentially uniformly distributed over 0-255 and a byte >=
0x80 shows up in virtually every real charstring section. T1Font itself
never makes that mistake: psLib.suckfont keeps the encrypted charstring
segments as raw bytes throughout, only decoding cleartext PostScript
syntax as text, so there is nothing here to get wrong a second time.

CID resolution is not relevant here: /FontFile is restricted to
non-composite Simple fonts (ISO 32000-2 Table 121), so there is no
CID-keyed counterpart to this module, matching type1_binary_utils.py's
convention.
"""

from __future__ import annotations

import logging
import struct
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# The standard Type 1 cleartext trailer (Adobe Type 1 Font Format,
# Chapter 7): 512 zero characters as 8 lines of 64, followed by
# `cleartomark`. fontTools.t1Lib's own EEXECEND regex requires exactly
# this many zero characters to recognize the end of the eexec-encrypted
# section at all (see open_type1_font_bytes below for why this matters
# for real-world PDFs specifically, not just hand-crafted test fixtures).
_SYNTHETIC_TYPE1_TRAILER = (b"0" * 64 + b"\n") * 8 + b"cleartomark\n"


def open_type1_font_bytes(t1_bytes: bytes) -> Any | None:
    """
    Parses a raw Type 1 (/FontFile) byte stream into a real, fully
    decrypted fontTools T1Font (its CharStrings dict already holds
    decrypted, decompilable T1CharString objects after this returns).

    T1Font's readers (`read`/`readOther`/`readPFB`) always take a
    filesystem path, never raw bytes or a file-like object, so the bytes
    are round-tripped through a temp file. Returns None if the program
    can't be parsed at all, rather than raising, so a single malformed
    font can't abort a caller iterating over many fonts.

    A PDF's /FontFile stream is only required to carry its cleartext
    header (/Length1) and its encrypted eexec body (/Length2); the
    trailing 512-zeros-plus-cleartomark trailer /Length3 covers is
    explicitly optional padding (PDF 32000-1 Table 111) and is routinely
    omitted by real-world PDF producers -- confirmed directly against
    real embedded fonts, not just a theoretical edge case: both Type 1
    fonts in the originally reported test PDF have /Length3 0.
    fontTools.t1Lib's own EEXECEND regex requires that trailer to
    recognize the end of the eexec section at all
    (`t1Lib.findEncryptedChunks` raises `T1Error("can't find end of
    eexec part")` without it), so a first parse attempt failing with
    exactly that error is retried once with a synthesized trailer
    (_SYNTHETIC_TYPE1_TRAILER) appended, before giving up.
    """
    from fontTools.misc.psLib import PSError, PSTokenError
    from fontTools.t1Lib import T1Error, T1Font

    for candidate_bytes in (t1_bytes, t1_bytes + _SYNTHETIC_TYPE1_TRAILER):
        with tempfile.NamedTemporaryFile(suffix=".t1", delete=False) as tmp:
            tmp.write(candidate_bytes)
            tmp_path = Path(tmp.name)

        try:
            # T1Font defaults to encoding="ascii" for decoding its
            # cleartext PostScript header, which breaks on anything
            # outside 7-bit ASCII -- a "©" in a copyright comment is
            # common and enough to trigger it. latin-1 maps every byte
            # 0x00-0xFF to a unique code point and back, so nothing here
            # can fail to decode regardless of what the header contains.
            font = T1Font(str(tmp_path), encoding="latin-1", kind="OTHER")
            font.parse()
            return font
        except T1Error as e:
            if "can't find end of eexec part" not in str(e):
                logger.debug("Failed to parse Type 1 font program: %s", e)
                return None
            # Missing/truncated trailer -- retry once with the
            # synthesized trailer appended.
            logger.debug("Type 1 program missing eexec trailer; retrying with synthesized one.")
            continue
        except (
            OSError,
            ValueError,
            KeyError,
            IndexError,
            AssertionError,
            AttributeError,
            PSError,
            PSTokenError,
            RuntimeError,
            struct.error,
        ) as e:
            # Same audited exception set as type1_binary_utils._open_type1_font
            # covers for this identical parse path -- see that module's
            # get_widths_from_type1 for the full site-by-site justification.
            logger.debug("Failed to parse Type 1 font program: %s", e)
            return None
        finally:
            tmp_path.unlink(missing_ok=True)

    return None


def resolve_glyph_names(
    font: Any, codes: set[int], differences: list | None, base_encoding: str | None
) -> set[str]:
    """
    Resolves used PDF character codes to glyph names, preferring the
    PDF's own /Encoding (/Differences, then /BaseEncoding) when given,
    and falling back to the font program's own built-in /Encoding array
    otherwise -- matching type1_binary_utils.py's "no CID" Simple-font
    convention, and font_subsetting.gids_for_simple_font_via_encoding's
    priority order for sfnt/bare-CFF Simple fonts.

    Public (not module-private) because subset_fonts.py needs to call
    this once per /Font dictionary that shares one underlying embedded
    /FontFile stream with others -- each dictionary can have its own,
    different /Encoding pointing into the very same physical glyph
    program -- and union the resulting glyph-name sets *before* deciding
    what to keep, rather than resolving+subsetting per dictionary and
    clobbering the shared stream on every dictionary but the last (see
    build_cff_from_glyph_names for the other half of that split).
    """
    from pdftl.fonts.font_encoding_tables import _get_maps, _resolve_glyph_name

    differences_map, base_encoding_map = _get_maps(differences, base_encoding)
    builtin_encoding = font.font.get("Encoding", [])

    names: set[str] = set()
    for code in codes:
        name = _resolve_glyph_name(code, None, differences_map, base_encoding_map)
        if (
            not name
            and isinstance(builtin_encoding, (list, tuple))
            and 0 <= code < len(builtin_encoding)
        ):
            name = builtin_encoding[code]
        if name and name != ".notdef":
            names.add(name)
    return names


def resolve_code_to_glyph_names(
    font: Any, codes: set[int], differences: list | None, base_encoding: str | None
) -> dict[int, str]:
    """
    Same resolution priority as resolve_glyph_names (PDF /Differences,
    then /BaseEncoding, then the font program's own built-in /Encoding
    array), but keeps the code -> glyph-name association instead of
    collapsing it to a bare set of names.

    Needed so the rebuilt CFF's own built-in Encoding table (see
    build_cff_from_glyph_names) can reproduce the same code -> glyph
    mapping a PDF /Font dictionary with no /Encoding entry of its own
    relies on implicitly falling back to the font program's built-in
    encoding (PDF 32000-1 9.6.6.2): without an explicit /Encoding, a
    viewer resolves a shown character code to a glyph via the embedded
    font's own encoding table, not via glyph name lookup, so that table
    has to survive subsetting/conversion intact for such fonts.
    """
    from pdftl.fonts.font_encoding_tables import _get_maps, _resolve_glyph_name

    differences_map, base_encoding_map = _get_maps(differences, base_encoding)
    builtin_encoding = font.font.get("Encoding", [])

    mapping: dict[int, str] = {}
    for code in codes:
        name = _resolve_glyph_name(code, None, differences_map, base_encoding_map)
        if (
            not name
            and isinstance(builtin_encoding, (list, tuple))
            and 0 <= code < len(builtin_encoding)
        ):
            name = builtin_encoding[code]
        if name and name != ".notdef":
            mapping[code] = name
    return mapping


def _draw_charstrings(glyph_set: Any, glyph_names: set[str]) -> dict[str, Any]:
    """Redraws each requested glyph's outline as a fresh CFF (Type 2)
    charstring via a pen. A glyph that fails to draw (a malformed or
    unsupported Type 1 charstring) is skipped rather than aborting the
    whole conversion.

    A CFF charstring's advance width has to be baked into its program as
    a leading integer by T2CharStringPen itself, at getCharString() time
    -- passing a `width` to the pen's constructor is the only thing that
    puts it there; setting `.width` on the T2CharString object it
    returns afterwards has no effect on the already-finalized program
    bytes. But a Type 1 glyph's own `.width` attribute is only populated
    as a side effect of calling `.draw()` on it in the first place (see
    fontTools.t1Lib.T1Font.getGlyphSet's docstring), so the width isn't
    knowable until after a draw call has already happened. Each glyph is
    therefore drawn twice: once onto a throwaway RecordingPen purely to
    discover its width, then replayed into a real T2CharStringPen that's
    now constructed with that width already known.
    """
    from fontTools.pens.recordingPen import RecordingPen
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    charstrings: dict[str, Any] = {}
    for name in glyph_names:
        if name not in glyph_set:
            continue
        try:
            glyph = glyph_set[name]
            probe_pen = RecordingPen()
            glyph.draw(probe_pen)
            width = getattr(glyph, "width", 0)

            pen = T2CharStringPen(width=width, glyphSet=glyph_set)
            probe_pen.replay(pen)
            charstrings[name] = pen.getCharString()
        except (AttributeError, KeyError, ValueError, TypeError, struct.error) as e:
            logger.debug("Failed to convert Type 1 glyph %r to CFF: %s", name, e)
            continue
    return charstrings


def _build_cff_encoding_table(
    code_to_name: dict[int, str], charstrings: dict[str, Any]
) -> list[str] | None:
    """
    Builds a CFF Encoding table (a plain list of 256 glyph names indexed
    by code, per fontTools.cffLib; unencoded slots stay ".notdef") from a
    code->glyph-name mapping, keeping only entries whose glyph actually
    survived subsetting -- an Encoding entry pointing at a glyph name
    absent from the CFF's own charset would be invalid.

    Returns None if nothing ended up encoded (nothing for the caller to
    set on the CFF TopDict).
    """
    encoding = [".notdef"] * 256
    for code, name in code_to_name.items():
        if 0 <= code < 256 and name in charstrings:
            encoding[code] = name
    if any(name != ".notdef" for name in encoding):
        return encoding
    return None


def _assemble_cff(
    font: Any, charstrings: dict[str, Any], code_to_name: dict[int, str] | None
) -> bytes | None:
    """
    Assembles a subsetted CFF (Type 1C) byte stream from already-drawn
    `charstrings` via FontBuilder, forwarding the source Type 1 font's
    own /FontMatrix (see the FontMatrix-preservation note below) and
    optionally baking `code_to_name` into the CFF's own built-in Encoding
    table (see _build_cff_encoding_table).

    Returns None if the resulting CFF can't be compiled.
    """
    from fontTools.fontBuilder import FontBuilder
    from fontTools.ttLib import TTLibError

    try:
        ps_name = font.font.get("FontName", "SubsetFont") or "SubsetFont"
        # A CFF charset's glyph 0 must be .notdef (fontTools.cffLib
        # asserts this at compile time) -- charstrings.keys() has no
        # guaranteed order since glyph_names started life as a set, so
        # .notdef must be placed first explicitly rather than assumed.
        glyph_order = [".notdef"] + [n for n in charstrings if n != ".notdef"]
        fb = FontBuilder(unitsPerEm=1000, isTTF=False)
        fb.setupGlyphOrder(glyph_order)
        font_info = {"FullName": ps_name}
        # FontBuilder.setupCFF only supplies its own FontMatrix (a plain
        # unitsPerEm scale, no shear) when fontInfo has no "FontMatrix"
        # key -- so the source Type 1 font's own /FontMatrix must be
        # forwarded explicitly here, or a synthetic-italic/oblique font
        # that achieves its slant purely via a sheared /FontMatrix (e.g.
        # a "...-Slant_NNN" style font) silently renders upright after
        # conversion. See TestType1ToCffFontMatrixPreservation.
        source_matrix = font.font.get("FontMatrix")
        if source_matrix:
            font_info["FontMatrix"] = list(source_matrix)
        fb.setupCFF(
            psName=ps_name,
            fontInfo=font_info,
            charStringsDict=charstrings,
            privateDict={},
        )
        if code_to_name:
            encoding = _build_cff_encoding_table(code_to_name, charstrings)
            if encoding is not None:
                fb.font["CFF "].cff[ps_name].Encoding = encoding
        return fb.font["CFF "].compile(fb.font)
    except (TTLibError, KeyError, ValueError, AttributeError, TypeError) as e:
        logger.warning("Failed to compile converted Type 1 to CFF: %s", e)
        return None


def build_cff_from_glyph_names(
    font: Any, glyph_names: set[str], code_to_name: dict[int, str] | None = None
) -> bytes | None:
    """
    Draws exactly the given glyph names (plus .notdef, always kept if
    present in the source font) from an already-opened T1Font and
    assembles them into a subsetted CFF (Type 1C) byte stream.

    Split out from type1_to_cff so a caller juggling several /Font
    dictionaries that all share one underlying embedded Type 1 program
    (common in real-world PDFs -- see subset_fonts.py) can resolve each
    dictionary's own glyph names via resolve_glyph_names, union them
    across every dictionary, and build the shared CFF exactly once from
    that union -- rather than each dictionary separately calling
    type1_to_cff and each rewrite clobbering the last.

    `code_to_name`, if given (see resolve_code_to_glyph_names), is baked
    into the resulting CFF's own built-in Encoding table. This matters
    for any /Font dictionary that has no /Encoding entry of its own: a
    viewer then resolves a shown character code to a glyph via the font
    program's *own* built-in encoding (PDF 32000-1 9.6.6.2), not by
    glyph name. fontTools.fontBuilder.FontBuilder.setupCFF never sets
    topDict.Encoding at all, which leaves the CFF spec's own default of
    plain StandardEncoding in place; a math/symbol glyph like a
    relational operator has no StandardEncoding slot, so without this
    it silently becomes unreachable by code even though its charstring
    is very much still present in the font (a conforming reader such as
    FontForge shows it "encoded" past slot 255, in whatever
    supplementary encoding it invents for otherwise-unencoded glyphs,
    rather than at its original code).

    Returns None if nothing beyond .notdef could be drawn (not worth
    writing back, mirrors font_subsetting.run_subsetter's "more than
    just .notdef" bar), or the resulting CFF can't be assembled.
    """
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    glyph_set = font.getGlyphSet()
    names = set(glyph_names)
    if ".notdef" in glyph_set:
        names.add(".notdef")

    charstrings = _draw_charstrings(glyph_set, names)

    if ".notdef" not in charstrings:
        pen = T2CharStringPen(width=0, glyphSet=None)
        charstrings[".notdef"] = pen.getCharString()

    if len(charstrings) <= 1:
        # Nothing beyond .notdef survived -- matches
        # font_subsetting.run_subsetter's bar for "worth writing back".
        return None

    return _assemble_cff(font, charstrings, code_to_name)


def type1_to_cff(
    t1_bytes: bytes,
    codes: set[int],
    differences: list | None = None,
    base_encoding: str | None = None,
) -> bytes | None:
    """
    Converts a Type 1 font program to a subsetted CFF (Type 1C) byte
    stream retaining only the glyphs reachable from `codes` (plus
    .notdef, always kept if present in the source font).

    Convenience wrapper around open_type1_font_bytes + resolve_glyph_names
    + build_cff_from_glyph_names for the common single-/Font-dictionary
    case; a caller that needs to union glyph names across several
    dictionaries sharing one embedded program should call those three
    directly instead (see subset_fonts.py).

    Returns None if: the font can't be parsed at all; nothing beyond
    .notdef could be resolved and drawn; or the resulting CFF can't be
    assembled.
    """
    if not codes:
        return None

    font = open_type1_font_bytes(t1_bytes)
    if font is None:
        return None

    glyph_names = resolve_glyph_names(font, codes, differences, base_encoding)
    code_to_name = resolve_code_to_glyph_names(font, codes, differences, base_encoding)
    return build_cff_from_glyph_names(font, glyph_names, code_to_name)
