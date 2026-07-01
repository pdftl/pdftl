# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/fonts/cff_binary_utils.py

"""
Utilities for reading and mutating advance-width metrics directly inside
*bare* CFF font programs (.cff) using fontTools.cffLib.

"Bare" CFF means a raw CFF table byte stream with no surrounding sfnt
(OpenType) container -- exactly what a PDF /FontFile3 stream contains when
its /Subtype is /Type1C or /CIDFontType0C (ISO 32000-2 Table 124). This is
distinct from an OpenType-wrapped CFF or CFF2 program (/FontFile3 /Subtype
/OpenType), which is sfnt-wrapped and already handled by
pdftl.fonts.font_binary_utils via fontTools.ttLib.TTFont -- TTFont cannot
open a bare CFF table on its own, since it expects an sfnt header and table
directory that a bare CFF stream does not have.

Two distinct glyph-selection regimes apply, per ISO 32000-2 Table 115 and
9.7.4.2, "Glyph selection in CIDFonts":

  - A Simple font's /FontFile3 /Type1C program: glyphs are selected by name,
    via the same /Differences, /BaseEncoding, and font-cmap priority chain
    already implemented for TrueType in font_binary_utils.py.

  - A CIDFontType0 (CFF-based) descendant's /FontFile3 /CIDFontType0C
    program: there is no /CIDToGIDMap on a CIDFontType0 descendant at all
    (Table 115 restricts /CIDToGIDMap to Type 2 CIDFonts) -- CID-to-glyph
    resolution is entirely internal to the CFF program itself, and per
    9.7.4.2 splits into two cases depending on whether the CFF's Top DICT
    uses CIDFont operators (carries a ROS entry):
      * ROS present (a genuine CID-keyed CFF): the CID is looked up in the
        CFF's own charset table to obtain a GID, and that GID indexes the
        CharStrings INDEX. fontTools represents this charset as a list of
        synthetic glyph names of the form "cid" + CID zero-padded to 5
        digits (e.g. CID 10 -> "cid00010"), positioned by GID -- see
        fontTools.cffLib's packCharset/parseCharset.
      * ROS absent (an ordinary, non-CID-keyed CFF program used as a
        CIDFontType0C): the CID is used directly as the GID.
"""

from __future__ import annotations

import logging
import struct
from io import BytesIO
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class _MinimalOTFontStub:
    """
    Stand-in for the fontTools.ttLib.TTFont context that
    fontTools.cffLib.CFFFontSet.compile() expects as its `otFont` argument,
    used when operating on a bare (non-sfnt-wrapped) CFF stream that has no
    real containing TTFont object at all.

    CFFFontSet.compile() only ever reads `otFont.recalcBBoxes` off this
    object (to decide whether to recompute each Top DICT's FontBBox before
    writing); a width-only edit never touches glyph outlines, so
    recalculation is never needed here.
    """

    recalcBBoxes = False


def _is_cid_keyed(topdict: Any) -> bool:
    """
    Detects whether a CFF Top DICT uses CIDFont operators (carries a ROS
    entry), per ISO 32000-2 9.7.4.2. This is the single branch point between
    the two CID-to-GID resolution regimes for a CIDFontType0C program.
    """
    return hasattr(topdict, "ROS")


def _resolve_cff_cid_to_gid(topdict: Any, cid: int) -> int | None:
    """
    Resolves a CID to a GID for a CIDFontType0C (CFF-based) CIDFont, per ISO
    32000-2 9.7.4.2. Returns None if the CID has no corresponding glyph in
    this font program -- callers should skip that CID entirely rather than
    guessing a fallback GID.
    """
    charstrings = topdict.CharStrings

    if _is_cid_keyed(topdict):
        cid_name = f"cid{cid:05d}"
        if cid_name not in charstrings.charStrings:
            return None
        return charstrings.charStrings[cid_name]

    # Non-CID-keyed CFF used as a CIDFontType0C program: the CID is used
    # directly as the GID (9.7.4.2, second bullet).
    if cid < 0 or cid >= len(charstrings.charStrings):
        return None
    return cid


