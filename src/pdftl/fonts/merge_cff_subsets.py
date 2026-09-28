# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/fonts/merge_cff_subsets.py

"""Merge embedded Type1C subsets of one font into a single program.

Simple Type 1 fonts embedded as bare CFF (`/FontFile3 /Type1C`) share one
program holding the union of their glyphs, matched by name. Each font
dictionary keeps its widths, ToUnicode and encoding.

A font whose `/Encoding` names a base encoding reaches glyphs by name
alone. One without (no `/Encoding`, or only `/Differences`) reaches them
through the program's built-in encoding; pdfium uses StandardEncoding
instead when the font is nonsymbolic. The merged built-in encoding keeps
every code such a font draws, and a code two fonts need for different
glyphs becomes a `/Differences` entry of the font that lost it.

As for TrueType, a code whose glyph a subset lacks is taken to be unused:
after the merge it may reach another subset's glyph.
"""

from __future__ import annotations

import io
import logging
import struct
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from pdftl.fonts.merge_subsets import MergeStats, _base_name

logger = logging.getLogger(__name__)

_NAMED_ENCODINGS = frozenset(
    {"/StandardEncoding", "/WinAnsiEncoding", "/MacRomanEncoding", "/MacExpertEncoding"}
)
# Private dict entries that only set how widths are stored.
_WIDTH_ENTRIES = frozenset({"Subrs", "defaultWidthX", "nominalWidthX"})


@dataclass
class _Encoding:
    """A font dictionary's /Encoding: a named base, or None for the built-in one."""

    base: str | None
    differences: dict[int, str]
    symbolic: bool = True


@dataclass
class _Member:
    """One embedded program and the font dictionaries drawing with it."""

    stream: Any
    fonts: list = field(default_factory=list)
    encodings: list[_Encoding] = field(default_factory=list)
    patches: list[dict[int, str]] = field(default_factory=list)
    cff: Any = None
    top: Any = None
    builtin: dict[int, str] = field(default_factory=dict)
    glyphs: dict[str, tuple] = field(default_factory=dict)  # name -> (width, program)


def _program(font) -> Any:
    import pikepdf

    descriptor = font.get("/FontDescriptor")
    if not isinstance(descriptor, pikepdf.Dictionary):
        return None
    program = descriptor.get("/FontFile3")
    if isinstance(program, pikepdf.Stream) and program.get("/Subtype") == "/Type1C":
        return program
    return None


def _differences(array) -> dict[int, str] | None:
    """Code -> glyph name from a /Differences array; None if malformed."""
    import pikepdf

    if array is None:
        return {}
    if not isinstance(array, pikepdf.Array):
        return None
    out: dict[int, str] = {}
    code = None
    for item in array:
        if isinstance(item, int):
            code = item
        elif isinstance(item, pikepdf.Name) and code is not None:
            out[code] = str(item)[1:]
            code += 1
        else:
            return None
    return out


def _symbolic(font) -> bool:
    flags = font.FontDescriptor.get("/Flags", 0)
    return isinstance(flags, int) and bool(flags & 4)


def _encoding(font) -> _Encoding | None:
    """The font's encoding, or None if it is not one this merge understands."""
    found = _encoding_entry(font.get("/Encoding"))
    if found is not None:
        found.symbolic = _symbolic(font)
    return found


def _encoding_entry(value) -> _Encoding | None:
    import pikepdf

    if value is None:
        return _Encoding(None, {})
    if isinstance(value, pikepdf.Name):
        return _Encoding(str(value), {}) if str(value) in _NAMED_ENCODINGS else None
    if not isinstance(value, pikepdf.Dictionary):
        return None
    base = value.get("/BaseEncoding")
    if base is not None and str(base) not in _NAMED_ENCODINGS:
        return None
    differences = _differences(value.get("/Differences"))
    if differences is None:
        return None
    return _Encoding(None if base is None else str(base), differences)


def _candidates(pdf) -> dict[str, list[_Member]]:
    """Type1C programs of simple Type 1 fonts, grouped by base font name."""
    import pikepdf

    by_stream: dict[tuple, _Member] = {}
    for obj in pdf.objects:
        if not isinstance(obj, pikepdf.Dictionary) or obj.get("/Subtype") != "/Type1":
            continue
        program = _program(obj)
        if program is not None:
            member = by_stream.setdefault(program.objgen, _Member(program))
            member.fonts.append(obj)
    groups: dict[str, list[_Member]] = defaultdict(list)
    for member in by_stream.values():
        name = str(member.fonts[0].get("/BaseFont", "")).lstrip("/")
        groups[_base_name(name)].append(member)
    return {name: members for name, members in groups.items() if len(members) > 1}


