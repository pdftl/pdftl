# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/server/multipart.py

"""Streaming multipart/form-data parsing for the pdftl HTTP server.

Extracted from the request-handler mixin: none of this logic depends on
`self` beyond an `rfile`-like readable, so it's exposed as a small set of
free functions that take `rfile` explicitly. This also makes the fiddly
boundary-scanning logic unit-testable without instantiating any HTTP
handler machinery.
"""

import logging
import os
import tempfile
from typing import Any

logger = logging.getLogger(__name__)

# Bounded chunk size for streaming the request body off the socket.
_STREAM_CHUNK_SIZE = 256 * 1024
_SPOOL_MAX_MEMORY_BYTES = 4 * 1024 * 1024


def extract_boundary(content_type: str) -> bytes:
    """Extract the multipart boundary marker bytes from a Content-Type header."""
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part[len("boundary=") :].strip('"')
            return b"--" + boundary.encode("utf-8")
    raise ValueError("No boundary found in Content-Type header")


def split_multipart_segments(post_data: bytes, boundary_bytes: bytes) -> list[bytes]:
    """Split a raw multipart body strictly on literal boundary bytes.

    Retained for direct unit tests against a fully-buffered body; the live
    request path goes through `parse_multipart_payload`'s streaming scanner
    instead of calling this on the whole body at once.
    """
    delimiter = b"\r\n" + boundary_bytes
    if post_data.startswith(boundary_bytes):
        post_data = b"\r\n" + post_data
    segments = post_data.split(delimiter)
    return [s for s in segments if s and s not in (b"--\r\n", b"--", b"\r\n--\r\n", b"\r\n--")]


def parse_content_disposition(header_block: str) -> tuple[str | None, str | None]:
    """Extract the ``name`` and ``filename`` params from a part's headers."""
    name = None
    filename = None
    for line in header_block.split("\r\n"):
        if not line.lower().startswith("content-disposition"):
            continue
        for piece in line.split(";"):
            piece = piece.strip()
            if piece.startswith("name="):
                name = piece[len("name=") :].strip('"')
            elif piece.startswith("filename="):
                filename = piece[len("filename=") :].strip('"')
    return name, filename


def parse_multipart_segment(segment: bytes) -> tuple[str | None, str | None, bytes | None]:
    """Split one multipart segment into (name, filename, body).

    Returns (None, None, None) if the segment has no usable headers or is
    missing a ``name`` parameter.
    """
    if segment.startswith(b"\r\n"):
        segment = segment[2:]
    header_end = segment.find(b"\r\n\r\n")
    if header_end == -1:
        return None, None, None

    header_block = segment[:header_end].decode("utf-8", errors="ignore")
    body = segment[header_end + 4 :]

    name, filename = parse_content_disposition(header_block)
    if not name:
        return None, None, None
    return name, filename, body


def iter_body_chunks(rfile: Any, content_length: int):
    """Reads the request body off a readable in bounded chunks instead of
    one big read()-then-split(), keeping peak memory O(chunk size)."""
    remaining = content_length
    while remaining > 0:
        chunk = rfile.read(min(_STREAM_CHUNK_SIZE, remaining))
        if not chunk:
            break
        remaining -= len(chunk)
        yield chunk


def _process_segment(
    segment: bytes,
    parsed_fields: dict[str, Any],
    uploaded_files: list[dict[str, Any]],
) -> None:
    """Parses a single already-delimited multipart segment and records its
    field/file contents into the shared accumulators."""
    if not segment or segment in (b"--", b"--\r\n"):
        return
    name, filename, body = parse_multipart_segment(segment)
    if not name:
        return
    if filename:
        fd, tmp_path = tempfile.mkstemp(prefix="pdftl_upload_", suffix=".pdf")
        with os.fdopen(fd, "wb") as f:
            f.write(body)
        file_info = {"name": name, "filename": filename, "path": tmp_path}
        uploaded_files.append(file_info)
        parsed_fields[name] = file_info
    else:
        parsed_fields[name] = body.decode("utf-8", errors="ignore")


def parse_multipart_payload(
    rfile: Any, content_type: str, content_length: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Streams the multipart body, scanning for the boundary with a bounded
    sliding window rather than materializing the whole body in RAM at once.
    File parts spool to a disk-backed temp file and are exposed to callers
    as a `path` rather than in-memory `content` bytes.
    """
    boundary_bytes = extract_boundary(content_type)
    delimiter = b"\r\n" + boundary_bytes

    parsed_fields: dict[str, Any] = {}
    uploaded_files: list[dict[str, Any]] = []

    buf = bytearray()
    for chunk in iter_body_chunks(rfile, content_length):
        buf.extend(chunk)
        while True:
            idx = buf.find(delimiter)
            if idx == -1:
                break
            segment = bytes(buf[:idx])
            del buf[: idx + len(delimiter)]
            _process_segment(segment, parsed_fields, uploaded_files)
        # NOTE: `buf` must retain every byte not yet matched to a
        # delimiter -- it's the in-progress body of whatever part is
        # currently streaming in, not scratch space. Nothing safe to trim.

    return parsed_fields, uploaded_files


def cleanup_uploaded_files(uploaded_files: list[dict[str, Any]]) -> None:
    """Removes the on-disk spool files written by parse_multipart_payload."""
    for f in uploaded_files:
        path = f.get("path")
        if path:
            try:
                os.remove(path)
            except OSError as exc:
                logger.debug("Could not remove upload spool file %s: %s", path, exc)
