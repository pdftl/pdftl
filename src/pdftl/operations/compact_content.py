# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/compact_content.py

"""Rewrite content streams in their shortest equivalent form."""

from __future__ import annotations

import functools
import logging
import os
from concurrent.futures import ThreadPoolExecutor

import pdftl.core.constants as c
from pdftl.core.core_types import HelpExample, OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError

logger = logging.getLogger(__name__)

_LONG_DESC = """
The `compact_content` operation rewrites every page content stream and
Form XObject in its shortest equivalent form: tokens are separated only
where they would otherwise run together, and numbers take their shortest
spelling (`0.50` becomes `.5`, `2.0` becomes `2`). A page whose content
is split over several streams gets one.

Nothing drawn changes: tokens are only respaced or respelled with the same
value, the rewrite is tokenized again and must hold as many tokens as the
original, and it is kept only if it compresses smaller than the original (with zopfli
when `shrink deflate=zopfli` runs it).
Streams holding a malformed token are left alone.
"""

_EXAMPLES = [
    HelpExample(
        desc="Compact every content stream.",
        cmd="in.pdf compact_content output out.pdf",
    ),
]


BATCH_BYTES = 64 << 20  # content held for the worker threads at once


def _worth_it(data: bytes, original: bytes) -> bool:
    from pdftl.output.recompress import deflate

    # Measured with the save's own encoder: zopfli can prefer the original spelling.
    return len(deflate(data)) < len(deflate(original))


def _page_streams(page) -> list:
    import pikepdf

    contents = page.obj.get("/Contents")
    streams = list(contents) if isinstance(contents, pikepdf.Array) else [contents]
    return [s for s in streams if isinstance(s, pikepdf.Stream)]


def _read(read) -> bytes | None:
    import pikepdf

    try:
        return read()
    except (pikepdf.PdfError, pikepdf.DataDecodingError):
        return None


def _page_jobs(pdf):
    """(content, writer) for each page with content."""
    for page in pdf.pages:
        streams = _page_streams(page)
        if streams:
            original = _read(lambda: b"\n".join(s.read_bytes() for s in streams))
            yield original, functools.partial(_write_page, pdf, page, streams)


def _write_page(pdf, page, streams, data: bytes) -> None:
    if len(streams) == 1:
        streams[0].write(data)
    else:
        page.obj.Contents = pdf.make_stream(data)


def _form_jobs(pdf):
    """(content, writer) for each Form XObject."""
    import pikepdf

    for obj in pdf.objects:
        if isinstance(obj, pikepdf.Stream) and obj.get("/Subtype") == "/Form":
            yield _read(obj.read_bytes), obj.write


def _compact_all(jobs, pool) -> int:
    """Compact each job's content; write it back where that deflates smaller."""
    from pdftl.utils.compact_content import compact_stream

    done = 0
    batch: list = []
    held = 0
    for original, write in jobs:
        data = compact_stream(original) if original is not None else None
        if data is None:
            continue
        batch.append((data, write, pool.submit(_worth_it, data, original)))
        held += len(original) + len(data)
        if held >= BATCH_BYTES:
            done += _commit(batch)
            batch, held = [], 0
    return done + _commit(batch)


def _commit(batch: list) -> int:
    done = 0
    for data, write, future in batch:
        if future.result():
            write(data)
            done += 1
    return done


@register_operation(
    "compact_content",
    tags=["in_place", "optimization"],
    type="single input operation",
    desc="Rewrite content streams in their shortest equivalent form",
    long_desc=_LONG_DESC,
    usage="<input> compact_content output <output>",
    examples=_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def compact_content(pdf, operation_args: list) -> OpResult:
    """Rewrite page and form content streams compactly, where that is smaller."""
    if operation_args:
        raise InvalidArgumentError(
            f"compact_content: takes no arguments, got '{' '.join(operation_args)}'"
        )
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        pages = _compact_all(_page_jobs(pdf), pool)
        # Pages first: a page's new stream must not appear mid-way through pdf.objects.
        forms = _compact_all(_form_jobs(pdf), pool)
    summary = f"Compacted {pages} page content stream(s) and {forms} form(s)"
    logger.info(summary)
    return OpResult(success=True, pdf=pdf, data={"pages": pages, "forms": forms}, summary=summary)
