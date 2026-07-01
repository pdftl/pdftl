# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/fonts/fixtures/font_fixture_builder.py

"""
Builds small, genuinely valid font byte streams via fontTools' own builders,
for use as real (non-mocked) test fixtures across the font-fidelity test
suite (see notes/font_second_plan-v2.md, Section 2, T-1).

Deliberately does NOT download or embed any third-party font files:
licensing and network restrictions both argue against it. Every fixture
here is synthesized at test time from scratch, so it is reproducible,
licensed under this project's own terms, and needs no network access.

Currently covers:
  - Bare, non-CID-keyed CFF (Type1C) -- `build_bare_cff_bytes`
  - Bare, CID-keyed CFF (CIDFontType0C, ROS-bearing Top DICT) --
    `build_cid_keyed_cff_bytes`
  - Bare CFF wrapped as a CIDFontType0C program with NO ROS (the second,
    "CID used directly as GID" branch of ISO 32000-2 9.7.4.2) --
    `build_noncid_cff_as_cidfonttype0_bytes`
  - Genuine, sfnt-wrapped TrueType (`/FontFile2`) -- `build_truetype_bytes`

Building a genuine OpenType/CFF2, OpenType/CFF (sfnt-wrapped), or classic
Type 1 fixture is not yet implemented here; see the Phase 2 spec's Task
1.1/1.2 for the remaining scope, and Task 1.1's note on the currently
unresolved from-scratch Type 1 fixture generation blocker.
"""

from __future__ import annotations

import io

# A pen-command recipe: a list of (T2CharStringPen method name, args tuple)
# pairs, e.g. [("moveTo", ((0, 0),)), ("lineTo", ((0, 500),))].
GlyphSpec = tuple[int, list[tuple[str, tuple]]]

# A simple 500x500 square outline and a 300x300 triangle outline, reused
# across fixtures below as stand-in glyph shapes. The exact shape is never
# asserted on by tests -- only the width metric matters -- but a real,
# non-degenerate outline (rather than an empty charstring) exercises the
# charstring decompile/draw/recompile path the same way a genuine font would.
#
# These moveTo/lineTo/closePath commands are pen-agnostic: they run
# identically against fontTools.pens.t2CharStringPen.T2CharStringPen (CFF,
# cubic-capable but only straight lines used here) and
# fontTools.pens.ttGlyphPen.TTGlyphPen (TrueType, quadratic-only) alike,
# which is what lets build_truetype_bytes below reuse the same GlyphSpec
# shape as the CFF builders.
SQUARE_500: GlyphSpec = (
    500,
    [
        ("moveTo", ((0, 0),)),
        ("lineTo", ((0, 500),)),
        ("lineTo", ((500, 500),)),
        ("lineTo", ((500, 0),)),
        ("closePath", ()),
    ],
)
TRIANGLE_300: GlyphSpec = (
    300,
    [
        ("moveTo", ((0, 0),)),
        ("lineTo", ((0, 300),)),
        ("lineTo", ((300, 300),)),
        ("closePath", ()),
    ],
)


class _MinimalOTFontStub:
    """
    Stand-in for the fontTools.ttLib.TTFont context that
    fontTools.cffLib.CFFFontSet.compile() expects as its `otFont` argument.
    See pdftl.fonts.cff_binary_utils._MinimalOTFontStub for the same pattern
    used in production code; duplicated here so fixture-building has no
    dependency on pdftl's own source beyond fontTools itself.
    """

    recalcBBoxes = False


def _build_charstrings(order, glyphs, private, global_subrs, fd_select, fd_array):
    from fontTools.cffLib import CharStrings
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    charstrings = CharStrings(None, order, global_subrs, private, fd_select, fd_array)
    for name in order:
        width, commands = glyphs[name]
        pen = T2CharStringPen(width, {})
        for method_name, args in commands:
            getattr(pen, method_name)(*args)
        charstring = pen.getCharString()
        charstring.private = private
        charstring.globalSubrs = global_subrs
        charstrings[name] = charstring
    return charstrings


