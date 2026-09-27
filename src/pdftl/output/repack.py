# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/output/repack.py

"""Merge the object streams of a file qpdf has written into as few as possible.

qpdf puts at most 100 objects in each object stream, and deflate does
markedly better on one large stream (about a fifth smaller on tagged
documents). Objects outside object streams are copied byte for byte. The
result is compared with the original object by object, and used only if
every object matches and the file is smaller.
"""

from __future__ import annotations

import io
import logging
import os
import re
import shutil
import tempfile
import zlib
from dataclasses import dataclass

from pdftl.output.xref_stream import Xref, encode_xref, read_xref

logger = logging.getLogger(__name__)

_CONTAINER = object()  # an object stream or xref stream: rebuilt, not compared
MAX_OBJECTS = 10000  # per object stream: a viewer decodes a whole stream to reach one object
_OBJSTM_HEADER = re.compile(rb"(\d+) 0 obj\n<<(.*?)>>\nstream\n", re.S)
_LOOSE = re.compile(rb"(\d+) 0 obj\n(.*)\nendobj\n\Z", re.S)
_KEY = {
    key: re.compile(rb"/" + key + rb" (\d+)") for key in (b"N", b"First", b"Length")
}  # fmt: skip


def repack_file(path: str) -> bool:
    """Repack the qpdf-written file at `path` in place; whether it changed."""
    with open(path, "rb") as f:
        data = f.read()
    packed = repack_bytes(data)
    if packed is data:
        return False
    fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=os.path.dirname(os.path.abspath(path)))
    with os.fdopen(fd, "wb") as f:
        f.write(packed)
    shutil.copymode(path, tmp)
    os.replace(tmp, path)
    return True


def repack_bytes(data: bytes) -> bytes:
    """`data` with its object streams merged, or `data` itself if that is not safe or smaller."""
    packed = _repack(data)
    if packed is None or len(packed) >= len(data) or not same_objects(data, packed):
        return data
    logger.debug("repack: %d -> %d bytes", len(data), len(packed))
    return packed


@dataclass
class _Layout:
    """Where a qpdf-written file keeps its objects."""

    xref: Xref
    rows: dict[int, list[int]]  # object number -> xref row
    members: list[int]  # objects in object streams, in file order
    containers: list[int]  # the object streams
    ends: dict[int, int]  # offset of a top-level object -> offset after it
    loose: dict[int, bytes]  # top-level objects that may move into an object stream


def _layout(data: bytes) -> _Layout | None:
    xref = read_xref(io.BytesIO(data), len(data))
    if xref is None or b"/Prev" in xref.header or b"/Linearized" in data[:1024]:
        return None
    rows = dict(zip(xref.numbers, xref.rows))
    members = sorted((r[1], r[2], n) for n, r in rows.items() if r[0] == 2)
    containers = sorted({container for container, _, _ in members})
    offsets = sorted(r[1] for r in rows.values() if r[0] == 1)
    ends = dict(zip(offsets, offsets[1:] + [xref.offset]))
    # qpdf keeps signature dictionaries out of object streams; nothing else forbids them.
    loose = {
        n: body
        for n, r in rows.items()
        if r[0] == 1 and n not in containers and n != xref.num
        for body in [_loose_object(data[r[1] : ends[r[1]]], n)]
        if body is not None
    }
    return _Layout(xref, rows, [n for _, _, n in members], containers, ends, loose)


def _objects(data: bytes, layout: _Layout) -> dict[int, bytes] | None:
    """Every object to be packed, or None if an object stream cannot be read."""
    objects = dict(layout.loose)
    for container in layout.containers:
        found = _object_stream_members(data, layout.rows.get(container), container)
        if found is None:
            return None
        objects.update(found)
    return objects if all(n in objects for n in layout.members) else None


def _repack(data: bytes) -> bytes | None:
    layout = _layout(data)
    if layout is None or not layout.containers:
        return None
    if len(layout.containers) < 2 and not layout.loose:
        return None
    objects = _objects(data, layout)
    if objects is None:
        return None
    rows = layout.rows
    order = layout.members + sorted(layout.loose, key=lambda n: rows[n][1])
    chunks = [order[i : i + MAX_OBJECTS] for i in range(0, len(order), MAX_OBJECTS)]
    if len(chunks) > len(layout.containers):
        return None
    return _write(data, layout, objects, chunks)


def _write(data: bytes, layout: _Layout, objects: dict[int, bytes], chunks) -> bytes:
    xref, rows, containers = layout.xref, layout.rows, layout.containers
    reused, freed = containers[: len(chunks)], containers[len(chunks) :]
    copied = sorted(
        (r[1], n)
        for n, r in rows.items()
        if r[0] == 1 and n not in containers and n != xref.num and n not in layout.loose
    )
    out = bytearray(data[: min(layout.ends)])
    new_rows = {n: list(r) for n, r in rows.items()}
    for offset, n in copied:
        new_rows[n][1] = len(out)
        out += data[offset : layout.ends[offset]]
    for number, chunk in zip(reused, chunks):
        new_rows[number] = [1, len(out), 0]
        out += _object_stream(number, [(n, objects[n]) for n in chunk])
        for index, n in enumerate(chunk):
            new_rows[n] = [2, number, index]
    for number in freed:
        new_rows[number] = [0, 0, 0]
    new_rows[xref.num] = [1, len(out), 0]
    return bytes(out + _xref_tail(xref, [new_rows[n] for n in xref.numbers], len(out)))