def _glyph_name_for_gid(topdict: Any, gid: int) -> str | None:
    """
    Resolves a GID to its glyph (or synthetic CID) name via the CFF's own
    charset, mirroring pdftl.fonts.font_binary_utils._resolve_cid_glyph_name
    for TrueType. Returns None if the GID falls outside the font's actual
    glyph set -- e.g. a resolved GID beyond what the embedded program
    actually defines, which can happen with a mismatched or corrupted
    font/PDF pairing.
    """
    charset = topdict.charset
    if 0 <= gid < len(charset):
        return charset[gid]
    return None


def _decompile_bare_cff(data: bytes):
    """
    Opens a bare (non-sfnt-wrapped) CFF byte stream and returns its single
    Top DICT. `otFont=None` is deliberate here: CFFFontSet.decompile() only
    needs an otFont context for CFF2's variable-font machinery, which bare
    CFF (major version 1) never uses.
    """
    from fontTools.cffLib import CFFFontSet

    cff_font_set = CFFFontSet()
    cff_font_set.decompile(BytesIO(data), otFont=None)
    topdict = cff_font_set[cff_font_set.fontNames[0]]
    return cff_font_set, topdict


def _measure_charstring_width(charstring: Any) -> float | None:
    """
    Reads a single Type 2 charstring's advance width. A charstring's width
    is not populated on decompile alone -- it is only recorded once the
    charstring's program has actually been interpreted, since the width is
    encoded as an optional leading numeric operand consumed by whichever
    path/hint operator first clears the argument stack. Any pen suffices
    here since only `.width` is read afterwards, not the drawn outline.
    """
    from fontTools.pens.basePen import NullPen

    try:
        charstring.decompile()
        charstring.draw(NullPen())
    except (AttributeError, IndexError, KeyError, ValueError) as e:
        # A malformed or truncated charstring program (e.g. a corrupted font/PDF pairing)
        # shouldn't abort reading every other glyph's width; skip just this one.
        logger.debug("Failed to interpret CFF charstring for width: %s", e)
        return None
    return charstring.width


def get_widths_from_cff(filepath: Path, cid_to_gid_map: str | None = None) -> dict[str, float]:
    """
    Measures advance widths of a bare CFF font program (Type1C or
    CIDFontType0C) using fontTools.cffLib.

    For a Simple font (`cid_to_gid_map` omitted), widths are keyed by the
    font's own charset-derived glyph name resolved back to nothing useful on
    its own -- Simple-font glyph-name resolution (via /Differences,
    /BaseEncoding, or the font's own encoding) is the caller's
    responsibility, mirroring pdftl.fonts.font_binary_utils's TrueType path;
    this function returns a name-keyed width map for the caller to re-key by
    code.

    For a CIDFontType0C program, pass `cid_to_gid_map="cff_native"` to
    request CID-keyed reading, resolved entirely via the CFF's own
    ROS/charset mechanism (see module docstring) -- unlike the TrueType
    path, there is no external /CIDToGIDMap to consult (ISO 32000-2 Table
    115 restricts that entry to Type 2 CIDFonts), so this is the only value
    this parameter accepts.
    """
    widths: dict[str, float] = {}
    try:
        data = filepath.read_bytes()
        _, topdict = _decompile_bare_cff(data)
    except (OSError, ValueError, IndexError, KeyError, AssertionError, struct.error) as e:
        # CFFFontSet.decompile() asserts on the major-version byte for input
        # that isn't a CFF table at all, and raises struct.error for input that has a
        # valid-looking header but is truncated partway through an index/offset table.
        # Treat both the same as any other unreadable/malformed font program rather
        # than letting either propagate uncaught.
        logger.debug("Failed to read bare CFF font file %s: %s", filepath.name, e)
        return widths

    if cid_to_gid_map == "cff_native":
        return _get_cff_cid_widths(topdict)
    return _get_cff_name_widths(topdict)