def build_bare_cff_bytes(glyphs: dict[str, GlyphSpec], font_name: str = "TestFont") -> bytes:
    """
    Builds a genuine, non-CID-keyed bare CFF byte stream (Type1C) -- exactly
    the byte layout a PDF /FontFile3 /Type1C stream contains, no surrounding
    sfnt container.

    `glyphs` maps glyph name -> (advance_width, pen_commands). `.notdef` is
    added automatically at GID 0 if not already present.
    """
    from fontTools.cffLib import CFFFontSet, GlobalSubrsIndex, PrivateDict, TopDict, TopDictIndex

    order = [".notdef", *[name for name in glyphs if name != ".notdef"]]
    if ".notdef" not in glyphs:
        glyphs = {".notdef": (0, []), **glyphs}

    font_set = CFFFontSet()
    font_set.major = 1
    font_set.minor = 0
    font_set.fontNames = [font_name]
    font_set.topDictIndex = TopDictIndex()

    global_subrs = GlobalSubrsIndex()
    font_set.GlobalSubrs = global_subrs
    private = PrivateDict()

    top_dict = TopDict()
    top_dict.charset = order
    top_dict.Private = private
    top_dict.GlobalSubrs = global_subrs
    top_dict.FontName = font_name
    top_dict.FontMatrix = [0.001, 0, 0, 0.001, 0, 0]
    top_dict.CharStrings = _build_charstrings(order, glyphs, private, global_subrs, None, None)

    font_set.topDictIndex.append(top_dict)

    buf = io.BytesIO()
    font_set.compile(buf, otFont=_MinimalOTFontStub())
    return buf.getvalue()


def build_cid_keyed_cff_bytes(
    cid_glyphs: dict[int, GlyphSpec], font_name: str = "TestCIDFont"
) -> bytes:
    """
    Builds a genuine, CID-keyed bare CFF byte stream (CIDFontType0C, a
    ROS-bearing Top DICT) -- exactly the byte layout a PDF
    /FontFile3 /CIDFontType0C stream contains for a CID-keyed CFF program,
    per ISO 32000-2 9.7.4.2's first branch.

    `cid_glyphs` maps CID (int, > 0; CID 0/.notdef is added automatically)
    -> (advance_width, pen_commands). Each CID is written to the CFF's own
    charset as the synthetic name "cid" + CID zero-padded to 5 digits (e.g.
    CID 10 -> "cid00010"), matching fontTools.cffLib's own charset
    convention for CID-keyed fonts -- GID 0 itself is always named
    ".notdef" literally in fontTools' charset representation, never
    "cid00000", matching how a real CID-keyed CFF's charset works.

    A CID-keyed CFF program requires an FDArray/FDSelect (even a trivial,
    single-entry one) in addition to the ROS operator -- both are absent
    from an ordinary, non-CID-keyed CFF, and their absence is exactly what
    would make a genuinely CID-keyed program fail to compile/decompile
    correctly. See fontTools.cffLib.FDArrayIndex/FDSelect.
    """
    from fontTools.cffLib import (
        CFFFontSet,
        FDArrayIndex,
        FDSelect,
        GlobalSubrsIndex,
        PrivateDict,
        TopDict,
        TopDictIndex,
    )

    order = [".notdef"] + [f"cid{cid:05d}" for cid in sorted(cid_glyphs)]
    glyphs: dict[str, GlyphSpec] = {".notdef": (0, [])}
    for cid, spec in cid_glyphs.items():
        glyphs[f"cid{cid:05d}"] = spec

    font_set = CFFFontSet()
    font_set.major = 1
    font_set.minor = 0
    font_set.fontNames = [font_name]
    font_set.topDictIndex = TopDictIndex()

    global_subrs = GlobalSubrsIndex()
    font_set.GlobalSubrs = global_subrs
    private = PrivateDict()

    top_dict = TopDict()
    top_dict.charset = order
    top_dict.Private = private
    top_dict.GlobalSubrs = global_subrs
    top_dict.FontName = font_name
    top_dict.FontMatrix = [0.001, 0, 0, 0.001, 0, 0]
    top_dict.ROS = ("Adobe", "Identity", 0)
    top_dict.CIDCount = len(order)

    fd_array = FDArrayIndex()
    fd = TopDict()
    fd.Private = private
    fd.FontName = f"{font_name}-FD0"
    fd_array.append(fd)
    top_dict.FDArray = fd_array

    fd_select = FDSelect()
    fd_select.format = 0
    fd_select.gidArray = [0] * len(order)
    top_dict.FDSelect = fd_select

    top_dict.CharStrings = _build_charstrings(
        order, glyphs, private, global_subrs, fd_select, fd_array
    )

    font_set.topDictIndex.append(top_dict)

    buf = io.BytesIO()
    font_set.compile(buf, otFont=_MinimalOTFontStub())
    return buf.getvalue()


def build_noncid_cff_as_cidfonttype0_bytes(
    glyphs: dict[str, GlyphSpec], font_name: str = "TestNonCIDFont"
) -> bytes:
    """
    Builds a genuine, ordinary (non-CID-keyed, no ROS) bare CFF byte stream,
    of the kind PDF's /CIDFontType0C may still wrap per ISO 32000-2 9.7.4.2's
    second branch: no ROS in the Top DICT, so the CID is used directly as
    the GID rather than resolved via a charset lookup.

    `glyphs` maps glyph name -> (advance_width, pen_commands), keyed by
    ordinary glyph name (not a CID) since this program is not itself
    CID-keyed -- the CID/GID correspondence is purely positional (GID N is
    the Nth glyph in `glyphs`' iteration order, with `.notdef` forced first).
    """
    return build_bare_cff_bytes(glyphs, font_name=font_name)


