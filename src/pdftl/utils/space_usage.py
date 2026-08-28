# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/space_usage.py

# Portions derived from space_audit.py (Spectra-PDF) by Jason Ulbright.
# Used under the MIT License (see NOTICES.md)

# MIT License

# Copyright (c) 2026 Jason Ulbright

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.


"""Per-category byte breakdown of a PDF."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    import pikepdf

# Report order. `overhead` is always last: it is the residual and claims
# no object directly.
CATEGORY_IDS = (
    "images",
    "fonts",
    "content_streams",
    "annotations",
    "forms",
    "embedded_files",
    "bookmarks",
    "named_destinations",
    "tagged_structure",
    "document_structure",
    "metadata",
    "javascript",
    "other_objects",
    "overhead",
)

_CATEGORY_RANK = {cid: idx for idx, cid in enumerate(CATEGORY_IDS)}

DETAIL_CAP = 50

_OBJ_HEADER_RE = re.compile(rb"(\d+)[\s]+(\d+)[\s]+obj")
_STARTXREF_RE = re.compile(rb"startxref[\s]+(\d+)")
_XREF_KEYWORD_RE = re.compile(rb"[\r\n](xref)[\r\n]")

# An appearance stream is an annotation's rendering, not page content, and
# so is everything nested beneath it -- so this edge makes the whole
# subtree "sticky" rather than just its immediate child.
_STICKY_EDGES = frozenset({"/AP"})

# Categories that hold regardless of where the object hangs, sticky
# subtree included: an embedded font program under an appearance stream
# is still a font program.
_STICKY_OVERRIDE_CATEGORIES = frozenset({"images", "fonts", "embedded_files"})

# Structural/back-pointer edges that say nothing about what the child IS.
_UNNAMED_EDGES = frozenset(
    {
        "/P",
        "/Parent",
        "/Root",
        "/Kids",
        "/Pages",
        "/First",
        "/Last",
        "/Next",
        "/Prev",
        "/Annots",
        "/Fields",
        "/Info",
        "/Dest",
    }
)

# Edges that decide a child's category purely by where it hangs.
_EDGE_CATEGORIES = {
    "/FontFile": "fonts",
    "/FontFile2": "fonts",
    "/FontFile3": "fonts",
    "/ToUnicode": "fonts",
    "/CIDSet": "fonts",
    "/CIDToGIDMap": "fonts",
    "/DescendantFonts": "fonts",
    "/Font": "fonts",
    "/Contents": "content_streams",
    "/CharProcs": "content_streams",
    "/Pattern": "content_streams",
    "/Shading": "content_streams",
    "/Metadata": "metadata",
    "/PieceInfo": "metadata",
    "/Thumb": "metadata",
    "/Info": "metadata",
    "/JavaScript": "javascript",
    "/AA": "javascript",
    "/Dests": "named_destinations",
    "/StructTreeRoot": "tagged_structure",
    "/ParentTree": "tagged_structure",
    "/RoleMap": "tagged_structure",
    "/ClassMap": "tagged_structure",
    "/Outlines": "bookmarks",
    "/AcroForm": "forms",
    "/XFA": "forms",
    "/DR": "forms",
    "/EF": "embedded_files",
    "/Resources": "document_structure",
    "/OCProperties": "document_structure",
    "/PageLabels": "document_structure",
    "/Threads": "document_structure",
}

# What an object declares itself to be (/Type, /Subtype). Checked after
# the keyed edges above, so e.g. a font program reached via /FontFile2 is
# a font wherever it hangs, while a /Widget annotation reached via
# /Annots is a form field rather than a comment.
_TYPE_CATEGORIES = {
    "/Font": "fonts",
    "/FontDescriptor": "fonts",
    "/EmbeddedFile": "embedded_files",
    "/Filespec": "embedded_files",
    "/StructTreeRoot": "tagged_structure",
    "/StructElem": "tagged_structure",
    "/Catalog": "document_structure",
    "/Pages": "document_structure",
    "/Page": "document_structure",
    "/Outlines": "bookmarks",
    "/Metadata": "metadata",
}

# Lazily-cached pikepdf classes. Importing pikepdf inside every hot-path
# helper (as this module previously did) costs a sys.modules lookup plus
# attribute-chain traversal on every one of the millions of calls made
# during attribution. Resolve the classes once and hand back a tuple.
_PIKEPDF_TYPES: tuple | None = None


def _pikepdf_types():
    global _PIKEPDF_TYPES
    if _PIKEPDF_TYPES is None:
        import pikepdf

        _PIKEPDF_TYPES = (pikepdf.Stream, pikepdf.Dictionary, pikepdf.Array, pikepdf.PdfError)
    return _PIKEPDF_TYPES


def _classify(obj: Any):
    """Single type-check pass: returns (kind, dict_or_None).

    `kind` is 'stream', 'dict', 'array', or None. This replaces the old
    pattern where `_stream_or_dict(obj)` (an isinstance-and-unwrap) was
    called once from the category-resolution path and then AGAIN from
    `_push_children` on the very same object -- doubling every isinstance
    check and every `.stream_dict` access across 790K+ objects. Callers
    that need the dict and the descent behavior now get both from one
    call.
    """
    Stream, Dictionary, Array, _ = _pikepdf_types()
    if isinstance(obj, Stream):
        return "stream", obj.stream_dict
    if isinstance(obj, Dictionary):
        return "dict", obj
    if isinstance(obj, Array):
        return "array", None
    return None, None


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    _, _, _, PdfError = _pikepdf_types()
    try:
        return str(value)
    except (TypeError, ValueError, PdfError):
        return ""


def _stream_or_dict(obj: Any):
    Stream, Dictionary, _, _ = _pikepdf_types()
    if isinstance(obj, Stream):
        return obj.stream_dict
    if isinstance(obj, Dictionary):
        return obj
    return None


def _self_declared_category(obj: Any, kind: str, d, cache: dict | None = None) -> str | None:
    try:
        objgen = obj.objgen
    except AttributeError:
        objgen = None

    if cache is not None and objgen is not None and objgen != (0, 0):
        if objgen in cache:
            return cache[objgen]
        cat = _uncached_self_declared_category(obj, kind, d)
        cache[objgen] = cat
        return cat
    return _uncached_self_declared_category(obj, kind, d)


def _uncached_self_declared_category(obj: Any, kind: str, d) -> str | None:
    if d is None:
        return None
    subtype = _as_str(d.get("/Subtype"))
    if kind == "stream":
        if subtype == "/Image":
            return "images"
        if subtype == "/Form":
            return "content_streams"
    kind = _as_str(d.get("/Type"))
    if kind == "/Annot":
        return "forms" if subtype == "/Widget" else "annotations"
    if kind == "/XObject" and subtype == "/Image":
        return "images"
    if kind in _TYPE_CATEGORIES:
        return _TYPE_CATEGORIES[kind]
    if "/ShadingType" in d or "/PatternType" in d or "/FunctionType" in d:
        return "content_streams"
    return None


def _edge_declared_category(key: str, child: Any) -> str | None:
    if key in ("/SMask", "/Mask", "/Alternates"):
        d = _stream_or_dict(child)
        if d is not None and _as_str(d.get("/Subtype")) == "/Image":
            return "images"
        return None
    return _EDGE_CATEGORIES.get(key)


# ---------------------------------------------------------------------
# byte extents
# ---------------------------------------------------------------------


def _extract_stream_extent(data: bytes, pos: int, obj: Any) -> int | None:
    """Find the byte position immediately after `endstream`."""
    stream_kw = data.find(b"stream", pos)
    if stream_kw < 0:
        return None
    start = stream_kw + 6
    if data[start : start + 2] == b"\r\n":
        start += 2
    elif data[start : start + 1] in (b"\n", b"\r"):
        start += 1

    try:
        declared = obj.stream_dict.get("/Length")
        length = int(declared) if declared is not None else None
    except (TypeError, ValueError):
        length = None

    if length is not None and length >= 0:
        tail = data[start + length : start + length + 24]
        if b"endstream" in tail:
            return start + length + tail.index(b"endstream") + 9

    idx = data.find(b"endstream", start)
    return idx + 9 if idx >= 0 else None


def _object_extent(data: bytes, offset: int, obj_num: int, obj: Any) -> int:
    """Stored length of a top-level object, header through `endobj`."""
    Stream, _, _, _ = _pikepdf_types()
    if offset <= 0 or offset >= len(data):
        return 0
    match = _OBJ_HEADER_RE.match(data, offset)
    if not match or int(match.group(1)) != obj_num:
        return 0

    pos = match.end()
    if isinstance(obj, Stream):
        pos_after_stream = _extract_stream_extent(data, pos, obj)
        if pos_after_stream is None:
            return 0
        pos = pos_after_stream

    close = data.find(b"endobj", pos)
    if close < 0:
        return 0
    return close + 6 - offset


def _merge_intervals(intervals: list) -> list:
    out: list = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if out and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return out


def _subtract_intervals(base: list, cut: list) -> list:
    out: list = []
    cuts = _merge_intervals(cut)
    i = 0
    for start, end in base:
        pos = start
        while i < len(cuts) and cuts[i][1] <= pos:
            i += 1
        j = i
        while j < len(cuts) and cuts[j][0] < end:
            if cuts[j][0] > pos:
                out.append([pos, min(cuts[j][0], end)])
            pos = max(pos, cuts[j][1])
            if pos >= end:
                break
            j += 1
        if pos < end:
            out.append([pos, end])
    return _merge_intervals(out)


def _interval_length(intervals: list) -> int:
    return sum(end - start for start, end in intervals)


# ---------------------------------------------------------------------
# attribution walk
# ---------------------------------------------------------------------


class _Attribution:
    """Which category owns each object."""

    def __init__(self) -> None:
        self.category: dict = {}
        self.pages: dict = {}
        self.edge_name: dict = {}

    def add_page(self, objgen, page) -> None:
        if page is not None:
            self.pages.setdefault(objgen, set()).add(page)

    def claim(self, objgen, category: str | None, edge_name: str) -> bool:
        current = self.category.get(objgen)
        if current is not None and _CATEGORY_RANK[current] <= _CATEGORY_RANK[category]:
            return False
        self.category[objgen] = category
        if edge_name and objgen not in self.edge_name:
            self.edge_name[objgen] = edge_name
        return True


def _push_children(
    obj: Any,
    kind: str,
    d,
    category: str | None,
    page: Any,
    edge_key: str,
    sticky: bool,
    stack: list,
):
    _, _, _, PdfError = _pikepdf_types()
    child_sticky = sticky or edge_key in _STICKY_EDGES
    push = stack.append
    try:
        if kind in ("dict", "stream"):
            for child_key in d.keys():
                try:
                    push((d[child_key], category, page, str(child_key), child_sticky))
                except (KeyError, AttributeError, PdfError):
                    continue
        elif kind == "array":
            for item in obj:
                push((item, category, page, edge_key, child_sticky))
    except (AttributeError, PdfError):
        pass


def _resolve_object_category(
    edge_key: str,
    obj: Any,
    kind: str,
    d,
    inherited: str | None,
    sticky: bool,
    cache: dict | None = None,
) -> str | None:
    declared = _edge_declared_category(edge_key, obj) or _self_declared_category(
        obj, kind, d, cache
    )
    if sticky and declared not in _STICKY_OVERRIDE_CATEGORIES:
        declared = None
    return declared or inherited


def _process_attribution_step(
    item: tuple,
    page_of_objgen: dict,
    found: _Attribution,
    stack: list,
    cache: dict | None = None,
) -> None:
    obj, inherited, page, edge_key, sticky = item
    try:
        objgen = obj.objgen
    except AttributeError:
        objgen = None
    is_indirect = objgen is not None and objgen != (0, 0)

    kind, d = _classify(obj)
    category = _resolve_object_category(edge_key, obj, kind, d, inherited, sticky, cache)
    if category is None and is_indirect:
        category = "other_objects"

    if is_indirect:
        page = page_of_objgen.get(objgen, page)
        found.add_page(objgen, page)
        edge_for_detail = "" if edge_key in _UNNAMED_EDGES else edge_key
        if not found.claim(objgen, category, edge_for_detail):
            return

    _push_children(obj, kind, d, category, page, edge_key, sticky, stack)


def _attribute_objects(pdf: pikepdf.Pdf) -> _Attribution:
    found = _Attribution()
    page_of_objgen = {}
    category_cache: dict = {}
    for index, page in enumerate(pdf.pages):
        try:
            page_of_objgen[page.obj.objgen] = index + 1
        except AttributeError:
            continue

    stack = [(pdf.trailer, None, None, "", False)]
    guard = 0
    guard_limit = 4_000_000
    while stack and guard < guard_limit:
        guard += 1
        item = stack.pop()
        _process_attribution_step(item, page_of_objgen, found, stack, category_cache)
    return found


def _is_xref_stream(obj: Any) -> bool:
    d = _stream_or_dict(obj)
    return d is not None and _as_str(d.get("/Type")) == "/XRef"


def _is_object_stream(obj: Any) -> bool:
    d = _stream_or_dict(obj)
    return d is not None and _as_str(d.get("/Type")) == "/ObjStm"


def _linearization_objgens(pdf: pikepdf.Pdf, offset_by_objgen: dict) -> set:
    _, _, _, PdfError = _pikepdf_types()
    out: set = set()
    for objgen, _offset in offset_by_objgen.items():
        try:
            obj = pdf.get_object(objgen[0], objgen[1])
        except (KeyError, PdfError):
            continue
        d = _stream_or_dict(obj)
        if d is None or "/Linearized" not in d:
            continue
        out.add(objgen)
        hint = d.get("/H")
        try:
            hint_starts = {int(hint[i]) for i in range(0, len(hint), 2)}
        except (TypeError, ValueError, KeyError, IndexError):
            hint_starts = set()
        for other_objgen, other_offset in offset_by_objgen.items():
            if other_offset in hint_starts:
                out.add(other_objgen)
    return out


def _xref_section_ranges(data: bytes) -> list:
    starts = set()
    for m in _STARTXREF_RE.finditer(data):
        try:
            starts.add(int(m.group(1)))
        except ValueError:
            continue
    for m in _XREF_KEYWORD_RE.finditer(data):
        starts.add(m.start(1))
    out = []
    for start in starts:
        if start <= 0 or start >= len(data):
            continue
        end = data.find(b"%%EOF", start)
        if end < 0:
            continue
        out.append([start, end + 5])
    return _merge_intervals(out)


def _filter_label(obj: Any) -> str:
    _, _, Array, _ = _pikepdf_types()
    d = _stream_or_dict(obj)
    if d is None:
        return ""
    value = d.get("/Filter")
    if value is None:
        label = _as_str(d.get("/Subtype")) or _as_str(d.get("/Type"))
        return label[1:] if label.startswith("/") else label
    if isinstance(value, Array):
        return ",".join(_strip_slash(_as_str(v)) for v in value)
    return _strip_slash(_as_str(value))


def _strip_slash(label: str) -> str:
    return label[1:] if label.startswith("/") else label


# ---------------------------------------------------------------------
# helper routines for analyze_space_usage
# ---------------------------------------------------------------------


def _collect_xref_and_objects(pdf: pikepdf.Pdf, data: bytes):
    import pikepdf

    xref_table = pdf.get_xref_table()
    offset_by_objgen: dict = {}
    members_by_stream: dict = {}
    for objgen, entry in xref_table.items():
        if entry.type == 1:
            offset_by_objgen[objgen] = entry.offset
        elif entry.type == 2:
            members_by_stream.setdefault((entry.obj_stream_number, 0), []).append(objgen)

    objects_by_objgen: dict = {}
    extent_by_objgen: dict = {}
    unmeasured = 0

    for objgen, offset in offset_by_objgen.items():
        try:
            obj = pdf.get_object(objgen[0], objgen[1])
        except (KeyError, pikepdf.PdfError):
            obj = None
        objects_by_objgen[objgen] = obj

        n = _object_extent(data, offset, objgen[0], obj)
        if n <= 0:
            unmeasured += 1
        else:
            extent_by_objgen[objgen] = n

    return (
        xref_table,
        offset_by_objgen,
        members_by_stream,
        objects_by_objgen,
        extent_by_objgen,
        unmeasured,
    )


def _charge_top_level_objects(
    extent_by_objgen,
    offset_by_objgen,
    objects_by_objgen,
    linearization_objgens,
    found,
    record,
):
    objstm_intervals = []
    layout_intervals = []
    charged_intervals = []
    unreferenced_intervals = []
    unref_count = 0

    for objgen, nbytes in extent_by_objgen.items():
        offset = offset_by_objgen[objgen]
        obj = objects_by_objgen.get(objgen)
        if _is_object_stream(obj):
            objstm_intervals.append([offset, offset + nbytes])
            continue
        if _is_xref_stream(obj) or objgen in linearization_objgens:
            layout_intervals.append([offset, offset + nbytes])
            continue
        category = found.category.get(objgen)
        if category is None:
            unref_count += 1
            unreferenced_intervals.append([offset, offset + nbytes])
            continue
        charged_intervals.append([offset, offset + nbytes])
        record(objgen, category, nbytes)

    return (
        objstm_intervals,
        layout_intervals,
        charged_intervals,
        unreferenced_intervals,
        unref_count,
    )


def _compute_stream_member_weights(pdf: pikepdf.Pdf, member_objgens: list) -> tuple[list, int]:
    _, _, _, PdfError = _pikepdf_types()
    weights = []
    for objgen in member_objgens:
        try:
            member_obj = pdf.get_object(objgen[0], objgen[1])
            weights.append((objgen, len(member_obj.unparse(resolved=True))))
        except (KeyError, AttributeError, PdfError):
            weights.append((objgen, 0))

    total_weight = sum(w for _, w in weights)
    return weights, total_weight


def _charge_stream_members(
    weights: list,
    extent: int,
    total_weight: int,
    pdf: pikepdf.Pdf,
    found: _Attribution,
    objects_by_objgen: dict,
    record: Any,
) -> tuple[int, int]:
    _, _, _, PdfError = _pikepdf_types()
    unref_count = 0
    unref_packed_bytes = 0

    for objgen, weight in weights:
        allocated = extent * weight // total_weight
        category = found.category.get(objgen)
        if category is None:
            unref_count += 1
            unref_packed_bytes += allocated
            continue
        if objgen not in objects_by_objgen:
            try:
                objects_by_objgen[objgen] = pdf.get_object(objgen[0], objgen[1])
            except (KeyError, PdfError):
                objects_by_objgen[objgen] = None
        record(objgen, category, allocated)

    return unref_count, unref_packed_bytes


def _charge_object_stream_members(
    members_by_stream: dict,
    extent_by_objgen: dict,
    pdf: pikepdf.Pdf,
    found: _Attribution,
    objects_by_objgen: dict,
    record: Any,
) -> tuple[int, int]:
    unref_count = 0
    unref_packed_bytes = 0

    for stream_objgen, member_objgens in members_by_stream.items():
        extent = extent_by_objgen.get(stream_objgen)
        if not extent:
            continue

        weights, total_weight = _compute_stream_member_weights(pdf, member_objgens)
        if total_weight <= 0:
            continue

        u_count, u_bytes = _charge_stream_members(
            weights, extent, total_weight, pdf, found, objects_by_objgen, record
        )
        unref_count += u_count
        unref_packed_bytes += u_bytes

    return unref_count, unref_packed_bytes


def _build_overhead_detail(
    data,
    size,
    layout_intervals,
    charged_intervals,
    objstm_intervals,
    unref_intervals_top,
    unref_packed_bytes,
    overhead_bytes,
):
    occupied = _merge_intervals(charged_intervals + objstm_intervals)
    xref_intervals = _subtract_intervals(
        _merge_intervals(_xref_section_ranges(data) + layout_intervals), occupied
    )
    unref_intervals = _subtract_intervals(_merge_intervals(unref_intervals_top), xref_intervals)

    xref_bytes = _interval_length(xref_intervals)
    unref_bytes = _interval_length(unref_intervals) + unref_packed_bytes
    structural_bytes = overhead_bytes - xref_bytes - unref_bytes

    return [
        {"kind": "cross_reference", "bytes": xref_bytes},
        {"kind": "unreferenced", "bytes": unref_bytes},
        {"kind": "structural", "bytes": structural_bytes},
    ]


# ---------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------


def analyze_space_usage(pdf: pikepdf.Pdf, file_bytes: bytes) -> dict:
    """Attribute every byte of `file_bytes` to exactly one category."""
    data = file_bytes
    size = len(data)

    found = _attribute_objects(pdf)

    (
        xref_table,
        offset_by_objgen,
        members_by_stream,
        objects_by_objgen,
        extent_by_objgen,
        unmeasured,
    ) = _collect_xref_and_objects(pdf, data)

    linearization_objgens = _linearization_objgens(pdf, offset_by_objgen)

    charged_bytes: dict = {cid: 0 for cid in CATEGORY_IDS}
    charged_count: dict = {cid: 0 for cid in CATEGORY_IDS}
    detail_rows: dict = {cid: [] for cid in CATEGORY_IDS}

    def record(objgen, category: str, nbytes: int) -> None:
        charged_bytes[category] += nbytes
        charged_count[category] += 1
        detail_rows[category].append(
            {
                "obj_id": objgen[0],
                "pages": sorted(found.pages.get(objgen, ())),
                "name": found.edge_name.get(objgen, ""),
                "type": _filter_label(objects_by_objgen.get(objgen)),
                "bytes": nbytes,
            }
        )

    (
        objstm_intervals,
        layout_intervals,
        charged_intervals,
        unref_intervals_top,
        unref_count_top,
    ) = _charge_top_level_objects(
        extent_by_objgen,
        offset_by_objgen,
        objects_by_objgen,
        linearization_objgens,
        found,
        record,
    )

    unref_count_members, unref_packed_bytes = _charge_object_stream_members(
        members_by_stream,
        extent_by_objgen,
        pdf,
        found,
        objects_by_objgen,
        record,
    )

    unreferenced_object_count = unref_count_top + unref_count_members
    attributed_total = sum(charged_bytes[cid] for cid in CATEGORY_IDS)
    overhead_bytes = size - attributed_total
    charged_bytes["overhead"] = overhead_bytes

    overhead_detail = _build_overhead_detail(
        data,
        size,
        layout_intervals,
        charged_intervals,
        objstm_intervals,
        unref_intervals_top,
        unref_packed_bytes,
        overhead_bytes,
    )

    categories = []
    for cid in CATEGORY_IDS:
        nbytes = charged_bytes[cid]
        row: dict = {
            "id": cid,
            "bytes": nbytes,
            "share": (nbytes / size) if size else 0.0,
            "objects": charged_count[cid],
        }
        if cid == "overhead":
            row["residual"] = True
            row["objects"] = unreferenced_object_count
            row["detail"] = overhead_detail
        else:
            rows = sorted(detail_rows[cid], key=lambda r: -r["bytes"])
            row["detail"] = rows[:DETAIL_CAP]
            if len(rows) > DETAIL_CAP:
                row["detail_truncated"] = True
        categories.append(row)

    return {
        "file_size": size,
        "total": sum(row["bytes"] for row in categories),
        "objects": len(xref_table),
        "unmeasured_objects": unmeasured,
        "categories": categories,
    }