def _get_cff_name_widths(topdict: Any) -> dict[str, float]:
    """Reads widths for every glyph in a Simple-font (name-keyed) CFF program."""
    widths: dict[str, float] = {}
    try:
        charstrings = topdict.CharStrings
    except AttributeError:
        # Some real-world fonts omit the 'charset' operator entirely, which
        # is legal (implies default predefined ISOAdobe charset) but which
        # fontTools' TopDict.__getattr__ can't resolve without it explicitly
        # present. Treat as "no usable width data" rather than crashing.
        return {}
    for glyph_name in charstrings.keys():
        width = _measure_charstring_width(charstrings[glyph_name])
        if width is not None:
            widths[glyph_name] = width
    return widths


def _get_cff_cid_widths(topdict: Any) -> dict[str, float]:
    """Reads CID-keyed widths for a CIDFontType0C program, resolved via the
    CFF's own ROS/charset mechanism rather than any external mapping."""
    widths: dict[str, float] = {}
    charset = topdict.charset
    for gid, name in enumerate(charset):
        cid = _cid_from_charset_name(name) if _is_cid_keyed(topdict) else gid
        if cid is None:
            continue
        width = _measure_charstring_width(topdict.CharStrings[name])
        if width is not None:
            widths[f"{cid:04X}"] = width
    return widths


def _cid_from_charset_name(name: str) -> int | None:
    """
    Recovers the CID encoded in a CID-keyed CFF's synthetic charset entry
    name (e.g. "cid00010" -> 10). Returns None for a name that doesn't
    follow this convention, which shouldn't occur for a genuinely CID-keyed
    Top DICT but is guarded defensively rather than raising, since this
    walks data straight from the font file.
    """
    if not name.startswith("cid"):
        # A non-CID-keyed charset entry (a plain glyph name) reaching this CID-only
        # helper indicates a caller error or an unusual font; skip rather than
        # misinterpret an arbitrary string as a CID.
        logger.debug("Unexpected non-CID charset entry '%s' in CID-keyed CFF.", name)
        return None
    try:
        return int(name[3:])
    except ValueError:
        logger.debug("Malformed CID charset entry '%s' in CID-keyed CFF.", name)
        return None


def _patch_name_widths(topdict: Any, pdf_widths: dict[str, float]) -> bool:
    """Patches every glyph in `pdf_widths` (keyed by glyph name) in a
    Simple-font (name-keyed) CFF program. Returns True if anything patched."""
    patched_any = False
    for glyph_name, new_width in pdf_widths.items():
        if glyph_name not in topdict.CharStrings.charStrings:
            continue
        if _patch_single_cff_width(topdict, glyph_name, new_width):
            patched_any = True
    return patched_any


def _patch_cid_widths(topdict: Any, pdf_widths: dict[str, float]) -> bool:
    """Patches every CID in `pdf_widths` (keyed by 4-digit-hex CID string)
    in a CIDFontType0C program, resolved via the CFF's own ROS/charset
    mechanism. Returns True if anything patched."""
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


def _resolve_nominal_width_x(topdict: Any, charstring: Any) -> float:
    """
    Resolves the nominalWidthX in effect for a single charstring.

    A CID-keyed CFF built with a real FDArray/FDSelect structure (the shape
    actual font-authoring tools -- FontForge, Adobe-originated CJK fonts,
    etc. -- produce) never carries a usable top-level `topdict.Private`;
    nominalWidthX lives only on each FD's own Private dict, and different
    FDs are free to carry different values. fontTools resolves this at
    decompile time and stores the correct, already-FD-resolved Private
    directly on the charstring itself as `.private` -- that is the only
    value that is correct for every CFF shape (single-Private simple CFF,
    single-FD CID-keyed CFF, and multi-FD CID-keyed CFF alike), so it is
    read here in preference to `topdict.Private`.

    Falls back to `topdict.Private.nominalWidthX` for the (non-conformant
    but not impossible) case of a charstring with no `.private` of its own;
    defaults to 0 -- the CFF spec's own default -- if neither source has
    a usable value, rather than raising.
    """
    private = getattr(charstring, "private", None)
    if private is None:
        private = getattr(topdict, "Private", None)
    return getattr(private, "nominalWidthX", 0)


