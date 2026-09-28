# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/fonts/merge_subsets.py

"""Merge embedded TrueType subsets of one font into a single program.

Producers that write a subset per page or per run (browsers, office
suites) embed the same font many times over. The subsets can share one
program holding the union of their glyphs, each stored once:

* CID-keyed TrueType fonts drawn through Identity-H or Identity-V keep
  their own CIDs, widths and ToUnicode; a CIDToGIDMap stream maps their
  CIDs into the union.
* Simple TrueType fonts reach glyphs through the program's cmap, so they
  are merged only if every subset maps each code to the same glyph, and
  glyph names are kept (and must agree) when a subset carries them.

Content streams are untouched. A group is merged only if its programs
agree on everything outside the glyphs (units per em and the hinting
programs). Type1C subsets are merged by merge_cff_subsets.
"""

from __future__ import annotations

import io
import logging
import struct
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_IDENTITY_ENCODINGS = ("/Identity-H", "/Identity-V")
_SHARED_TABLES = ("fpgm", "prep", "cvt ")
_HEAD_BBOX = ("xMin", "yMin", "xMax", "yMax")
_MAXP_MAX = (
    "maxPoints",
    "maxContours",
    "maxCompositePoints",
    "maxCompositeContours",
    "maxComponentElements",
    "maxComponentDepth",
    "maxSizeOfInstructions",
    "maxZones",
    "maxTwilightPoints",
    "maxStorage",
    "maxFunctionDefs",
    "maxInstructionDefs",
    "maxStackElements",
)
_HHEA_MAX = ("advanceWidthMax", "xMaxExtent")
_HHEA_MIN = ("minLeftSideBearing", "minRightSideBearing")


@dataclass
class _Member:
    """One embedded program, and the CIDFontType2 dictionaries drawing with it."""

    stream: Any
    simple: bool = False  # a simple /TrueType font, not a CIDFontType2
    fonts: list = field(default_factory=list)
    font: Any = None  # fontTools TTFont
    gid_map: dict[int, int] = field(default_factory=dict)  # subset GID -> union GID


@dataclass
class MergeStats:
    groups: int = 0
    programs_merged: int = 0
    bytes_saved: int = 0


def _base_name(name: str) -> str:
    """The font name without a six-letter subset tag."""
    prefix, _, rest = name.partition("+")
    return rest if rest and len(prefix) == 6 and prefix.isalpha() and prefix.isupper() else name


def _program(font) -> Any:
    import pikepdf

    descriptor = font.get("/FontDescriptor")
    program = descriptor.get("/FontFile2") if isinstance(descriptor, pikepdf.Dictionary) else None
    return program if isinstance(program, pikepdf.Stream) else None


def _identity_cid_font(font) -> Any:
    """The CIDFontType2 an Identity-H/V Type0 font draws with directly, or None."""
    import pikepdf

    if font.get("/Subtype") != "/Type0" or font.get("/Encoding") not in _IDENTITY_ENCODINGS:
        return None
    descendants = font.get("/DescendantFonts")
    if not isinstance(descendants, pikepdf.Array) or len(descendants) != 1:
        return None
    cid_font = descendants[0]
    if cid_font.get("/Subtype") != "/CIDFontType2":
        return None
    if cid_font.get("/CIDToGIDMap", pikepdf.Name.Identity) != pikepdf.Name.Identity:
        return None
    return cid_font


def _candidates(pdf) -> dict[tuple, list[_Member]]:
    """TrueType programs grouped by (simple or CID, base font name)."""
    import pikepdf

    by_stream: dict[tuple, _Member] = {}
    for obj in pdf.objects:
        if not isinstance(obj, pikepdf.Dictionary):
            continue
        simple = obj.get("/Subtype") == "/TrueType"
        font = obj if simple else _identity_cid_font(obj)
        program = None if font is None else _program(font)
        if program is not None:
            # A program drawn both ways is merged per kind; the other kind keeps the original.
            member = by_stream.setdefault((program.objgen, simple), _Member(program, simple))
            member.fonts.append(font)
    groups: dict[tuple, list[_Member]] = defaultdict(list)
    for member in by_stream.values():
        name = str(member.fonts[0].get("/BaseFont", "")).lstrip("/")
        groups[(member.simple, _base_name(name))].append(member)
    return {key: members for key, members in groups.items() if len(members) > 1}


def _damaged() -> tuple[type[Exception], ...]:
    """What fontTools raises on a damaged program."""
    from fontTools.ttLib import TTLibError

    return (
        TTLibError,
        struct.error,
        AssertionError,
        AttributeError,
        IndexError,
        KeyError,
        ValueError,
    )