def _damaged() -> tuple[type[Exception], ...]:
    """What fontTools raises on a damaged CFF program."""
    from pdftl.fonts.charstring_work_guard import CharstringBudgetExceeded

    return (
        CharstringBudgetExceeded,
        struct.error,
        AssertionError,
        AttributeError,
        IndexError,
        NotImplementedError,  # a charset or encoding format fontTools lacks
        TypeError,
        UnboundLocalError,  # fontTools, on some malformed charsets
        ValueError,
    )


def _glyph(charstring, private) -> tuple:
    """(advance width, program without its width), subroutines inlined."""
    from fontTools.cffLib.specializer import programToCommands
    from fontTools.cffLib.transforms import desubroutinizeCharString

    from pdftl.fonts.charstring_work_guard import bounded_charstring_interpreter

    with bounded_charstring_interpreter():
        desubroutinizeCharString(charstring)
    program = charstring.program
    commands = programToCommands(program)
    if commands and commands[0][0] == "":
        return private.nominalWidthX + program[0], tuple(program[1:])
    return private.defaultWidthX, tuple(program)


def _builtin(top) -> dict[int, str] | None:
    from pdftl.fonts.font_encoding_tables import _get_base_encoding_table

    encoding = top.Encoding
    if encoding == "StandardEncoding":
        return dict(_get_base_encoding_table("StandardEncoding"))
    if isinstance(encoding, list):
        return {code: name for code, name in enumerate(encoding) if name != ".notdef"}
    return None  # ExpertEncoding


def _parse(member: _Member, data: bytes) -> bool:
    from fontTools.cffLib import CFFFontSet

    cff = CFFFontSet()
    cff.decompile(io.BytesIO(data), None)
    if len(cff.fontNames) != 1:
        return False
    top = cff[cff.fontNames[0]]
    builtin = _builtin(top)
    if hasattr(top, "ROS") or top.CharstringType != 2 or builtin is None:
        return False
    member.cff, member.top, member.builtin = cff, top, builtin
    member.glyphs = {name: _glyph(top.CharStrings[name], top.Private) for name in top.charset}
    return True


def _load(member: _Member) -> bool:
    """Decode the program and every glyph; read each font's encoding."""
    import pikepdf

    member.encodings = [_encoding(font) for font in member.fonts]
    if None in member.encodings:
        return False
    member.patches = [{} for _ in member.fonts]
    try:
        data = member.stream.read_bytes()
    except (pikepdf.PdfError, pikepdf.DataDecodingError):
        return False
    try:
        return _parse(member, data)
    except _damaged():
        return False


def _outline_settings(top) -> str:
    """Everything outside the glyphs that changes how they are drawn."""
    private = {k: v for k, v in top.Private.rawDict.items() if k not in _WIDTH_ENTRIES}
    return repr((top.FontMatrix, top.PaintType, top.StrokeWidth, sorted(private.items())))


def _compatible(members: list[_Member]) -> bool:
    first = _outline_settings(members[0].top)
    return all(_outline_settings(m.top) == first for m in members[1:])


def _union_glyphs(members: list[_Member]) -> dict[str, tuple]:
    """Every member's glyphs, each once; a name drawn differently raises ValueError."""
    union: dict[str, tuple] = {}
    for member in members:
        for name, glyph in member.glyphs.items():
            if union.setdefault(name, glyph) != glyph:
                raise ValueError(f"glyph {name} differs between subsets")
    return union


def _needs(member: _Member, encoding: _Encoding) -> dict[int, str]:
    """Codes this font reaches through the built-in encoding, and their glyphs."""
    if encoding.base is not None:
        return {}
    return {
        code: name
        for code, name in member.builtin.items()
        if code not in encoding.differences and name in member.glyphs
    }


def _renderers_agree(member: _Member) -> bool:
    """Whether pdfium, which reads a nonsymbolic font without a base encoding
    through StandardEncoding, draws the same glyphs as the built-in encoding."""
    from pdftl.fonts.font_encoding_tables import _get_base_encoding_table

    standard = _get_base_encoding_table("StandardEncoding")
    for encoding in member.encodings:
        if not encoding.symbolic and any(
            standard.get(code) != name for code, name in _needs(member, encoding).items()
        ):
            return False
    return True


def _plan_builtin(members: list[_Member]) -> dict[int, str]:
    """The merged built-in encoding; fills each font's patch for codes it lost."""
    merged: dict[int, str] = {}
    for member in members:
        for encoding, patch in zip(member.encodings, member.patches):
            for code, name in sorted(_needs(member, encoding).items()):
                if merged.setdefault(code, name) != name:
                    patch[code] = name
    return merged