# Type 2 charstring operators that stop and interpret the leading operand
# stack (Adobe TN #5177 §3.1, "Charstring Number Encoding" / §4.1). Width
# parity is only decidable relative to *one* of these operators — whichever
# is the first one encountered while scanning the program's leading numeric
# operands. `_MOVETO_ARGCOUNTS` gives the width-absent argument count for
# each of the three moveto forms; `_STEM_OPS` take operands in coordinate
# *pairs*, so width-presence there is decided by count parity (odd => a
# leading width is present) rather than a fixed expected count.
_MOVETO_ARGCOUNTS = {"rmoveto": 2, "hmoveto": 1, "vmoveto": 1}
# hintmask/cntrmask implicitly clear any pending vstem args before the mask
# byte -- same odd/even parity rule as hstem/vstem for width-presence.
_STEM_OPS = frozenset({"hstem", "vstem", "hstemhm", "vstemhm", "hintmask", "cntrmask"})

_MAX_SUBR_TRACE_DEPTH = 10


def _subr_bias(count: int) -> int:
    """Standard Type 2 charstring subroutine index bias (Adobe TN #5177 §4.7)."""
    if count < 1240:
        return 107
    if count < 33900:
        return 1131
    return 32768


def _get_local_subrs(charstring: Any):
    """Resolves a charstring's local subroutine list, supporting both a
    direct `.subrs` attribute (used by test doubles) and the real
    fontTools shape, where local subrs live on `.private.Subrs`."""
    subrs = getattr(charstring, "subrs", None)
    if subrs is not None:
        return subrs
    private = getattr(charstring, "private", None)
    return getattr(private, "Subrs", None) if private is not None else None


def _resolve_subr(subrs, index: int):
    if not subrs:
        return None
    bias = _subr_bias(len(subrs))
    real_index = index + bias
    if real_index < 0 or real_index >= len(subrs):
        return None
    return subrs[real_index]


def _presence_result(
    present: bool, depth: int, carried_len: int
) -> tuple[bool | None, int | None]:
    """
    Converts a raw stack-parity verdict into the safe (width_present,
    insert_index) result, applying the shared-subroutine guard: a
    "present" verdict is only safe to act on if the extra operand traces
    back to the glyph's own (unique, non-shared) program rather than to a
    shared subroutine's own pushes -- overwriting a shared subroutine's
    leading operand would corrupt every other glyph that also calls it.
    An "absent" verdict has no such risk, since prepending only ever
    mutates the caller's own top-level program.
    """
    if present and depth > 0 and carried_len == 0:
        logger.debug(
            "Width-presence resolved inside a shared subroutine with "
            "no corresponding operand in the calling glyph's own "
            "program; treating as undeterminable rather than risking "
            "a shared-subroutine corruption."
        )
        return None, None
    return present, 0


def _resolve_called_subr(subr_index: Any, subrs: Any) -> Any | None:
    """
    Resolves and decompiles the subroutine a callsubr/callgsubr operand
    refers to, returning None if the index is non-numeric, out of range,
    or the subroutine can't be decompiled -- any of which make the
    delegation untraceable.
    """
    try:
        idx = int(subr_index)
    except (TypeError, ValueError):
        return None
    subr = _resolve_subr(subrs, idx)
    if subr is None:
        return None
    if hasattr(subr, "decompile"):
        try:
            subr.decompile()
        except (AttributeError, IndexError, KeyError, ValueError):
            return None
    return subr


def _trace_into_subr(
    subr: Any, args: list, local_subrs, global_subrs, depth: int
) -> tuple[bool | None, int | None]:
    """Continues width-presence tracing into a resolved subroutine's own
    program, carrying over the caller's accumulated (post-index-pop) args."""
    sub_program = getattr(subr, "program", None)
    if sub_program is None:
        return None, None
    sub_local_subrs = _get_local_subrs(subr) or local_subrs
    sub_global_subrs = getattr(subr, "globalSubrs", None) or global_subrs
    return _find_width_presence(sub_program, sub_local_subrs, sub_global_subrs, depth + 1, args)


_STOP_RESULT = object()  # sentinel: this branch produced a final (bool|None, int|None) result
_DELEGATE = object()  # sentinel: this branch means "recurse into a subroutine"