def _load(member: _Member) -> bool:
    """Load the program if fontTools can decode and rewrite all of it."""
    import pikepdf
    from fontTools.ttLib import TTFont

    try:
        data = member.stream.read_bytes()
    except (pikepdf.PdfError, pikepdf.DataDecodingError):
        return False
    try:
        trial = TTFont(io.BytesIO(data))
        trial.ensureDecompiled()
        trial.save(io.BytesIO())  # saving recalculates tables, so the trial copy is discarded
    except _damaged():
        return False
    font = TTFont(io.BytesIO(data))
    member.font = font
    return all(tag in font for tag in ("head", "glyf", "hmtx"))


def _cmap_layout(font) -> set | None:
    if "cmap" not in font:
        return None
    return {(t.platformID, t.platEncID) for t in font["cmap"].tables}


def _compatible(members: list[_Member]) -> bool:
    first = members[0].font
    for member in members[1:]:
        font = member.font
        if font["head"].unitsPerEm != first["head"].unitsPerEm:
            return False
        if member.simple and (
            _cmap_layout(font) is None or _cmap_layout(font) != _cmap_layout(first)
        ):
            return False
        for tag in _SHARED_TABLES:
            if (tag in font) != (tag in first):
                return False
            if tag in font and font.getTableData(tag) != first.getTableData(tag):
                return False
    return True


def _widen_maxp_hhea(union, members: list[_Member]) -> None:
    """Union's maxp/hhea max*/min* fields over every member, not just member 0's."""
    maxp, hhea = union["maxp"], union["hhea"]
    for attr in _MAXP_MAX:
        setattr(maxp, attr, max(getattr(m.font["maxp"], attr) for m in members))
    for attr in _HHEA_MAX:
        setattr(hhea, attr, max(getattr(m.font["hhea"], attr) for m in members))
    for attr in _HHEA_MIN:
        setattr(hhea, attr, min(getattr(m.font["hhea"], attr) for m in members))


def _glyph_key(font, name: str, cache: dict) -> tuple:
    """A key equal for glyphs that draw the same thing with the same metrics."""
    if name in cache:
        return cache[name]
    glyph = font["glyf"][name]
    metrics = font["hmtx"][name]
    program = bytes(glyph.program.getBytecode()) if hasattr(glyph, "program") else b""
    if glyph.isComposite():
        parts = tuple(
            (
                _glyph_key(font, c.glyphName, cache),
                c.flags & ~0x0020,  # MORE_COMPONENTS depends only on position in the list
                getattr(c, "x", None),
                getattr(c, "y", None),
                tuple(map(tuple, c.transform)) if hasattr(c, "transform") else None,
            )
            for c in glyph.components
        )
        key = ("composite", parts, program, metrics)
    elif glyph.numberOfContours == 0:
        key = ("empty", metrics)
    else:
        key = (
            "simple",
            tuple(glyph.coordinates),
            tuple(glyph.endPtsOfContours),
            tuple(f & 1 for f in glyph.flags),
            program,
            metrics,
        )
    cache[name] = key
    return key


def _keeps_names(members: list[_Member]) -> bool:
    return any("post" in m.font and m.font["post"].formatType == 2.0 for m in members)


def _union_name(key, name, order, simple_names: bool, taken: dict) -> str:
    if not order:
        return ".notdef"
    if not simple_names:
        return f"g{len(order)}"
    if taken.get(name, key) != key:
        raise ValueError(f"glyph name {name} names different glyphs")
    taken[name] = key
    return name


def _union_cmap(members: list[_Member], renamed_by_member: list[dict]) -> Any:
    """One cmap whose subtables map every member's codes; conflicts raise ValueError."""
    from fontTools.ttLib import newTable
    from fontTools.ttLib.tables._c_m_a_p import CmapSubtable

    merged: dict[tuple, dict] = {}
    for member, renamed in zip(members, renamed_by_member):
        for table in member.font["cmap"].tables:
            mapping = merged.setdefault((table.platformID, table.platEncID, table.format), {})
            for code, name in table.cmap.items():
                glyph = renamed[name]
                if mapping.setdefault(code, glyph) != glyph:
                    raise ValueError(f"code {code} maps to different glyphs")
    cmap = newTable("cmap")
    cmap.tableVersion = 0
    cmap.tables = []
    for (platform, encoding, fmt), mapping in sorted(merged.items()):
        table = CmapSubtable.newSubtable(fmt)
        table.platformID, table.platEncID, table.language = platform, encoding, 0
        table.cmap = mapping
        cmap.tables.append(table)
    return cmap


