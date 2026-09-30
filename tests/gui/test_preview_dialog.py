# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/gui/test_preview_dialog.py

"""Tests for the large page preview."""

import math
from pathlib import Path

import pikepdf
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QKeySequence, QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pdftl.gui import keymap, preview_dialog
from pdftl.gui.preview_dialog import (
    FIT_MARGIN,
    MAX_RENDER_PX,
    MAX_ZOOM,
    MIN_ZOOM,
    ZOOM_STEP,
    PreviewDialog,
    hint_text,
)
from pdftl.gui.thumbs import PdfiumThumbProvider


@pytest.fixture
def provider(qtbot):
    p = PdfiumThumbProvider()
    yield p
    p.close()


def _make_half_pdf(path: Path, width=200, height=100, rotate=0) -> Path:
    pdf = pikepdf.new()
    content = (
        f"0 0 0 rg 0 0 {width / 2} {height} re f 1 1 1 rg {width / 2} 0 {width / 2} {height} re f"
    ).encode()
    pdf.add_blank_page(page_size=(width, height))
    pdf.pages[0].Contents = pdf.make_stream(content)
    if rotate:
        pdf.pages[0].Rotate = rotate
    pdf.save(path)
    return path


TALL = [(600, 800)]
"""Fits at well under a third of MAX_ZOOM, so zooming in has room."""


def _make_sized_pdf(path: Path, sizes) -> Path:
    pdf = pikepdf.new()
    for size in sizes:
        pdf.add_blank_page(page_size=size)
    pdf.save(path)
    return path


def _open(qtbot, provider, path: Path, page: int, total: int) -> PreviewDialog:
    dlg = PreviewDialog(provider, "kk", path, page, total)
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitExposed(dlg)
    _rendered(qtbot, dlg)
    return dlg


def _rendered(qtbot, dlg: PreviewDialog) -> None:
    """Wait for the render at the current page and zoom, not a stretched stand-in."""

    def done():
        pixmap = dlg.image.pixmap()
        return (
            dlg._want is not None
            and not pixmap.isNull()
            and abs(pixmap.width() - dlg._want[1]) <= 1
            and dlg._want[0] == dlg.page
            and pixmap.cacheKey() != getattr(dlg, "_stand_in", None)
        )

    qtbot.waitUntil(done, timeout=5000)


def _px_per_pt(dlg: PreviewDialog) -> float:
    """Printed size: a point is 1/72 inch, and the screen has logicalDpiX pixels per inch."""
    return dlg.logicalDpiX() / 72