def _classify_stack_clearer(item: Any, args: list) -> bool | None:
    """
    Given a stack-clearing operator token and the accumulated numeric args
    seen so far, returns the raw width-presence parity verdict for that
    operator kind, or None if `item` isn't a stack-clearing operator at all
    (i.e. the caller should try a different classification).
    """
    if item in _MOVETO_ARGCOUNTS:
        return len(args) == _MOVETO_ARGCOUNTS[item] + 1
    if item in _STEM_OPS:
        return len(args) % 2 == 1
    if item == "endchar":
        return len(args) in (1, 5)
    return None


def _step_width_presence(
    item: Any, args: list, local_subrs, global_subrs, depth: int, carried_len: int
) -> tuple[Any, tuple[bool | None, int | None] | None]:
    """
    Processes a single program token during width-presence scanning.
    Returns (_STOP_RESULT, verdict) if this token yields a final answer,
    (_DELEGATE, verdict) if this token means "recurse into a subroutine and
    return its verdict directly", or (None, None) if the caller should keep
    scanning (only reached for plain numeric operands, handled by the
    caller before this is invoked).
    """
    verdict = _classify_stack_clearer(item, args)
    if verdict is not None:
        return _STOP_RESULT, _presence_result(verdict, depth, carried_len)

    if item in ("callsubr", "callgsubr"):
        if not args:
            return _STOP_RESULT, (None, None)
        subr_index = args.pop()
        subrs = local_subrs if item == "callsubr" else global_subrs
        subr = _resolve_called_subr(subr_index, subrs)
        if subr is None:
            return _STOP_RESULT, (None, None)
        return _DELEGATE, _trace_into_subr(subr, args, local_subrs, global_subrs, depth)

    return _STOP_RESULT, (None, None)


def _find_width_presence(
    program: list,
    local_subrs=None,
    global_subrs=None,
    _depth: int = 0,
    _carried_args: list | None = None,
) -> tuple[bool | None, int | None]:
    """
    Determines whether a Type 2 charstring's `program` carries an explicit
    leading width operand (Adobe TN #5177): width is present iff the
    leading operand count at the first stack-clearing operator is one more
    than that operator's own expected (width-absent) argument count.

    `hstem`/`vstem`/`hstemhm`/`vstemhm`/`hintmask`/`cntrmask` all clear the
    stack via count parity; the three moveto forms via a fixed expected
    count; `endchar` via its own two allowed forms -- see
    `_classify_stack_clearer`.

    `callsubr`/`callgsubr` do NOT clear the stack -- per spec, a width
    operand (if any) is always pushed in the outermost calling charstring
    before any subroutine delegation, so this traces into the called
    subroutine (see `_resolve_called_subr`/`_trace_into_subr`), carrying
    over whatever args were already accumulated (minus the popped
    subroutine-index operand). A depth guard bounds this against
    runaway/self-referential subrs, degrading to "undeterminable" rather
    than looping forever.

    Shared-subroutine safety: a real, professionally-hinted font (see
    tests/fonts/test_cff_roundtrip_integration.py's source_sans3_subset.otf
    fixture) commonly has glyphs whose ENTIRE program is just a
    subroutine-index push + callgsubr, with no operands of their own at
    all -- the width (if pushed at all) is pushed inside the subroutine's
    own program, not the calling glyph's. Since that subroutine is shared
    across every glyph that calls it, a "present" verdict resolved only
    after descending into a subroutine -- with nothing pushed in the
    calling glyph's own (unique, top-level) program first -- is NOT safe
    to act on. Such cases are reported as undeterminable (None, None)
    rather than guessed at -- see `_presence_result` for the exact
    carried-args check that distinguishes this from the safe, well-
    supported case where the width genuinely is pushed in the glyph's own
    program before delegating
    (test_patch_width_present_before_subroutine_delegation).

    `insert_index` is 0 whenever a present/absent verdict is safely
    determinable: a width operand, when it's the calling glyph's own (not
    a shared subroutine's), is always the very first token of the
    *original* (outermost) program passed in.
    """
    if _depth > _MAX_SUBR_TRACE_DEPTH:
        return None, None

    carried_len = len(_carried_args) if _carried_args is not None else 0
    args: list = list(_carried_args) if _carried_args is not None else []

    for item in program:
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            args.append(item)
            continue
        _, verdict = _step_width_presence(
            item, args, local_subrs, global_subrs, _depth, carried_len
        )
        return verdict
    return None, None


