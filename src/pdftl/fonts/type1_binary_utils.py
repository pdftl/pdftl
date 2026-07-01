# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/fonts/type1_binary_utils.py

"""
Utilities for reading and mutating advance-width metrics directly inside
classic Type 1 font programs (a PDF's /FontFile stream) using
fontTools.t1Lib / fontTools.misc.psCharStrings.

Per ISO 32000-2 Table 121, /FontFile only ever appears on a non-composite
Simple font -- Type 1 programs are never CID-keyed, so unlike
cff_binary_utils.py and font_binary_utils.py's TrueType path, there is no
CID-keyed variant of anything in this module at all.

## Byte layout

A PDF /FontFile stream is always the plain (non-segmented) Type 1 program
form: a cleartext ASCII header, the literal token sequence
`currentfile eexec`, an eexec-encrypted binary (or ASCII-hex) portion, a
trailing run of zero-padding lines, and a final `cleartomark` operator
(Adobe Type 1 Font Format, Chapter 7 -- the same structure
font_import_helpers.py's `_find_type1_segment_lengths` already parses at
the outer stream-length level). It is never a real segmented PFB (the
0x80-prefixed binary-chunk container format some standalone .pfb files on
disk use) -- see `_open_type1_font` below for why this matters when
opening it via fontTools.

## Width encoding

A Type 1 charstring encodes its own advance width via its own leading
`hsbw` (`sbx wx hsbw`) or `sbw` (`sbx sby wx wy sbw`) operator -- there is
no separate hmtx-equivalent side table the way TrueType/CFF have. Per the
Adobe Type 1 Font Format (Chapter 8), this operator is required to be the
very first command in every charstring.

Reading the width via fontTools' own pen-based extraction
(`charstring.draw(pen)` then `.width`, the pattern used for CFF in
cff_binary_utils.py) was tried and rejected: as of fontTools 4.62.1 (and
confirmed directly against the installed version rather than assumed),
`psCharStrings.T1OutlineExtractor.op_sbw` unconditionally discards its
operands (`self.popall()  # XXX`) and never sets `.width` at all --
`hsbw`-based glyphs draw a correct width, but any glyph using `sbw`
instead would silently read back as width 0. This module instead reads
(and patches) the width operand directly out of the charstring's own
decompiled operand list via `_find_width_operator`, giving read and patch
a single, shared, verified mechanism that isn't exposed to that gap.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# hsbw: sbx wx hsbw          -> width operand is one slot before the operator
# sbw:  sbx sby wx wy sbw    -> width operand is two slots before the operator
_WIDTH_OPERAND_OFFSET = {"hsbw": 1, "sbw": 2}


def _open_type1_font(filepath: Path) -> Any:
    """
    Opens a raw Type 1 (/FontFile) byte stream via fontTools.t1Lib.T1Font.

    `kind="OTHER"` is deliberate and required: fontTools.t1Lib.T1Font's
    default (`kind=None`) dispatches on the file's *extension* --
    `.pfb` selects `readPFB`, which expects a real segmented PFB container
    (0x80-prefixed binary chunk markers) and raises `T1Error("corrupt PFB
    file")` on anything else. A PDF /FontFile stream is always the plain,
    non-segmented form (see module docstring) regardless of what suffix
    pdftl happens to save the extracted stream under (font_export_helpers.py
    names every /FontFile extraction "pfb" purely for readability -- see
    `_get_embedded_font_details`'s `key_map`). Forcing `kind="OTHER"`
    selects `readOther`, which reads the plain form directly and ignores
    the extension entirely. Verified directly: a hand-built plain-form
    font saved under a ".pfb" name fails via the default extension-based
    dispatch and succeeds via `kind="OTHER"`.
    """
    from fontTools.t1Lib import T1Font

    font = T1Font(str(filepath), kind="OTHER")
    font.parse()
    return font


def _find_width_operator(program: list) -> tuple[int, str] | tuple[None, None]:
    """
    Locates the `hsbw`/`sbw` operator token in a decompiled Type 1
    charstring's operand/operator list. Per the Adobe Type 1 Font Format,
    this operator is required to be a charstring's very first command, but
    this scans the whole program defensively rather than assuming index 0,
    so a malformed or non-conformant program degrades to "not found"
    instead of an IndexError.
    """
    for index, token in enumerate(program):
        if token in _WIDTH_OPERAND_OFFSET:
            return index, token
    return None, None


def _read_charstring_width(charstring: Any) -> float | None:
    """
    Reads a single Type 1 charstring's advance width directly from its
    decompiled operand list (see module docstring for why this doesn't use
    `.draw()` + `.width` the way CFF/TrueType do). Returns None if the
    charstring can't be decompiled, carries no `hsbw`/`sbw` operator at
    all, or that operator's width operand isn't a plain number (e.g. built
    via `div` or another sub-expression rather than a literal) -- any of
    which mean this glyph's width can't be safely read this way, and the
    caller should skip it rather than guess.
    """
    try:
        charstring.decompile()
    except (AttributeError, IndexError, KeyError, ValueError) as e:
        # A malformed or truncated charstring program shouldn't abort
        # reading every other glyph's width; skip just this one.
        logger.debug("Failed to decompile Type 1 charstring for width: %s", e)
        return None

    index, op = _find_width_operator(charstring.program)
    if index is None:
        return None

    width_index = index - _WIDTH_OPERAND_OFFSET[op]
    if width_index < 0:
        # The width operand would sit before the start of the program --
        # a malformed charstring with too few operands ahead of its own
        # required first operator. Skip rather than raise.
        logger.debug("Malformed hsbw/sbw operand stack in Type 1 charstring.")
        return None

    value = charstring.program[width_index]
    if not isinstance(value, (int, float)):
        # A width built from a sub-expression (e.g. `... div`) rather
        # than a literal number isn't something this module attempts to
        # evaluate; skip rather than misread it.
        logger.debug("Non-literal hsbw/sbw width operand in Type 1 charstring.")
        return None
    return value


def get_widths_from_type1(filepath: Path) -> dict[str, float]:
    """
    Measures advance widths of every glyph in a bare Type 1 (/FontFile)
    font program, keyed by the font's own glyph name.

    Mirrors pdftl.fonts.cff_binary_utils.get_widths_from_cff's Simple-font
    (non-CID) convention: resolving a PDF character code to a glyph name
    via /Differences, /BaseEncoding, or the font's own built-in /Encoding
    is the caller's responsibility. Type 1 fonts are never CID-keyed (ISO
    32000-2 Table 121 restricts /FontFile to non-composite Simple fonts),
    so there is no CID-keyed counterpart to this function at all.
    """
    widths: dict[str, float] = {}
    try:
        from fontTools.t1Lib import T1Error
        from fontTools.misc.psLib import PSError, PSTokenError
        import struct
    except ImportError as exc:
        logger.debug("ImportError: %s", exc)
        return widths

    try:
        font = _open_type1_font(filepath)
    except (
        OSError,
        ValueError,
        KeyError,
        IndexError,
        AssertionError,
        AttributeError,
        T1Error,
        PSError,
        PSTokenError,
        RuntimeError,
        struct.error,
    ) as e:
        # Every exception type here has been traced to a specific, real raise
        # site reachable from fontTools' Type 1 parsing path (t1Lib.parse() ->
        # psLib.suckfont() -> PSInterpreter.interpret() / psCharStrings decode):
        #   T1Error        - t1Lib: corrupt PFB/LWFN, missing/malformed eexec section
        #   PSTokenError   - psLib tokenizer: malformed string/hexstring/token
        #   PSError        - psLib interpreter: unresolvable PS name, stack underflow
        #   RuntimeError   - psOperators: PS stack/dictstack underflow during execution
        #   struct.error   - psCharStrings: truncated/malformed binary numeric operand
        #                    (NOT a ValueError subclass, must be listed explicitly)
        #   ValueError/AttributeError/KeyError/IndexError/OSError/AssertionError -
        #                    malformed tokens, None-valued items, missing data, I/O
        # This is not a blanket catch: it's the complete, audited set of exception
        # types fontTools can raise from this call, based on tracing every `raise`
        # site in psLib.py, psOperators.py, psCharStrings.py, t1Lib/__init__.py.
        logger.debug("Failed to read Type 1 font file %s: %s", filepath.name, e)
        return widths
    charstrings = font.font["CharStrings"]
    for glyph_name in charstrings.keys():
        width = _read_charstring_width(charstrings[glyph_name])
        if width is not None:
            widths[glyph_name] = width
    return widths


def _patch_single_type1_width(charstring: Any, new_width: float) -> bool:
    """
    Rewrites a single Type 1 charstring's advance width operand in place
    (the `wx` operand of its leading `hsbw`/`sbw` command) and recompiles
    it. Returns False (rather than raising) if the width can't be located
    or the recompile itself fails, mirroring
    cff_binary_utils._patch_single_cff_width's contract.
    """
    try:
        charstring.decompile()
    except (AttributeError, IndexError, KeyError, ValueError) as e:
        logger.debug("Failed to decompile Type 1 charstring for patching: %s", e)
        return False

    index, op = _find_width_operator(charstring.program)
    if index is None:
        return False

    width_index = index - _WIDTH_OPERAND_OFFSET[op]
    if width_index < 0:
        logger.debug("Malformed hsbw/sbw operand stack in Type 1 charstring.")
        return False

    # A Type 1 charstring's operands are always plain integers (Adobe Type 1
    # Font Format, Chapter 8) -- there is no Fixed (16.16) operand encoding
    # the way CFF2 has. fontTools.misc.psCharStrings.T1CharString.compile()
    # only defines an int encoder and a Fixed encoder; the latter is always
    # None for a Type 1 (non-CFF2) charstring, so writing a Python float
    # here reaches `encodeFixed(token)` in compile() and raises `TypeError:
    # 'NoneType' object is not callable` rather than anything more
    # descriptive. `new_width` is rounded rather than truncated since PDF
    # /Widths values themselves are frequently not whole numbers.
    int_width = int(round(new_width))
    charstring.program[width_index] = int_width
    charstring.width = int_width
    try:
        charstring.compile()
    except (AttributeError, IndexError, KeyError, ValueError) as e:
        logger.debug("Failed to recompile patched Type 1 charstring: %s", e)
        return False
    return True


def patch_type1_widths(filepath: Path, pdf_widths: dict[str, float]) -> bytes | None:
    """
    Patches advance widths in a bare Type 1 (/FontFile) font program
    in-memory, returning the recompiled full font-program bytes, or None
    if nothing was patched (either because no name in `pdf_widths` matched
    a real glyph, or the font program couldn't be parsed at all).

    `pdf_widths` is keyed by glyph name, mirroring
    pdftl.fonts.cff_binary_utils.patch_cff_widths's Simple-font
    convention -- resolving a PDF character code to a glyph name is the
    caller's responsibility.

    Unlike a flat hmtx table (TrueType) or a single decompiled CFF table,
    a Type 1 font's on-disk representation is its own full cleartext
    header plus an eexec-encrypted body; there's no way to patch just one
    charstring's bytes in isolation, so a successful patch always
    recompiles and re-serializes the *entire* font program via
    `T1Font.createData()`, not just the touched glyphs.
    """
    try:
        from fontTools.t1Lib import T1Error
    except ImportError:
        return None

    try:
        font = _open_type1_font(filepath)
    except (OSError, ValueError, KeyError, IndexError, AssertionError, T1Error) as e:
        logger.debug("Failed to read Type 1 font file %s: %s", filepath.name, e)
        return None

    charstrings = font.font["CharStrings"]
    patched_any = False
    for glyph_name, new_width in pdf_widths.items():
        if glyph_name not in charstrings:
            continue
        if _patch_single_type1_width(charstrings[glyph_name], new_width):
            patched_any = True

    if not patched_any:
        return None

    try:
        data = font.createData()
    except (AttributeError, IndexError, KeyError, ValueError) as e:
        logger.warning("Failed to recompile patched Type 1 font program: %s", e)
        return None

    logger.info(
        "Successfully patched advance widths in memory for Type 1 program %s", filepath.name
    )
    return data