def build_truetype_bytes(glyphs: dict[str, GlyphSpec], font_name: str = "TestTTFont") -> bytes:
    """
    Builds a genuine, sfnt-wrapped TrueType font byte stream via
    fontTools.fontBuilder.FontBuilder(isTTF=True) -- exactly the byte
    layout a PDF /FontFile2 stream contains (a full sfnt container with a
    real `glyf`/`loca`/`hmtx` table set), openable as-is via
    fontTools.ttLib.TTFont, matching this codebase's "sfnt" dispatch bucket
    in pdftl.fonts.font_binary_utils.classify_binary_format.

    Unlike the bare-CFF builders above, `glyphs` pen_commands are applied to
    a fontTools.pens.ttGlyphPen.TTGlyphPen (quadratic TrueType outlines)
    rather than a T2CharStringPen -- but since SQUARE_500/TRIANGLE_300 above
    only use moveTo/lineTo/closePath, the exact same GlyphSpec values are
    reusable unmodified across both pen types; only a curveTo-based spec
    would need per-pen-type handling, and none of the fixtures here use one.

    `glyphs` maps glyph name -> (advance_width, pen_commands). `.notdef` is
    added automatically at GID 0 if not already present.
    """
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    order = [".notdef", *[name for name in glyphs if name != ".notdef"]]
    if ".notdef" not in glyphs:
        glyphs = {".notdef": (0, []), **glyphs}

    glyf_table = {}
    metrics = {}
    for name in order:
        width, commands = glyphs[name]
        pen = TTGlyphPen(None)
        for method_name, args in commands:
            getattr(pen, method_name)(*args)
        glyf_table[name] = pen.glyph()
        metrics[name] = (width, 0)

    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({})
    fb.setupGlyf(glyf_table)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(advanceWidthMax=max(w for w, _ in glyphs.values()) or 1)
    fb.setupNameTable({"familyName": font_name, "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()

    buf = io.BytesIO()
    fb.font.save(buf)
    return buf.getvalue()


def build_opentype_cff_bytes(
    glyphs: dict[str, GlyphSpec], font_name: str = "TestOTFFont"
) -> bytes:
    """
    Builds a genuine, sfnt-wrapped CFF font byte stream (OpenType/CFF) via
    fontTools.fontBuilder.FontBuilder(isTTF=False) -- exactly the byte
    layout a PDF /FontFile3 /Subtype /OpenType stream contains when it
    wraps a classic (non-CFF2) CFF table, per ISO 32000-2 Table 126.

    Distinct from both `build_bare_cff_bytes` (no sfnt wrapper at all --
    the /Type1C case) and `build_truetype_bytes` (sfnt-wrapped, but a
    `glyf` table rather than `CFF `). This exercises pdftl's "sfnt"
    classify_binary_format bucket for a font that nonetheless has no
    `glyf` table to squash -- see squash_font_file_vectors's docstring in
    pdftl.fonts.font_binary_utils for why that combination matters: today
    that combination silently drops the requested edit entirely, unlike
    the analogous bare_cff/type1 case, which degrades to a metrics-only
    patch instead.

    `glyphs` maps glyph name -> (advance_width, pen_commands), using the
    same T2CharStringPen-compatible command shape as
    `build_bare_cff_bytes`. `.notdef` is added automatically at GID 0 if
    not already present.
    """
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    order = [".notdef", *[name for name in glyphs if name != ".notdef"]]
    if ".notdef" not in glyphs:
        glyphs = {".notdef": (0, []), **glyphs}

    char_strings_dict = {}
    metrics = {}
    for name in order:
        width, commands = glyphs[name]
        pen = T2CharStringPen(width, {})
        for method_name, args in commands:
            getattr(pen, method_name)(*args)
        # FontBuilder.setupCFF assigns `.private`/`.globalSubrs` directly
        # onto each dict value (see fontTools.fontBuilder.setupCFF's
        # `charString.private = private` line), so it needs the actual
        # T2CharString object here, not its decompiled `.program` list.
        char_strings_dict[name] = pen.getCharString()
        metrics[name] = (width, 0)

    fb = FontBuilder(1000, isTTF=False)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({})
    fb.setupCFF(
        font_name,
        {"FullName": font_name},
        char_strings_dict,
        {},
    )
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(advanceWidthMax=max(w for w, _ in glyphs.values()) or 1)
    fb.setupNameTable({"familyName": font_name, "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()

    import io

    buf = io.BytesIO()
    fb.font.save(buf)
    return buf.getvalue()
