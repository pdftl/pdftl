# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/thumbs.py

"""Page thumbnail rendering off the GUI thread, via pypdfium2.

pdfium is not thread-safe: every call into it happens on one dedicated
worker thread owned by `PdfiumThumbProvider`, including reading page sizes.
The GUI thread only calls `request`/`request_sizes`/`cancel`/`close` and
receives `thumb_ready`/`sizes_ready`, which Qt delivers through a queued
connection because the emitting thread differs from the provider's own
(GUI) thread affinity.

A process-wide lock additionally serializes pdfium calls across separate
`PdfiumThumbProvider` instances (e.g. one outliving another's teardown),
since pdfium has no per-instance isolation.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pypdfium2 as pdfium
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

if TYPE_CHECKING:
    from pypdfium2 import PdfDocument

_DEFAULT_CACHE_BYTES = 256 * 1024 * 1024
_DEFAULT_DOC_CACHE_SIZE = 8

PDFIUM_LOCK = threading.Lock()

_OPEN_FILES_ARE_LOCKED = os.name == "nt"
"""Windows refuses to replace a file another handle has open."""


def open_pdf(path: Path, password: str | None = None) -> PdfDocument:
    """Open `path` with pdfium; where open files are locked, from a copy in memory,
    so the file can still be saved over while it is shown."""
    source = Path(path).read_bytes() if _OPEN_FILES_ARE_LOCKED else str(path)
    return pdfium.PdfDocument(source, password=password)


def render_page(doc: PdfDocument, page: int, width_px: int) -> QImage:
    """Render 1-based `page` of `doc` to a `width_px`-wide QImage.

    Height follows the page's aspect ratio after its own /Rotate, which
    pypdfium2 applies automatically to both page size and rendering.
    Raises `pdfium.PdfiumError` for a bad page number or render failure.
    """
    pdf_page = doc[page - 1]
    try:
        width_pt, _height_pt = pdf_page.get_size()
        scale = width_px / width_pt
        bitmap = pdf_page.render(scale=scale, rev_byteorder=True, fill_color=(255, 255, 255, 255))
        try:
            data = bitmap.to_numpy().tobytes()
            image = QImage(
                data, bitmap.width, bitmap.height, bitmap.stride, QImage.Format.Format_RGB888
            ).copy()
        finally:
            bitmap.close()
    finally:
        pdf_page.close()
    return image


@dataclass(frozen=True)
class _Request:
    key: str
    pdf_path: Path
    page: int
    width_px: int

    def dedupe_key(self) -> tuple[str, int, int]:
        return (self.key, self.page, self.width_px)


@dataclass(frozen=True)
class _SizeRequest:
    key: str
    pdf_path: Path
    count: int


@dataclass
class _CacheEntry:
    image: QImage
    nbytes: int


class PdfiumThumbProvider(QObject):
    """`ThumbProvider` backed by a single pdfium worker thread.

    Renders are queued LIFO, most recent first. Identical pending requests
    (same key, page, width) are deduplicated to their newest queue slot.
    Rendered images are cached in memory (bounded by total bytes); a small
    LRU of open `PdfDocument` handles avoids reopening files repeatedly.

    Page-size requests use their own queue, served ahead of renders, since a
    strip needs sizes to pick its thumbnail scale before rendering is useful.
    """

    thumb_ready = Signal(str, int, QImage)
    sizes_ready = Signal(str, object)
    """`object` payload is a `dict[int, tuple[float, float]]`; `dict` fails to marshal queued."""

    def __init__(
        self,
        cache_bytes: int = _DEFAULT_CACHE_BYTES,
        doc_cache_size: int = _DEFAULT_DOC_CACHE_SIZE,
        passwords: Callable[[Path], str | None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._cache_bytes_limit = cache_bytes
        self._doc_cache_size = doc_cache_size
        self._passwords = passwords

        self._cv = threading.Condition()
        self._queue: deque[_Request] = deque()
        self._pending: set[tuple[str, int, int]] = set()
        self._size_queue: deque[_SizeRequest] = deque()
        self._size_pending: set[str] = set()
        self._closed = False

        self._image_cache: OrderedDict[tuple[str, int, int], _CacheEntry] = OrderedDict()
        self._image_cache_bytes = 0

        self._doc_cache: OrderedDict[tuple[str, Path], PdfDocument] = OrderedDict()

        self._thread = threading.Thread(target=self._run, name="pdfium-thumbs", daemon=True)
        self._thread.start()

    def request_sizes(self, key: str, pdf_path: Path, count: int) -> None:
        """Queue reading pages 1..count's (width_pt, height_pt) after /Rotate.

        Delivered via `sizes_ready`; a later call for the same key supersedes
        an earlier one still queued.
        """
        with self._cv:
            if self._closed:
                return
            if key in self._size_pending:
                self._size_queue = deque(r for r in self._size_queue if r.key != key)
            self._size_pending.add(key)
            self._size_queue.append(_SizeRequest(key, Path(pdf_path), count))
            self._cv.notify()

    def request(self, key: str, pdf_path: Path, page: int, width_px: int) -> None:
        """Queue a render; most recent requests are served first."""
        req = _Request(key, Path(pdf_path), page, width_px)
        dedupe_key = req.dedupe_key()
        with self._cv:
            if self._closed:
                return
            if dedupe_key in self._pending:
                self._queue = deque(r for r in self._queue if r.dedupe_key() != dedupe_key)
            self._pending.add(dedupe_key)
            self._queue.append(req)
            self._cv.notify()

    def cancel(self, key: str) -> None:
        """Drop queued requests for `key`."""
        with self._cv:
            self._queue = deque(r for r in self._queue if r.key != key)
            self._pending = {d for d in self._pending if d[0] != key}
            self._size_queue = deque(r for r in self._size_queue if r.key != key)
            self._size_pending.discard(key)

    def narrow(self, key: str, keep_pages: set[int]) -> None:
        """Drop `key`'s queued (not yet started) requests for pages outside `keep_pages`.

        A request already popped by the worker thread keeps rendering; its
        result is cached, so re-requesting the same page later is cheap.
        """
        with self._cv:
            self._queue = deque(r for r in self._queue if r.key != key or r.page in keep_pages)
            self._pending = {d for d in self._pending if d[0] != key or d[1] in keep_pages}

    def close(self) -> None:
        """Stop and join the worker thread; later `request` calls are ignored."""
        with self._cv:
            if self._closed:
                return
            self._closed = True
            self._queue.clear()
            self._pending.clear()
            self._size_queue.clear()
            self._size_pending.clear()
            self._cv.notify_all()
        self._thread.join()
        with PDFIUM_LOCK:
            for doc in self._doc_cache.values():
                doc.close()
        self._doc_cache.clear()

    def _run(self) -> None:
        while True:
            item = self._next_item()
            if item is None:
                return
            if isinstance(item, _SizeRequest):
                self._process_sizes(item)
            else:
                self._process(item)

    def _next_item(self) -> _Request | _SizeRequest | None:
        with self._cv:
            while not self._queue and not self._size_queue and not self._closed:
                self._cv.wait()
            if self._closed:
                return None
            if self._size_queue:
                size_req = self._size_queue.pop()
                self._size_pending.discard(size_req.key)
                return size_req
            req = self._queue.pop()
            self._pending.discard(req.dedupe_key())
            return req

    def _process_sizes(self, req: _SizeRequest) -> None:
        with PDFIUM_LOCK:
            try:
                doc = self._get_document(req.key, req.pdf_path)
            except (OSError, pdfium.PdfiumError):
                self.sizes_ready.emit(req.key, {})
                return
            sizes: dict[int, tuple[float, float]] = {}
            for page_num in range(1, req.count + 1):
                try:
                    pdf_page = doc[page_num - 1]
                except pdfium.PdfiumError:
                    continue
                try:
                    sizes[page_num] = pdf_page.get_size()
                finally:
                    pdf_page.close()
        self.sizes_ready.emit(req.key, sizes)

    def _process(self, req: _Request) -> None:
        cache_key = req.dedupe_key()
        cached = self._image_cache.get(cache_key)
        if cached is not None:
            self._image_cache.move_to_end(cache_key)
            self.thumb_ready.emit(req.key, req.page, cached.image)
            return
        image = self._render(req)
        if not image.isNull():
            self._store_in_cache(cache_key, image)
        self.thumb_ready.emit(req.key, req.page, image)

    def _render(self, req: _Request) -> QImage:
        if req.width_px <= 0:
            return QImage()
        with PDFIUM_LOCK:
            try:
                doc = self._get_document(req.key, req.pdf_path)
            except (OSError, pdfium.PdfiumError):
                return QImage()
            try:
                return render_page(doc, req.page, req.width_px)
            except pdfium.PdfiumError:
                return QImage()

    def _get_document(self, key: str, pdf_path: Path) -> PdfDocument:
        """Open documents are cached per (key, path): a new key means new content."""
        cached = self._doc_cache.get((key, pdf_path))
        if cached is not None:
            self._doc_cache.move_to_end((key, pdf_path))
            return cached
        password = self._passwords(pdf_path) if self._passwords is not None else None
        doc = open_pdf(pdf_path, password)
        self._doc_cache[(key, pdf_path)] = doc
        while len(self._doc_cache) > self._doc_cache_size:
            _evicted_path, evicted_doc = self._doc_cache.popitem(last=False)
            evicted_doc.close()
        return doc

    def _store_in_cache(self, cache_key: tuple[str, int, int], image: QImage) -> None:
        nbytes = image.sizeInBytes()
        self._image_cache[cache_key] = _CacheEntry(image, nbytes)
        self._image_cache_bytes += nbytes
        while self._image_cache_bytes > self._cache_bytes_limit and self._image_cache:
            _evicted_key, evicted = self._image_cache.popitem(last=False)
            self._image_cache_bytes -= evicted.nbytes