def _patch_single_cff_width(topdict: Any, glyph_name: str, new_width: float) -> bool:
    """
    Rewrites a single charstring's advance width in place.

    A Type 2 charstring's width is not a separate field; it is the optional
    leading numeric operand of the charstring's own program, present or
    absent depending on argument-count parity at the first stack-clearing
    operator (see `_find_width_presence`). When present, the existing
    operand is updated in place; when absent, the new relative width is
    prepended rather than overwriting what is actually the glyph's first
    real drawing/hint coordinate. If presence can't be determined at all
    (e.g. the program delegates to a subroutine before any stack-clearing
    operator), this function does not guess, and returns False leaving the
    program untouched.
    """
    charstring = topdict.CharStrings[glyph_name]
    if _measure_charstring_width(charstring) is None:
        # The charstring couldn't be interpreted at all; nothing to patch.
        return False

    width_present, insert_index = _find_width_presence(
        charstring.program,
        local_subrs=_get_local_subrs(charstring),
        global_subrs=getattr(charstring, "globalSubrs", None),
    )
    if width_present is None:
        logger.debug(
            "Could not determine width-operand presence for glyph '%s' "
            "(likely delegated to a subroutine); skipping width patch.",
            glyph_name,
        )
        return False

    nominal_width_x = _resolve_nominal_width_x(topdict, charstring)
    relative_width = new_width - nominal_width_x
    if width_present:
        charstring.program[insert_index] = relative_width
    else:
        charstring.program.insert(insert_index, relative_width)
    charstring.width = new_width

    try:
        charstring.compile()
    except (AttributeError, IndexError, KeyError, ValueError) as e:
        logger.debug("Failed to recompile patched CFF charstring '%s': %s", glyph_name, e)
        return False
    return True


def patch_cff_widths(
    filepath: Path, pdf_widths: dict[str, float], cid_to_gid_map: str | None = None
) -> bytes | None:
    """
    Patches advance widths in a bare CFF font program (Type1C or
    CIDFontType0C) in-memory, returning the recompiled bare CFF bytes, or
    None if nothing was patched (either because no code/CID in `pdf_widths`
    matched a real glyph, or the font program couldn't be parsed at all).

    For a Simple font, `pdf_widths` is keyed by glyph name -- resolving PDF
    character codes to glyph names via /Differences, /BaseEncoding, or the
    font's own encoding is the caller's responsibility, mirroring
    pdftl.fonts.font_binary_utils's TrueType path. For a CIDFontType0C
    program, pass `cid_to_gid_map="cff_native"` and key `pdf_widths` by
    4-digit-hex CID.
    """
    try:
        data = filepath.read_bytes()
        cff_font_set, topdict = _decompile_bare_cff(data)
    except (OSError, ValueError, IndexError, KeyError, AssertionError, struct.error) as e:
        # See get_widths_from_cff for why AssertionError and struct.error
        # are caught here alongside the usual I/O and parsing exceptions.
        logger.debug("Failed to read bare CFF font file %s: %s", filepath.name, e)
        return None

    if cid_to_gid_map == "cff_native":
        patched_any = _patch_cid_widths(topdict, pdf_widths)
    else:
        patched_any = _patch_name_widths(topdict, pdf_widths)

    if not patched_any:
        return None

    buf = BytesIO()
    try:
        cff_font_set.compile(buf, otFont=_MinimalOTFontStub())
    except (AttributeError, IndexError, KeyError, ValueError) as e:
        logger.warning("Failed to recompile patched bare CFF font program: %s", e)
        return None

    logger.info(
        "Successfully patched advance widths in memory for bare CFF program %s", filepath.name
    )
    return buf.getvalue()
