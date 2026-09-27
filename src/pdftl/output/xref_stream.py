# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/output/xref_stream.py

"""The cross-reference stream of a file qpdf has written, read and re-encoded.

Only the exact layout qpdf writes is accepted (a single xref stream with PNG
Up prediction at the end of the file); anything else reads as None.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass

TAIL_BYTES = 1024
STARTXREF = re.compile(rb"startxref\n(\d+)\n%%EOF\n?\Z")
LENGTH = re.compile(rb"/Length (\d+)")
XREF_HEADER = re.compile(
    rb"\A(\d+) 0 obj\n<< /Type /XRef /Length \d+ /Filter /FlateDecode"
    rb" /DecodeParms << /Columns (\d+) /Predictor 12 >> /W \[ (\d+) (\d+) (\d+) \]"
)
INDEX = re.compile(rb"/Index \[ ([\d ]+) \]")
SIZE = re.compile(rb"/Size (\d+)")


@dataclass
class Xref:
    offset: int  # of the xref stream object
    num: int  # its object number
    header: bytes  # up to and including ">>\n"
    widths: tuple[int, int, int]
    numbers: list[int]  # object number of each row
    rows: list[list[int]]


def read_xref(src, size: int) -> Xref | None:
    src.seek(max(0, size - TAIL_BYTES))
    match = STARTXREF.search(src.read())
    if match is None:
        return None
    offset = int(match.group(1))
    src.seek(offset)
    tail = src.read(size - offset)
    head = XREF_HEADER.match(tail)
    stream_at = tail.find(b">>\nstream\n")
    if head is None or stream_at < 0:
        return None
    header = tail[: stream_at + 3]
    if b"/Encrypt" in header:
        return None
    length = int(LENGTH.search(header).group(1))
    body = tail[stream_at + 10 : stream_at + 10 + length]
    if not tail[stream_at + 10 + length :].startswith(b"\nendstream\nendobj\n" + match.group(0)):
        return None
    widths = (int(head.group(3)), int(head.group(4)), int(head.group(5)))
    columns = int(head.group(2))
    if sum(widths) != columns:
        return None
    rows = decode_rows(body, columns, widths)
    numbers = object_numbers(header)
    if rows is None or numbers is None or len(numbers) != len(rows):
        return None
    return Xref(offset, int(head.group(1)), header, widths, numbers, rows)


def object_numbers(header: bytes) -> list[int] | None:
    index = INDEX.search(header)
    if index is None:
        size = SIZE.search(header)
        return None if size is None else list(range(int(size.group(1))))
    pairs = [int(n) for n in index.group(1).split()]
    if len(pairs) % 2:
        return None
    return [
        n for first, count in zip(pairs[::2], pairs[1::2]) for n in range(first, first + count)
    ]


def decode_rows(body: bytes, columns: int, widths) -> list[list[int]] | None:
    try:
        data = zlib.decompress(body)
    except zlib.error:
        return None
    stride = columns + 1
    if len(data) % stride:
        return None
    rows, prior = [], bytes(columns)
    for i in range(0, len(data), stride):
        kind, raw = data[i], data[i + 1 : i + stride]
        if kind == 2:
            raw = bytes((a + b) & 0xFF for a, b in zip(raw, prior))
        elif kind != 0:
            return None
        prior = raw
        fields, pos = [], 0
        for width in widths:
            fields.append(int.from_bytes(raw[pos : pos + width], "big"))
            pos += width
        rows.append(fields)
    return rows


def encode_xref(xref: Xref) -> bytes:
    out, prior = bytearray(), bytes(sum(xref.widths))
    for fields in xref.rows:
        raw = b"".join(v.to_bytes(w, "big") for v, w in zip(fields, xref.widths))
        out += b"\x02" + bytes((a - b) & 0xFF for a, b in zip(raw, prior))
        prior = raw
    body = zlib.compress(bytes(out), 9)
    header = LENGTH.sub(b"/Length %d" % len(body), xref.header, count=1)
    return header + b"stream\n" + body + b"\nendstream\nendobj\n"


def copy_range(src, dst, begin: int, stop: int, chunk: int = 1 << 20) -> None:
    src.seek(begin)
    remaining = stop - begin
    while remaining > 0:
        block = src.read(min(chunk, remaining))
        dst.write(block)
        remaining -= len(block)