def _press(qtbot, dlg, key, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
    """Keys go where a user's would: the dialog's focus widget."""
    dlg.activateWindow()
    qtbot.waitUntil(dlg.isActiveWindow, timeout=2000)
    target = QApplication.focusWidget()
    assert target is not None and dlg.isAncestorOf(target)
    QTest.keyClick(target, key, modifiers)


def _mouse(dlg, kind, pos, button, buttons=None) -> None:
    viewport = dlg.scroll.viewport()
    held = button if buttons is None else buttons
    event = QMouseEvent(
        kind,
        QPointF(pos),
        QPointF(viewport.mapToGlobal(pos)),
        button,
        held,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(viewport, event)


def _wheel(dlg, pos: QPoint, dy: int, modifiers, dx: int = 0) -> bool:
    viewport = dlg.scroll.viewport()
    event = QWheelEvent(
        QPointF(pos),
        QPointF(viewport.mapToGlobal(pos)),
        QPoint(0, 0),
        QPoint(dx, dy),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(viewport, event)
    return event.isAccepted()


def _scroll(dlg) -> tuple[int, int]:
    return dlg.scroll.horizontalScrollBar().value(), dlg.scroll.verticalScrollBar().value()


# --- Rendering ---


def test_previewdialog_renders_unrotated_page(qtbot, tmp_path, provider):
    pdf_path = _make_half_pdf(tmp_path / "half.pdf", width=200, height=100)
    dlg = _open(qtbot, provider, pdf_path, 1, 1)
    pixmap = dlg.image.pixmap()
    assert pixmap.width() > pixmap.height()  # landscape, matches 200x100
    image = pixmap.toImage()
    left = image.pixelColor(10, pixmap.height() // 2)
    right = image.pixelColor(pixmap.width() - 10, pixmap.height() // 2)
    assert (left.red(), left.green(), left.blue()) == (0, 0, 0)
    assert (right.red(), right.green(), right.blue()) == (255, 255, 255)
    assert dlg.windowTitle() == "Page 1 of 1"


def test_previewdialog_renders_rotated_page(qtbot, tmp_path, provider):
    pdf_path = _make_half_pdf(tmp_path / "half.pdf", width=200, height=100, rotate=90)
    dlg = _open(qtbot, provider, pdf_path, 1, 1)
    pixmap = dlg.image.pixmap()
    assert pixmap.width() < pixmap.height()  # portrait after rotation
    image = pixmap.toImage()
    top = image.pixelColor(pixmap.width() // 2, 10)
    bottom = image.pixelColor(pixmap.width() // 2, pixmap.height() - 10)
    assert (top.red(), top.green(), top.blue()) == (0, 0, 0)
    assert (bottom.red(), bottom.green(), bottom.blue()) == (255, 255, 255)


def test_opening_fits_the_first_page_in_the_window(qtbot, tmp_path, provider):
    dlg = _open(qtbot, provider, _make_sized_pdf(tmp_path / "a.pdf", [(200, 400)]), 1, 1)
    room = dlg.scroll.viewport().size()
    image = dlg.image.size()
    assert image.width() <= room.width() and image.height() <= room.height()
    assert abs(image.height() - (room.height() - FIT_MARGIN)) <= 1  # tall page: height limits
    assert abs(image.width() * 2 - image.height()) <= 2
    assert dlg.zoom_label.text() == f"{round(dlg.zoom * 100)}%"


def test_every_page_shows_at_the_same_scale(qtbot, tmp_path, provider):
    path = _make_sized_pdf(tmp_path / "a.pdf", [(200, 100), (400, 300)])
    dlg = _open(qtbot, provider, path, 1, 2)
    first = dlg.image.size()
    _press(qtbot, dlg, Qt.Key.Key_Right)
    _rendered(qtbot, dlg)
    second = dlg.image.size()
    assert abs(second.width() - 2 * first.width()) <= 1
    assert abs(second.height() - 3 * first.height()) <= 2


def test_hundred_percent_is_the_printed_size(qtbot, tmp_path, provider):
    dlg = _open(qtbot, provider, _make_sized_pdf(tmp_path / "a.pdf", [(144, 72)]), 1, 1)
    dlg._set_zoom(1.0, None)
    _rendered(qtbot, dlg)
    assert dlg.zoom_label.text() == "100%"
    assert dlg.image.width() == round(144 * _px_per_pt(dlg))
    assert dlg.image.height() == round(72 * _px_per_pt(dlg))


def test_a_zoom_shows_the_old_image_stretched_until_the_new_render(qtbot, tmp_path, provider):
    dlg = _open(qtbot, provider, _make_sized_pdf(tmp_path / "a.pdf", [(200, 100)]), 1, 1)
    dlg.zoom_by(2)
    stand_in = dlg.image.pixmap()
    assert not stand_in.isNull()
    assert stand_in.deviceIndependentSize().toSize() == dlg.image.size()
    dlg._stand_in = stand_in.cacheKey()
    _rendered(qtbot, dlg)


def test_a_new_page_says_it_is_rendering(qtbot, tmp_path, provider):
    path = _make_sized_pdf(tmp_path / "a.pdf", [(200, 100), (200, 100)])
    dlg = _open(qtbot, provider, path, 1, 2)
    dlg.show_page(2)
    assert dlg.image.pixmap().isNull()
    assert dlg.image.text() == "Rendering..."


def test_an_unreadable_page_says_so(qtbot, tmp_path, provider):
    dlg = _open(qtbot, provider, _make_sized_pdf(tmp_path / "a.pdf", [(200, 100)]), 1, 2)
    _press(qtbot, dlg, Qt.Key.Key_Right)
    assert dlg.page == 2
    assert dlg.image.text() == "Could not read this page"
    assert dlg.zoom_label.text() == ""
    dlg._thumb_ready(dlg.key, 2, QImage(10, 10, QImage.Format.Format_RGB32))
    assert dlg.image.pixmap().isNull()
    zoom = dlg.zoom
    dlg.fit()
    assert dlg.zoom == zoom  # nothing to fit to; the other pages keep their zoom
    dlg.zoom_by(1000)
    assert dlg.zoom == MAX_ZOOM


def test_an_unreadable_first_page_opens_at_printed_size(qtbot, tmp_path, provider):
    dlg = PreviewDialog(provider, "kk", _make_sized_pdf(tmp_path / "a.pdf", [(200, 100)]), 2, 2)
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: dlg.zoom is not None, timeout=5000)
    assert dlg.zoom == 1.0
    assert dlg.image.text() == "Could not read this page"


def test_sizes_for_another_key_are_ignored(qtbot, tmp_path, provider):
    dlg = _open(qtbot, provider, _make_sized_pdf(tmp_path / "a.pdf", [(200, 100)]), 1, 1)
    zoom = dlg.zoom
    dlg._sizes_ready("other", {1: (10.0, 10.0)})
    assert dlg.zoom == zoom


def test_previewdialog_ignores_stale_and_foreign_renders(qtbot, tmp_path, provider):
    dlg = _open(qtbot, provider, _make_sized_pdf(tmp_path / "a.pdf", [(200, 100)]), 1, 1)
    before = dlg.image.pixmap().toImage()
    other = QImage(dlg._want[1], 10, QImage.Format.Format_RGB32)
    other.fill(Qt.GlobalColor.red)
    dlg._thumb_ready("not-the-key", 1, other)
    dlg._thumb_ready(dlg.key, 99, other)
    dlg._thumb_ready(dlg.key, 1, QImage())
    narrow = QImage(dlg._want[1] // 2, 10, QImage.Format.Format_RGB32)
    dlg._thumb_ready(dlg.key, 1, narrow)  # an earlier zoom's render
    assert dlg.image.pixmap().toImage() == before


def test_zoom_and_fit_wait_for_the_page_sizes(qtbot, tmp_path, provider):
    dlg = PreviewDialog(provider, "kk", _make_sized_pdf(tmp_path / "a.pdf", [(200, 100)]), 1, 1)
    qtbot.addWidget(dlg)
    dlg.zoom_by(2)
    dlg.fit()
    assert dlg.zoom is None
    assert dlg.image.text() == "Rendering..."
    qtbot.waitUntil(lambda: dlg.zoom is not None, timeout=5000)


# --- Zoom limits ---


def test_zoom_stays_within_its_limits(qtbot, tmp_path, provider):
    dlg = _open(qtbot, provider, _make_sized_pdf(tmp_path / "a.pdf", [(200, 100)]), 1, 1)
    dlg._set_zoom(1000, None)
    assert dlg.zoom == MAX_ZOOM
    dlg._set_zoom(0.0001, None)
    assert dlg.zoom == MIN_ZOOM


def test_a_huge_page_zooms_only_as_far_as_the_render_budget(qtbot, tmp_path, provider):
    dlg = _open(qtbot, provider, _make_sized_pdf(tmp_path / "a.pdf", [(14400, 14400)]), 1, 1)
    dlg._set_zoom(MAX_ZOOM, None)
    scale = dlg.zoom * _px_per_pt(dlg) * dlg.devicePixelRatioF()
    assert (14400 * scale) ** 2 <= MAX_RENDER_PX * 1.001
    assert dlg.zoom < MAX_ZOOM
    assert dlg.zoom == pytest.approx(math.sqrt(MAX_RENDER_PX) / 14400 / (scale / dlg.zoom))


# --- Keys ---


def test_previewdialog_navigation_keys_change_and_clamp_page(qtbot, tmp_path, provider):
    path = _make_sized_pdf(tmp_path / "a.pdf", [(200, 100)] * 3)
    dlg = _open(qtbot, provider, path, 1, 3)

    _press(qtbot, dlg, Qt.Key.Key_Right)
    assert dlg.page == 2
    _press(qtbot, dlg, Qt.Key.Key_PageDown)
    assert dlg.page == 3
    _press(qtbot, dlg, Qt.Key.Key_Right)  # clamp at total
    assert dlg.page == 3
    assert dlg.windowTitle() == "Page 3 of 3"

    _press(qtbot, dlg, Qt.Key.Key_Left)
    _press(qtbot, dlg, Qt.Key.Key_PageUp)
    _press(qtbot, dlg, Qt.Key.Key_Left)
    assert dlg.page == 1
    _press(qtbot, dlg, Qt.Key.Key_PageUp)  # clamp at 1
    assert dlg.page == 1


def test_zoom_keys_zoom_in_out_and_fit(qtbot, tmp_path, provider):
    dlg = _open(qtbot, provider, _make_sized_pdf(tmp_path / "a.pdf", TALL), 1, 1)
    fit = dlg.zoom
    ctrl = Qt.KeyboardModifier.ControlModifier
    _press(qtbot, dlg, Qt.Key.Key_Equal, ctrl)
    assert dlg.zoom == pytest.approx(fit * ZOOM_STEP)
    _press(qtbot, dlg, Qt.Key.Key_Plus, ctrl)
    assert dlg.zoom == pytest.approx(fit * ZOOM_STEP**2)
    _press(qtbot, dlg, Qt.Key.Key_Minus, ctrl)
    assert dlg.zoom == pytest.approx(fit * ZOOM_STEP)
    _press(qtbot, dlg, Qt.Key.Key_0, ctrl)
    assert dlg.zoom == pytest.approx(fit)
    assert dlg.zoom_label.text() == f"{round(fit * 100)}%"


def test_previewdialog_escape_closes(qtbot, tmp_path, provider):
    dlg = _open(qtbot, provider, _make_half_pdf(tmp_path / "half.pdf"), 1, 1)
    _press(qtbot, dlg, Qt.Key.Key_Escape)
    assert not dlg.isVisible()


def test_previewdialog_other_key_neither_moves_nor_closes(qtbot, tmp_path, provider):
    path = _make_sized_pdf(tmp_path / "a.pdf", [(200, 100)] * 3)
    dlg = _open(qtbot, provider, path, 1, 3)
    _press(qtbot, dlg, Qt.Key.Key_A)
    assert dlg.page == 1
    assert dlg.isVisible()


def test_the_hint_names_the_keys_from_the_keymap():
    native = QKeySequence.SequenceFormat.NativeText
    text = hint_text()
    for action_id in (
        "preview_prev_page",
        "preview_next_page",
        "zoom_in",
        "zoom_out",
        "zoom_reset",
        "close_preview",
    ):
        for key in keymap.keys_for(action_id):
            assert QKeySequence(key).toString(native) in text
    assert "Drag: pan" in text


# --- Mouse ---


def test_ctrl_wheel_zooms_about_the_pointer(qtbot, tmp_path, provider):
    dlg = _open(qtbot, provider, _make_sized_pdf(tmp_path / "a.pdf", TALL), 1, 1)
    ctrl = Qt.KeyboardModifier.ControlModifier
    dlg._set_zoom(dlg.zoom * 3, None)  # larger than the window, so it can scroll
    pointer = QPoint(100, 80)
    geometry = dlg.image.geometry()
    fx = (pointer.x() - geometry.x()) / geometry.width()
    fy = (pointer.y() - geometry.y()) / geometry.height()
    zoom = dlg.zoom
    assert _wheel(dlg, pointer, 120, ctrl)
    assert dlg.zoom == pytest.approx(zoom * ZOOM_STEP)
    geometry = dlg.image.geometry()
    assert abs(geometry.x() + fx * geometry.width() - pointer.x()) <= 1
    assert abs(geometry.y() + fy * geometry.height() - pointer.y()) <= 1
    _wheel(dlg, pointer, -120, ctrl)
    assert dlg.zoom == pytest.approx(zoom)


def test_a_plain_or_sideways_wheel_does_not_zoom(qtbot, tmp_path, provider):
    dlg = _open(qtbot, provider, _make_sized_pdf(tmp_path / "a.pdf", [(200, 100)]), 1, 1)
    zoom = dlg.zoom
    _wheel(dlg, QPoint(50, 50), 120, Qt.KeyboardModifier.NoModifier)
    _wheel(dlg, QPoint(50, 50), 0, Qt.KeyboardModifier.ControlModifier, dx=120)
    assert dlg.zoom == zoom


@pytest.mark.parametrize("button", preview_dialog.PAN_BUTTONS)
def test_dragging_pans_the_page(qtbot, tmp_path, provider, button):
    dlg = _open(qtbot, provider, _make_sized_pdf(tmp_path / "a.pdf", TALL), 1, 1)
    dlg._set_zoom(dlg.zoom * 3, None)
    before = _scroll(dlg)
    viewport = dlg.scroll.viewport()
    _mouse(dlg, QEvent.Type.MouseButtonPress, QPoint(200, 200), button)
    assert viewport.cursor().shape() == Qt.CursorShape.ClosedHandCursor
    _mouse(dlg, QEvent.Type.MouseMove, QPoint(170, 180), Qt.MouseButton.NoButton, button)
    _mouse(dlg, QEvent.Type.MouseButtonRelease, QPoint(170, 180), button, Qt.MouseButton.NoButton)
    assert _scroll(dlg) == (before[0] + 30, before[1] + 20)
    assert viewport.cursor().shape() == Qt.CursorShape.OpenHandCursor
    _mouse(dlg, QEvent.Type.MouseMove, QPoint(0, 0), Qt.MouseButton.NoButton)
    assert _scroll(dlg) == (before[0] + 30, before[1] + 20)


def test_other_buttons_do_not_pan(qtbot, tmp_path, provider):
    dlg = _open(qtbot, provider, _make_sized_pdf(tmp_path / "a.pdf", TALL), 1, 1)
    dlg._set_zoom(dlg.zoom * 3, None)
    before = _scroll(dlg)
    right = Qt.MouseButton.RightButton
    _mouse(dlg, QEvent.Type.MouseButtonPress, QPoint(200, 200), right)
    _mouse(dlg, QEvent.Type.MouseMove, QPoint(170, 180), Qt.MouseButton.NoButton, right)
    _mouse(dlg, QEvent.Type.MouseButtonRelease, QPoint(170, 180), right, Qt.MouseButton.NoButton)
    assert _scroll(dlg) == before
    left = Qt.MouseButton.LeftButton
    _mouse(dlg, QEvent.Type.MouseButtonPress, QPoint(200, 200), left)
    _mouse(dlg, QEvent.Type.MouseButtonRelease, QPoint(200, 200), right, left)
    assert dlg._drag is not None  # a right release does not end a left drag