def _xref_tail(xref: Xref, rows_out: list[list[int]], offset: int) -> bytes:
    """The xref stream for `rows_out`, written at `offset`, and the trailer after it."""
    widths = (1, _width(max(r[1] for r in rows_out)), _width(max(r[2] for r in rows_out)))
    header = re.sub(rb"/W \[ \d+ \d+ \d+ \]", b"/W [ %d %d %d ]" % widths, xref.header)
    header = re.sub(rb"/Columns \d+", b"/Columns %d" % sum(widths), header)
    new_xref = Xref(offset, xref.num, header, widths, xref.numbers, rows_out)
    return encode_xref(new_xref) + b"startxref\n%d\n%%%%EOF\n" % offset


def _loose_object(text: bytes, number: int) -> bytes | None:
    """The value of top-level non-stream object `number` (generation 0), or None."""
    match = _LOOSE.match(text)
    if match is None or int(match.group(1)) != number:
        return None
    value = match.group(2)
    return None if value.endswith(b"endstream") else value


def _width(value: int) -> int:
    return max(1, (value.bit_length() + 7) // 8)


def _object_stream_members(data: bytes, row, number: int) -> dict[int, bytes] | None:
    """{object number: bytes} of the object stream `number` stored at `row`, or None."""
    if row is None or row[0] != 1:
        return None
    head = _OBJSTM_HEADER.match(data, row[1])
    if head is None or int(head.group(1)) != number:
        return None
    entries = head.group(2)
    if b"/Type /ObjStm" not in entries or b"/Extends" in entries or b"/DecodeParms" in entries:
        return None
    if b"/Filter /FlateDecode" not in entries:
        return None
    values = {key: pattern.search(entries) for key, pattern in _KEY.items()}
    if any(v is None for v in values.values()):
        return None
    count, first, length = (int(values[k].group(1)) for k in (b"N", b"First", b"Length"))
    try:
        body = zlib.decompress(data[head.end() : head.end() + length])
    except zlib.error:
        return None
    table = body[:first].split()
    if len(table) != 2 * count or not all(t.isdigit() for t in table):
        return None
    numbers = [int(t) for t in table[::2]]
    starts = [first + int(t) for t in table[1::2]] + [len(body)]
    return {n: body[a:b].rstrip() for n, a, b in zip(numbers, starts, starts[1:])}


def _object_stream(number: int, items: list[tuple[int, bytes]]) -> bytes:
    from pdftl.output.recompress import deflate

    table, position = [], 0
    for n, value in items:
        table.append(b"%d %d" % (n, position))
        position += len(value) + 1
    offsets = b" ".join(table) + b"\n"
    body = deflate(offsets + b"".join(value + b"\n" for _, value in items))
    return (
        b"%d 0 obj\n<< /Type /ObjStm /Length %d /Filter /FlateDecode /N %d /First %d >>\nstream\n"
        % (number, len(body), len(items), len(offsets))
        + body
        + b"\nendstream\nendobj\n"
    )


def _describe(obj):
    import pikepdf

    if isinstance(obj, pikepdf.Stream):
        if obj.get("/Type") in ("/ObjStm", "/XRef"):
            return _CONTAINER
        return (obj.stream_dict.unparse(), obj.read_raw_bytes())
    if isinstance(obj, pikepdf.Object):
        return obj.unparse(resolved=True)
    return repr(obj)


def _contents(data: bytes, check_warnings: bool):
    """({number: description}, trailer entries), or None on warnings when checked."""
    import pikepdf

    with pikepdf.open(io.BytesIO(data)) as pdf:
        found = {n: _describe(pdf.get_object((n, 0))) for n in range(1, int(pdf.trailer.Size))}
        trailer = {
            k: pdf.trailer[k].unparse() for k in ("/Root", "/Info", "/ID") if k in pdf.trailer
        }
        if check_warnings and pdf.get_warnings():
            return None
        return found, trailer


def same_objects(old: bytes, new: bytes) -> bool:
    """Whether both files hold the same objects under the same numbers.

    Objects are addressed by number: pikepdf hands indirect scalars back as
    plain Python values, which carry no object number of their own.
    """
    import pikepdf

    try:
        before, after = _contents(old, False), _contents(new, True)
    except pikepdf.PdfError:
        return False
    if after is None or before[1] != after[1]:
        return False
    skip = {n for n, v in (*before[0].items(), *after[0].items()) if v is _CONTAINER}
    numbers = (before[0].keys() | after[0].keys()) - skip
    return all(before[0].get(n) == after[0].get(n) for n in numbers)