def _cff_encoding(merged: dict[int, str]) -> Any:
    from pdftl.fonts.font_encoding_tables import _get_base_encoding_table

    standard = _get_base_encoding_table("StandardEncoding")
    if all(standard.get(code) == name for code, name in merged.items()):
        return "StandardEncoding"
    return [merged.get(code, ".notdef") for code in range(256)]


def _build(members: list[_Member], union: dict[str, tuple], order: list[str], encoding) -> bytes:
    """The merged program, built on the first member's parsed font."""
    from fontTools.cffLib import CharStrings
    from fontTools.misc.psCharStrings import T2CharString

    cff, top = members[0].cff, members[0].top
    private = top.Private
    if hasattr(private, "Subrs"):
        del private.Subrs
    private.rawDict.pop("Subrs", None)
    cff.GlobalSubrs.clear()
    charstrings = CharStrings(None, None, cff.GlobalSubrs, private, None, None)
    for name in order:
        width, program = union[name]
        prefix = [] if width == private.defaultWidthX else [width - private.nominalWidthX]
        charstrings[name] = T2CharString(
            program=[*prefix, *program], private=private, globalSubrs=cff.GlobalSubrs
        )
    top.CharStrings = charstrings
    top.charset = order
    top.Encoding = encoding
    boxes = [m.top.FontBBox for m in members]
    top.FontBBox = [min(b[0] for b in boxes), min(b[1] for b in boxes)] + [
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]
    buf = io.BytesIO()
    cff.compile(buf, SimpleNamespace(recalcBBoxes=False))  # keep the members' extent
    return buf.getvalue()


def _differences_array(differences: dict[int, str]) -> list:
    import pikepdf

    array: list = []
    previous = None
    for code in sorted(differences):
        if previous is None or code != previous + 1:
            array.append(code)
        array.append(pikepdf.Name("/" + differences[code]))
        previous = code
    return array


def _patched(encoding: _Encoding, patch: dict[int, str]) -> Any:
    """A new /Encoding: `encoding` with `patch` added to its Differences."""
    import pikepdf

    array = pikepdf.Array(_differences_array({**encoding.differences, **patch}))
    return pikepdf.Dictionary(Type=pikepdf.Name.Encoding, Differences=array)


def _charset(order: list[str]) -> Any:
    import pikepdf

    return pikepdf.String("".join("/" + name for name in order if name != ".notdef"))


def _extra_bytes(members: list[_Member], order: list[str]) -> int:
    """Bytes the patched encodings and grown /CharSet entries add."""
    extra = 0
    descriptors = {}
    for member in members:
        for font, encoding, patch in zip(member.fonts, member.encodings, member.patches):
            if patch:
                extra += len(_patched(encoding, patch).unparse())
            descriptor = font.FontDescriptor
            descriptors[descriptor.objgen if descriptor.is_indirect else id(font)] = descriptor
    grown = len(bytes(_charset(order)))
    for descriptor in descriptors.values():
        charset = descriptor.get("/CharSet")
        if charset is not None:
            extra += grown - len(bytes(charset))
    return extra


def _install(pdf, members: list[_Member], merged: bytes, order: list[str]) -> None:
    import pikepdf

    program = pdf.make_stream(merged, Subtype=pikepdf.Name.Type1C)
    for member in members:
        for font, encoding, patch in zip(member.fonts, member.encodings, member.patches):
            font.FontDescriptor.FontFile3 = program
            if "/CharSet" in font.FontDescriptor:
                font.FontDescriptor.CharSet = _charset(order)
            if patch:
                font.Encoding = _patched(encoding, patch)


def _merge_group(pdf, name: str, members: list[_Member]) -> int:
    """Merge one group if that makes it smaller; the bytes saved."""
    if not all(_load(m) and _renderers_agree(m) for m in members) or not _compatible(members):
        return 0
    try:
        union = _union_glyphs(members)
        encoding = _cff_encoding(_plan_builtin(members))
        order = [".notdef", *(n for n in union if n != ".notdef")]
        merged = _build(members, union, order, encoding)
    except (ValueError, struct.error) as exc:  # conflicting glyphs, or unencodable values
        logger.debug("merge_font_subsets: %s not merged: %s", name, exc)
        return 0
    before = sum(len(m.stream.read_raw_bytes()) for m in members)
    after = len(zlib.compress(merged, 9)) + _extra_bytes(members, order)
    if after >= before:
        return 0
    _install(pdf, members, merged, order)
    return before - after


def merge_cff_subsets(pdf, stats: MergeStats) -> None:
    """Merge Type1C subsets of the same font in `pdf`, in place, adding to `stats`."""
    for name, members in _candidates(pdf).items():
        saved = _merge_group(pdf, name, members)
        if saved:
            stats.groups += 1
            stats.programs_merged += len(members)
            stats.bytes_saved += saved
