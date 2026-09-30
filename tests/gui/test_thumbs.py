# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/gui/test_thumbs.py

"""Tests for thumbs module: pdfium-backed thumbnail rendering off the GUI thread."""

import threading
from pathlib import Path

import pikepdf
import pytest

pytest.importorskip("PySide6")

import pypdfium2 as pdfium
from PySide6.QtCore import QObject

from pdftl.gui import thumbs


class _Collector(QObject):
    """Collects thumb_ready deliveries; a real QObject so cross-thread
    signal emission uses a queued connection like a real GUI widget would."""

    def __init__(self):
        super().__init__()
        self.received: list[tuple[str, int, object]] = []

    def on_ready(self, key: str, page: int, image: object) -> None:
        self.received.append((key, page, image))


class _SizesCollector(QObject):
    """Collects sizes_ready deliveries; a real QObject for the same reason."""

    def __init__(self):
        super().__init__()
        self.received: list[tuple[str, dict]] = []

    def on_ready(self, key: str, sizes: dict) -> None:
        self.received.append((key, sizes))


def _make_half_pdf(
    path: Path, width: float = 200, height: float = 100, rotate: int = 0, pages: int = 1
) -> Path:
    """Page(s) with the left half filled black, the right half white."""
    pdf = pikepdf.new()
    content = (
        f"0 0 0 rg 0 0 {width / 2} {height} re f 1 1 1 rg {width / 2} 0 {width / 2} {height} re f"
    ).encode()
    for _ in range(pages):
        pdf.add_blank_page(page_size=(width, height))
    for page in pdf.pages:
        page.Contents = pdf.make_stream(content)
        if rotate:
            page.Rotate = rotate
    pdf.save(path)
    return path


@pytest.fixture
def provider(qtbot):
    p = thumbs.PdfiumThumbProvider()
    yield p
    p.close()


