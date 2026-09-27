# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/output/compress_xmp.py

"""Flate-compress the catalog's XMP metadata stream in a file qpdf has written.

qpdf compresses every other metadata stream but always writes the catalog's
uncompressed, so non-PDF tools can find the XMP packet by scanning bytes.
PDF readers decode it either way. ISO 19005-1 (PDF/A-1) forbids a /Filter
on this stream; parts 2 to 4 do not. So PDF/A-1 files are left alone, as is
any file whose PDF/A part cannot be read.

The saved file is patched: the stream is replaced by a compressed copy and
the cross-reference stream's offsets are shifted to match. Anything
unexpected leaves the file as qpdf wrote it.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import zlib

from pdftl.output.xref_stream import LENGTH as _LENGTH
from pdftl.output.xref_stream import Xref as _Xref
from pdftl.output.xref_stream import copy_range as _copy

from pdftl.output.xref_stream import encode_xref as _encode_xref
from pdftl.output.xref_stream import read_xref as _read_xref

_PDFA_ID_NS = b"http://www.aiim.org/pdfa/ns/id/"
_HEADER_MAX = 4096
_OBJ_END = (b"endstream\nendobj\n", b"\nendstream\nendobj\n")


def may_compress_xmp(pdf) -> bool:
    """Whether `pdf` has a catalog XMP stream that may be compressed."""
    import pikepdf

    metadata = pdf.Root.get("/Metadata")
    if not isinstance(metadata, pikepdf.Stream):
        return False
    try:
        xmp = metadata.read_bytes()
    except (pikepdf.PdfError, pikepdf.DataDecodingError):
        return False
    if b"\0" in xmp:  # UTF-16 or UTF-32, where a PDF/A claim cannot be found by bytes
        return False
    return _PDFA_ID_NS not in xmp or _pdfa_part(pdf) >= 2


def _pdfa_part(pdf) -> int:
    try:
        return int(pdf.open_metadata(set_pikepdf_as_editor=False).get("pdfaid:part", 0))
    except ValueError:  # a part that is not a number: treat as PDF/A-1
        return 0


def compress_saved_file(path: str) -> bool:
    """Compress the catalog XMP of the qpdf-written file at `path`, in place."""
    fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=os.path.dirname(os.path.abspath(path)))
    replaced = False
    try:
        with open(path, "rb") as src, os.fdopen(fd, "wb") as dst:
            xmp = _patch(src, dst)
        if xmp is not None and _verify(tmp, xmp):
            shutil.copymode(path, tmp)
            os.replace(tmp, path)
            replaced = True
    finally:
        if not replaced:
            os.unlink(tmp)
    return replaced


def compress_saved_bytes(data: bytes) -> bytes:
    """`data` with its catalog XMP compressed, or `data` unchanged."""
    import io

    dst = io.BytesIO()
    xmp = _patch(io.BytesIO(data), dst)
    if xmp is not None and _verify(io.BytesIO(dst.getvalue()), xmp):
        return dst.getvalue()
    return data


def _verify(source, xmp: bytes) -> bool:
    import pikepdf

    try:
        with pikepdf.open(source) as pdf:
            metadata = pdf.Root.Metadata
            ok = metadata.get("/Filter") == "/FlateDecode" and metadata.read_bytes() == xmp
            return ok and not pdf.get_warnings()
    except (pikepdf.PdfError, AttributeError):
        return False


def _patch(src, dst) -> bytes | None:
    """Write `src` to `dst` with its catalog XMP compressed; return the XMP, or None."""
    num, xmp = _saved_metadata(src)
    size = src.seek(0, os.SEEK_END)
    xref = _read_xref(src, size) if num else None
    located = _locate_metadata(src, xref, num, xmp) if xref else None
    if located is None:
        return None
    start, end, header = located
    packed = zlib.compress(xmp, 9)
    header = _LENGTH.sub(b"/Length %d /Filter /FlateDecode" % len(packed), header)
    new_obj = header + b"stream\n" + packed + b"\nendstream\nendobj\n"
    delta = len(new_obj) - (end - start)
    if delta >= 0 or not _offsets_valid(src, xref, start, end):
        return None
    for row in xref.rows:
        if row[0] == 1 and row[1] >= end:
            row[1] += delta
    _copy(src, dst, 0, start)
    dst.write(new_obj)
    _copy(src, dst, end, xref.offset)
    dst.write(_encode_xref(xref))
    dst.write(b"startxref\n%d\n%%%%EOF\n" % (xref.offset + delta))
    return xmp


def _saved_metadata(src) -> tuple[int, bytes]:
    """(object number, decoded data) of the catalog XMP as saved, or (0, b"")."""
    import pikepdf

    src.seek(0)
    try:
        with pikepdf.open(src) as pdf:
            metadata = pdf.Root.get("/Metadata")
            if isinstance(metadata, pikepdf.Stream):
                return metadata.objgen[0], metadata.read_bytes()
    except pikepdf.PdfError:
        pass
    return 0, b""


def _locate_metadata(src, xref: _Xref, num: int, xmp: bytes) -> tuple[int, int, bytes] | None:
    """(start, end, header) of object `num` if it is an uncompressed stream holding `xmp`."""
    rows = [row for n, row in zip(xref.numbers, xref.rows) if n == num and row[0] == 1]
    if len(rows) != 1 or num == xref.num:
        return None
    offset = rows[0][1]
    src.seek(offset)
    chunk = src.read(_HEADER_MAX)
    stream_at = chunk.find(b">>\nstream\n")
    if stream_at < 0 or not chunk.startswith(b"%d 0 obj\n<<" % num):
        return None
    header = chunk[: stream_at + 3]
    lengths = _LENGTH.findall(header)
    if len(lengths) != 1 or b"/Filter" in header or b"/DecodeParms" in header:
        return None
    data_at = offset + stream_at + 10
    src.seek(data_at)
    data = src.read(int(lengths[0]))
    trailer = src.read(len(_OBJ_END[1]))
    ending = next((e for e in _OBJ_END if trailer.startswith(e)), None)
    if data != xmp or ending is None:
        return None
    return offset, data_at + len(data) + len(ending), header


def _offsets_valid(src, xref: _Xref, start: int, end: int) -> bool:
    """Every in-use entry points at its own object, none inside the replaced one."""
    for num, (kind, offset, gen) in zip(xref.numbers, xref.rows):
        if kind != 1:
            continue
        if start < offset < end:
            return False
        src.seek(offset)
        if not src.read(24).startswith(b"%d %d obj" % (num, gen)):
            return False
    return True