@dataclass
class _Glyphs:
    """The union's glyphs so far, each stored once."""

    keep_names: bool
    order: list[str] = field(default_factory=list)
    glyphs: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, tuple] = field(default_factory=dict)
    index: dict[tuple, str] = field(default_factory=dict)  # glyph key -> union name
    position: dict[str, int] = field(default_factory=dict)  # union name -> union GID
    taken: dict[str, tuple] = field(default_factory=dict)  # kept name -> glyph key

    def add(self, member: _Member) -> dict[str, str]:
        """Add a member's glyphs and fill its gid_map; its glyph names in the union."""
        import copy

        font = member.font
        cache: dict = {}
        renamed: dict[str, str] = {}
        created: list[str] = []
        for gid, name in enumerate(font.getGlyphOrder()):
            key = _glyph_key(font, name, cache)
            if key not in self.index:
                new_name = _union_name(key, name, self.order, self.keep_names, self.taken)
                self.index[key] = new_name
                self.position[new_name] = len(self.order)
                self.order.append(new_name)
                created.append(new_name)
                self.glyphs[new_name] = copy.deepcopy(font["glyf"][name])
                self.metrics[new_name] = font["hmtx"][name]
            if self.keep_names and self.index[key] != name and gid:
                raise ValueError(f"glyph {name} is named {self.index[key]} in another subset")
            renamed[name] = self.index[key]
            member.gid_map[gid] = self.position[self.index[key]]
        # Only glyphs this subset added still name its components.
        for new_name in created:
            glyph = self.glyphs[new_name]
            for component in glyph.components if glyph.isComposite() else ():
                component.glyphName = renamed[component.glyphName]
        return renamed


def _build_union(members: list[_Member]) -> bytes:
    """The merged program; fills each member's gid_map."""
    import copy

    simple = members[0].simple
    union_glyphs = _Glyphs(simple and _keeps_names(members))
    renamed_by_member = [union_glyphs.add(member) for member in members]
    order = union_glyphs.order
    union = copy.deepcopy(members[0].font)
    union.setGlyphOrder(order)
    union["glyf"].glyphs = union_glyphs.glyphs
    union["glyf"].glyphOrder = order
    union["hmtx"].metrics = union_glyphs.metrics
    union["maxp"].numGlyphs = len(order)
    _widen_maxp_hhea(union, members)
    if "post" in union:
        union["post"].formatType = 2.0 if union_glyphs.keep_names else 3.0
    # These tables index the old glyph order; a simple font gets a merged cmap.
    for tag in ("cmap", "hdmx", "VDMX", "LTSH", "kern", "GSUB", "GPOS", "GDEF", "vmtx", "vhea"):
        if tag in union:
            del union[tag]
    if simple:
        union["cmap"] = _union_cmap(members, renamed_by_member)
    head = union["head"]
    for attr, pick in zip(_HEAD_BBOX, (min, min, max, max)):
        setattr(head, attr, pick(getattr(m.font["head"], attr) for m in members))
    head.modified = max(m.font["head"].modified for m in members)
    buf = io.BytesIO()
    union.recalcBBoxes = False  # renderers use the font-wide bbox; keep the originals' extent
    union.recalcTimestamp = False  # keep `modified` reproducible instead of the save time
    union.save(buf)
    return buf.getvalue()


def _cid_to_gid(gid_map: dict[int, int]) -> bytes:
    size = max(gid_map) + 1
    return b"".join(gid_map.get(cid, 0).to_bytes(2, "big") for cid in range(size))


def _install(pdf, members: list[_Member], merged: bytes, maps: list[bytes]) -> None:
    """Point every member's fonts at the merged program (and its CIDToGIDMap)."""
    import pikepdf

    program = pdf.make_stream(merged, Length1=len(merged))
    for member, packed in zip(members, maps):
        cid_to_gid = None
        if not member.simple:
            cid_to_gid = pdf.make_stream(packed, Filter=pikepdf.Name.FlateDecode)
        for font in member.fonts:
            font.FontDescriptor.FontFile2 = program
            if cid_to_gid is not None:
                font.CIDToGIDMap = cid_to_gid


def _merge_group(pdf, name: str, members: list[_Member]) -> int:
    """Merge one group if that makes it smaller; the bytes saved."""
    if not all(_load(m) for m in members) or not _compatible(members):
        return 0
    try:
        merged = _build_union(members)
    except (ValueError, struct.error) as exc:  # conflicting glyphs, or too many for one font
        logger.debug("merge_font_subsets: %s not merged: %s", name, exc)
        return 0
    before = sum(len(m.stream.read_raw_bytes()) for m in members)
    maps = [b"" if m.simple else zlib.compress(_cid_to_gid(m.gid_map), 9) for m in members]
    after = len(zlib.compress(merged, 9)) + sum(len(b) for b in maps)
    if after >= before:
        return 0
    _install(pdf, members, merged, maps)
    return before - after


def merge_font_subsets(pdf) -> MergeStats:
    """Merge TrueType and Type1C subsets of the same font in `pdf`, in place."""
    from pdftl.fonts.merge_cff_subsets import merge_cff_subsets

    stats = MergeStats()
    for (_simple, name), members in _candidates(pdf).items():
        saved = _merge_group(pdf, name, members)
        if saved:
            stats.groups += 1
            stats.programs_merged += len(members)
            stats.bytes_saved += saved
    merge_cff_subsets(pdf, stats)
    return stats