def test_renders_left_dark_right_light(qtbot, tmp_path, provider):
    pdf_path = _make_half_pdf(tmp_path / "half.pdf")
    collector = _Collector()
    provider.thumb_ready.connect(collector.on_ready)

    provider.request("k", pdf_path, 1, 200)
    qtbot.waitUntil(lambda: len(collector.received) == 1, timeout=2000)

    key, page, image = collector.received[0]
    assert key == "k"
    assert page == 1
    assert not image.isNull()
    assert image.width() == 200
    left = image.pixelColor(10, image.height() // 2)
    right = image.pixelColor(190, image.height() // 2)
    assert (left.red(), left.green(), left.blue()) == (0, 0, 0)
    assert (right.red(), right.green(), right.blue()) == (255, 255, 255)


def test_rotated_page_swaps_aspect_and_moves_dark_half(qtbot, tmp_path, provider):
    # Unrotated: left half black, right half white, 200x100.
    # /Rotate 90 (clockwise) turns the left edge into the top edge, so the
    # rendered bitmap is 100 wide x 200 tall with the black half on top.
    pdf_path = _make_half_pdf(tmp_path / "rotated.pdf", rotate=90)
    collector = _Collector()
    provider.thumb_ready.connect(collector.on_ready)

    provider.request("k", pdf_path, 1, 100)
    qtbot.waitUntil(lambda: len(collector.received) == 1, timeout=2000)

    _key, _page, image = collector.received[0]
    assert not image.isNull()
    assert image.width() == 100
    assert image.height() == 200
    top = image.pixelColor(50, 10)
    bottom = image.pixelColor(50, 190)
    assert (top.red(), top.green(), top.blue()) == (0, 0, 0)
    assert (bottom.red(), bottom.green(), bottom.blue()) == (255, 255, 255)


def test_missing_file_delivers_null_image(qtbot, tmp_path, provider):
    collector = _Collector()
    provider.thumb_ready.connect(collector.on_ready)

    provider.request("k", tmp_path / "does_not_exist.pdf", 1, 100)
    qtbot.waitUntil(lambda: len(collector.received) == 1, timeout=2000)

    key, page, image = collector.received[0]
    assert key == "k"
    assert page == 1
    assert image.isNull()


def test_bad_page_number_delivers_null_image(qtbot, tmp_path, provider):
    pdf_path = _make_half_pdf(tmp_path / "half.pdf")
    collector = _Collector()
    provider.thumb_ready.connect(collector.on_ready)

    provider.request("k", pdf_path, 7, 100)
    qtbot.waitUntil(lambda: len(collector.received) == 1, timeout=2000)

    _key, page, image = collector.received[0]
    assert page == 7
    assert image.isNull()


def test_corrupt_file_delivers_null_image(qtbot, tmp_path, provider):
    bad_path = tmp_path / "corrupt.pdf"
    bad_path.write_bytes(b"not a pdf at all")
    collector = _Collector()
    provider.thumb_ready.connect(collector.on_ready)

    provider.request("k", bad_path, 1, 100)
    qtbot.waitUntil(lambda: len(collector.received) == 1, timeout=2000)

    assert collector.received[0][2].isNull()


def test_cache_hit_delivered_without_rerender(qtbot, tmp_path, provider, monkeypatch):
    real_render = thumbs.render_page
    calls = []

    def counting_render(doc, page, width_px):
        calls.append((page, width_px))
        return real_render(doc, page, width_px)

    monkeypatch.setattr(thumbs, "render_page", counting_render)

    pdf_path = _make_half_pdf(tmp_path / "half.pdf")
    collector = _Collector()
    provider.thumb_ready.connect(collector.on_ready)

    provider.request("k", pdf_path, 1, 200)
    qtbot.waitUntil(lambda: len(collector.received) == 1, timeout=2000)
    provider.request("k", pdf_path, 1, 200)
    qtbot.waitUntil(lambda: len(collector.received) == 2, timeout=2000)

    assert calls == [(1, 200)]
    assert not collector.received[1][2].isNull()


def test_image_cache_bounded_by_bytes_evicts_oldest(qtbot, tmp_path, monkeypatch):
    real_render = thumbs.render_page
    calls = []

    def counting_render(doc, page, width_px):
        calls.append((page, width_px))
        return real_render(doc, page, width_px)

    monkeypatch.setattr(thumbs, "render_page", counting_render)

    pdf_path = _make_half_pdf(tmp_path / "half.pdf", pages=2)

    probe = thumbs.PdfiumThumbProvider()
    probe_collector = _Collector()
    probe.thumb_ready.connect(probe_collector.on_ready)
    probe.request("k", pdf_path, 1, 200)
    qtbot.waitUntil(lambda: len(probe_collector.received) == 1, timeout=2000)
    one_image_bytes = probe_collector.received[0][2].sizeInBytes()
    probe.close()

    provider = thumbs.PdfiumThumbProvider(cache_bytes=int(one_image_bytes * 1.5))
    collector = _Collector()
    provider.thumb_ready.connect(collector.on_ready)

    provider.request("k", pdf_path, 1, 200)
    qtbot.waitUntil(lambda: len(collector.received) == 1, timeout=2000)
    provider.request("k", pdf_path, 2, 200)
    qtbot.waitUntil(lambda: len(collector.received) == 2, timeout=2000)

    calls.clear()
    provider.request("k", pdf_path, 1, 200)
    qtbot.waitUntil(lambda: len(collector.received) == 3, timeout=2000)

    assert calls == [(1, 200)]
    provider.close()


def test_lifo_order_with_dedupe(qtbot, provider, tmp_path, monkeypatch):
    pdf_path = _make_half_pdf(tmp_path / "half.pdf", pages=6)
    real_render = thumbs.render_page
    started = threading.Event()
    release = threading.Event()
    rendered_pages = []

    def blocking_render(doc, page, width_px):
        rendered_pages.append(page)
        if page == 1:
            started.set()
            release.wait(timeout=5)
        return real_render(doc, page, width_px)

    monkeypatch.setattr(thumbs, "render_page", blocking_render)

    collector = _Collector()
    provider.thumb_ready.connect(collector.on_ready)

    provider.request("k", pdf_path, 1, 100)
    assert started.wait(timeout=5)

    provider.request("k", pdf_path, 2, 100)
    provider.request("k", pdf_path, 3, 100)
    provider.request("k", pdf_path, 3, 100)  # duplicate of the previous, deduplicated
    provider.request("k", pdf_path, 4, 100)
    release.set()

    qtbot.waitUntil(lambda: len(collector.received) == 4, timeout=2000)
    delivered_pages = [page for _key, page, _image in collector.received]
    assert delivered_pages == [1, 4, 3, 2]
    assert rendered_pages == [1, 4, 3, 2]


def test_cancel_drops_pending_requests(qtbot, provider, tmp_path, monkeypatch):
    pdf_path = _make_half_pdf(tmp_path / "half.pdf", pages=6)
    real_render = thumbs.render_page
    started = threading.Event()
    release = threading.Event()

    def blocking_render(doc, page, width_px):
        if page == 1:
            started.set()
            release.wait(timeout=5)
        return real_render(doc, page, width_px)

    monkeypatch.setattr(thumbs, "render_page", blocking_render)

    collector = _Collector()
    provider.thumb_ready.connect(collector.on_ready)

    provider.request("blocker", pdf_path, 1, 100)
    assert started.wait(timeout=5)

    provider.request("cancel_me", pdf_path, 2, 100)
    provider.request("cancel_me", pdf_path, 3, 100)
    provider.request("keep_me", pdf_path, 4, 100)
    provider.cancel("cancel_me")
    release.set()

    qtbot.waitUntil(lambda: len(collector.received) == 2, timeout=2000)
    keys = {key for key, _page, _image in collector.received}
    assert keys == {"blocker", "keep_me"}


def test_narrow_drops_queued_pages_outside_keep_set(qtbot, provider, tmp_path, monkeypatch):
    pdf_path = _make_half_pdf(tmp_path / "half.pdf", pages=8)
    real_render = thumbs.render_page
    started = threading.Event()
    release = threading.Event()

    def blocking_render(doc, page, width_px):
        if page == 1:
            started.set()
            release.wait(timeout=5)
        return real_render(doc, page, width_px)

    monkeypatch.setattr(thumbs, "render_page", blocking_render)

    collector = _Collector()
    provider.thumb_ready.connect(collector.on_ready)

    provider.request("blocker", pdf_path, 1, 100)
    assert started.wait(timeout=5)

    provider.request("strip", pdf_path, 2, 100)
    provider.request("strip", pdf_path, 3, 100)
    provider.request("strip", pdf_path, 5, 100)
    provider.narrow("strip", {5})
    release.set()

    qtbot.waitUntil(lambda: len(collector.received) == 2, timeout=2000)
    keys_pages = {(key, page) for key, page, _image in collector.received}
    assert keys_pages == {("blocker", 1), ("strip", 5)}


def test_close_joins_thread_and_ignores_later_requests(qtbot, tmp_path):
    pdf_path = _make_half_pdf(tmp_path / "half.pdf")
    provider = thumbs.PdfiumThumbProvider()
    collector = _Collector()
    provider.thumb_ready.connect(collector.on_ready)

    provider.request("k", pdf_path, 1, 200)
    qtbot.waitUntil(lambda: len(collector.received) == 1, timeout=2000)

    provider.close()
    assert not provider._thread.is_alive()

    provider.request("k", pdf_path, 1, 200)
    qtbot.wait(200)
    assert len(collector.received) == 1

    provider.close()  # idempotent


def test_document_cache_reuses_open_handle(qtbot, provider, tmp_path, monkeypatch):
    open_calls = []
    real_ctor = pdfium.PdfDocument

    def counting_ctor(*args, **kwargs):
        open_calls.append(args)
        return real_ctor(*args, **kwargs)

    monkeypatch.setattr(thumbs.pdfium, "PdfDocument", counting_ctor)

    pdf_path = _make_half_pdf(tmp_path / "half.pdf", pages=3)
    collector = _Collector()
    provider.thumb_ready.connect(collector.on_ready)

    provider.request("k", pdf_path, 1, 200)
    qtbot.waitUntil(lambda: len(collector.received) == 1, timeout=2000)
    provider.request("k", pdf_path, 2, 200)
    qtbot.waitUntil(lambda: len(collector.received) == 2, timeout=2000)

    assert len(open_calls) == 1


def test_document_lru_eviction_closes_documents(qtbot, tmp_path, monkeypatch):
    closed = []
    real_close = pdfium.PdfDocument.close

    def spy_close(self):
        closed.append(self)
        return real_close(self)

    monkeypatch.setattr(pdfium.PdfDocument, "close", spy_close)

    provider = thumbs.PdfiumThumbProvider(doc_cache_size=2)
    collector = _Collector()
    provider.thumb_ready.connect(collector.on_ready)

    paths = [_make_half_pdf(tmp_path / f"doc{i}.pdf") for i in range(3)]
    for i, path in enumerate(paths):
        provider.request(f"k{i}", path, 1, 200)
        qtbot.waitUntil(lambda n=i: len(collector.received) == n + 1, timeout=2000)

    assert len(closed) == 1  # the first (oldest) document was evicted and closed

    provider.close()
    assert len(closed) == 3  # close() closes the two still cached


def test_negative_width_delivers_null_image(qtbot, tmp_path, provider):
    pdf_path = _make_half_pdf(tmp_path / "half.pdf")
    collector = _Collector()
    provider.thumb_ready.connect(collector.on_ready)

    provider.request("k", pdf_path, 1, 0)
    qtbot.waitUntil(lambda: len(collector.received) == 1, timeout=2000)

    assert collector.received[0][2].isNull()


def test_passwords_callback_used_to_open_encrypted_document(qtbot, tmp_path):
    pdf_path = tmp_path / "secret.pdf"
    src = pikepdf.new()
    src.add_blank_page(page_size=(200, 100))
    src.pages[0].Contents = src.make_stream(
        b"0 0 0 rg 0 0 100 100 re f 1 1 1 rg 100 0 100 100 re f"
    )
    src.save(pdf_path, encryption=pikepdf.Encryption(owner="owner", user="hunter2"))

    seen_paths = []

    def passwords(path: Path) -> str:
        seen_paths.append(path)
        return "hunter2"

    provider = thumbs.PdfiumThumbProvider(passwords=passwords)
    collector = _Collector()
    provider.thumb_ready.connect(collector.on_ready)

    provider.request("k", pdf_path, 1, 200)
    qtbot.waitUntil(lambda: len(collector.received) == 1, timeout=2000)

    assert not collector.received[0][2].isNull()
    assert seen_paths == [pdf_path]
    provider.close()


def test_wrong_password_delivers_null_image(qtbot, tmp_path):
    pdf_path = tmp_path / "secret.pdf"
    src = pikepdf.new()
    src.add_blank_page(page_size=(200, 100))
    src.save(pdf_path, encryption=pikepdf.Encryption(owner="owner", user="hunter2"))

    provider = thumbs.PdfiumThumbProvider(passwords=lambda _path: "wrong")
    collector = _Collector()
    provider.thumb_ready.connect(collector.on_ready)

    provider.request("k", pdf_path, 1, 200)
    qtbot.waitUntil(lambda: len(collector.received) == 1, timeout=2000)

    assert collector.received[0][2].isNull()
    provider.close()


def test_request_sizes_delivers_page_dimensions(qtbot, tmp_path, provider):
    pdf_path = _make_half_pdf(tmp_path / "half.pdf", width=200, height=100, pages=3)
    collector = _SizesCollector()
    provider.sizes_ready.connect(collector.on_ready)

    provider.request_sizes("k", pdf_path, 3)
    qtbot.waitUntil(lambda: len(collector.received) == 1, timeout=2000)

    key, sizes = collector.received[0]
    assert key == "k"
    assert sizes == {1: (200.0, 100.0), 2: (200.0, 100.0), 3: (200.0, 100.0)}


def test_request_sizes_missing_file_delivers_empty_dict(qtbot, tmp_path, provider):
    collector = _SizesCollector()
    provider.sizes_ready.connect(collector.on_ready)

    provider.request_sizes("k", tmp_path / "nope.pdf", 3)
    qtbot.waitUntil(lambda: len(collector.received) == 1, timeout=2000)

    assert collector.received[0] == ("k", {})


def test_request_sizes_corrupt_file_delivers_empty_dict(qtbot, tmp_path, provider):
    bad_path = tmp_path / "corrupt.pdf"
    bad_path.write_bytes(b"not a pdf at all")
    collector = _SizesCollector()
    provider.sizes_ready.connect(collector.on_ready)

    provider.request_sizes("k", bad_path, 1)
    qtbot.waitUntil(lambda: len(collector.received) == 1, timeout=2000)

    assert collector.received[0] == ("k", {})


def test_request_sizes_skips_pages_past_the_end(qtbot, tmp_path, provider):
    pdf_path = _make_half_pdf(tmp_path / "half.pdf", pages=2)
    collector = _SizesCollector()
    provider.sizes_ready.connect(collector.on_ready)

    provider.request_sizes("k", pdf_path, 5)  # only 2 real pages exist
    qtbot.waitUntil(lambda: len(collector.received) == 1, timeout=2000)

    _key, sizes = collector.received[0]
    assert set(sizes) == {1, 2}


def test_request_sizes_supersedes_earlier_queued_request_for_same_key(
    qtbot, provider, tmp_path, monkeypatch
):
    pdf_path = _make_half_pdf(tmp_path / "half.pdf", pages=6)
    real_render = thumbs.render_page
    started = threading.Event()
    release = threading.Event()

    def blocking_render(doc, page, width_px):
        started.set()
        release.wait(timeout=5)
        return real_render(doc, page, width_px)

    monkeypatch.setattr(thumbs, "render_page", blocking_render)

    collector = _SizesCollector()
    provider.sizes_ready.connect(collector.on_ready)

    provider.request("blocker", pdf_path, 1, 100)
    assert started.wait(timeout=5)

    provider.request_sizes("k", pdf_path, 2)
    provider.request_sizes("k", pdf_path, 4)  # supersedes the queued 2-page request
    release.set()

    qtbot.waitUntil(lambda: len(collector.received) == 1, timeout=2000)
    _key, sizes = collector.received[0]
    assert set(sizes) == {1, 2, 3, 4}


def test_size_requests_are_served_before_queued_renders(qtbot, provider, tmp_path, monkeypatch):
    pdf_path = _make_half_pdf(tmp_path / "half.pdf", pages=2)
    real_render = thumbs.render_page
    started = threading.Event()
    release = threading.Event()

    def blocking_render(doc, page, width_px):
        if page == 1:
            started.set()
            release.wait(timeout=5)
        return real_render(doc, page, width_px)

    monkeypatch.setattr(thumbs, "render_page", blocking_render)

    order = []
    provider.thumb_ready.connect(lambda *_a: order.append("render"))
    provider.sizes_ready.connect(lambda *_a: order.append("sizes"))

    provider.request("k", pdf_path, 1, 100)
    assert started.wait(timeout=5)

    provider.request("k", pdf_path, 2, 100)  # queued render, waiting behind page 1
    provider.request_sizes("k", pdf_path, 2)  # queued after, but served first
    release.set()

    qtbot.waitUntil(lambda: len(order) == 3, timeout=2000)
    assert order == ["render", "sizes", "render"]


def test_cancel_drops_pending_size_request(qtbot, provider, tmp_path, monkeypatch):
    pdf_path = _make_half_pdf(tmp_path / "half.pdf", pages=2)
    real_render = thumbs.render_page
    started = threading.Event()
    release = threading.Event()

    def blocking_render(doc, page, width_px):
        started.set()
        release.wait(timeout=5)
        return real_render(doc, page, width_px)

    monkeypatch.setattr(thumbs, "render_page", blocking_render)

    collector = _SizesCollector()
    provider.sizes_ready.connect(collector.on_ready)

    provider.request("blocker", pdf_path, 1, 100)
    assert started.wait(timeout=5)

    provider.request_sizes("cancel_me", pdf_path, 2)
    provider.cancel("cancel_me")
    release.set()

    qtbot.wait(200)
    assert collector.received == []


def test_request_sizes_after_close_is_ignored(qtbot, tmp_path):
    pdf_path = _make_half_pdf(tmp_path / "half.pdf")
    provider = thumbs.PdfiumThumbProvider()
    collector = _SizesCollector()
    provider.sizes_ready.connect(collector.on_ready)
    provider.close()

    provider.request_sizes("k", pdf_path, 1)
    qtbot.wait(200)

    assert collector.received == []


def test_new_key_for_rewritten_file_reopens_it(qtbot, tmp_path, provider):
    pdf_path = _make_half_pdf(tmp_path / "doc.pdf", width=200, height=100)
    collector = _Collector()
    provider.thumb_ready.connect(collector.on_ready)
    provider.request("v1", pdf_path, 1, 100)
    qtbot.waitUntil(lambda: len(collector.received) == 1, timeout=2000)
    _make_half_pdf(pdf_path, width=100, height=300)
    provider.request("v2", pdf_path, 1, 100)
    qtbot.waitUntil(lambda: len(collector.received) == 2, timeout=2000)
    assert [image.height() for _key, _page, image in collector.received] == [50, 300]


@pytest.mark.parametrize("locked", [False, True])
def test_open_pdf_reads_from_memory_where_open_files_are_locked(tmp_path, monkeypatch, locked):
    monkeypatch.setattr(thumbs, "_OPEN_FILES_ARE_LOCKED", locked)
    pdf_path = _make_half_pdf(tmp_path / "doc.pdf", width=200, height=100)
    doc = thumbs.open_pdf(pdf_path)
    try:
        if locked:
            pdf_path.unlink()
        image = thumbs.render_page(doc, 1, 100)
        assert (image.width(), image.height()) == (100, 50)
    finally:
        doc.close()


def test_a_directory_path_gives_no_sizes_and_no_image(qtbot, tmp_path, provider, monkeypatch):
    monkeypatch.setattr(thumbs, "_OPEN_FILES_ARE_LOCKED", True)
    collector = _Collector()
    provider.thumb_ready.connect(collector.on_ready)
    sizes = []
    provider.sizes_ready.connect(lambda key, got: sizes.append(got))
    provider.request_sizes("d", tmp_path, 1)
    provider.request("d", tmp_path, 1, 100)
    qtbot.waitUntil(lambda: bool(sizes and collector.received), timeout=2000)
    assert sizes == [{}]
    assert collector.received[0][2].isNull()
