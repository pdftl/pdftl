# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/gui/test_picker.py

"""Tests for picker: the Briss-style CoordinatePicker dialog."""

from pathlib import Path

import pikepdf
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from pdftl.gui import picker
from pdftl.gui.page_geometry import PageGeometry


def _make_pdf(
    path: Path,
    page_specs: list[tuple[tuple[float, float, float, float], tuple, int]],
    password: str | None = None,
) -> Path:
    """`page_specs`: list of (mediabox, square_or_None, rotate)."""
    pdf = pikepdf.new()
    for mediabox, _square, _rotate in page_specs:
        w, h = mediabox[2] - mediabox[0], mediabox[3] - mediabox[1]
        pdf.add_blank_page(page_size=(w, h))
    for page, (mediabox, square, rotate) in zip(pdf.pages, page_specs):
        page.MediaBox = pikepdf.Array(mediabox)
        if rotate:
            page.Rotate = rotate
        if square is not None:
            sx, sy, sw, sh = square
            content = f"0 0 0 rg {sx} {sy} {sw} {sh} re f".encode()
            page.Contents = pdf.make_stream(content)
    if password is not None:
        pdf.save(path, encryption=pikepdf.Encryption(owner="owner-pw", user=password, R=4))
    else:
        pdf.save(path)
    return path


def _is_dark(image, x: float, y: float) -> bool:
    color = image.pixelColor(int(x), int(y))
    return color.red() + color.green() + color.blue() < 384


@pytest.fixture
def dlg_factory(qtbot):
    # qtbot.addWidget only weak-references the dialog, which is not enough
    # to keep a still-rendering composite worker thread alive until this
    # fixture's own teardown; hold a strong ref and stop each worker
    # explicitly, or Python may garbage-collect (and abort on) a QThread
    # mid-render as soon as a test function returns.
    dialogs: list[picker.CoordinatePicker] = []

    def make(path, pages, passwords=None):
        d = picker.CoordinatePicker(path, pages, passwords=passwords)
        qtbot.addWidget(d)
        d.show()
        dialogs.append(d)
        return d

    yield make
    for d in dialogs:
        d._stop_renderer()


# --- grouping ---


def test_groups_pages_by_geometry(tmp_path, dlg_factory):
    specs = [
        ((0, 0, 200, 100), None, 0),
        ((0, 0, 300, 150), None, 0),
        ((0, 0, 200, 100), None, 0),
    ]
    path = _make_pdf(tmp_path / "g.pdf", specs)
    dlg = dlg_factory(path, [1, 2, 3])

    assert dlg.groups == [[1, 3], [2]]


# --- composite blending ---


def test_composite_blends_both_squares(qtbot, tmp_path, dlg_factory):
    mediabox = (0, 0, 200, 100)
    square_a = (10, 10, 20, 20)  # bottom-left-ish
    square_b = (150, 60, 20, 20)  # top-right-ish
    specs = [(mediabox, square_a, 0), (mediabox, square_b, 0)]
    path = _make_pdf(tmp_path / "blend.pdf", specs)
    dlg = dlg_factory(path, [1, 2])

    qtbot.waitUntil(lambda: dlg.canvas.has_image(), timeout=3000)
    image = dlg.canvas._pixmap.toImage()
    geometry = dlg.groups and dlg.geometries[dlg.groups[0][0]]

    for square in (square_a, square_b):
        cx = square[0] + square[2] / 2
        cy = square[1] + square[3] / 2
        dx, dy = geometry.user_to_displayed(cx, cy)
        px, py = geometry.displayed_to_pixels(dx, dy, dlg.canvas._scale)
        assert _is_dark(image, px, py), f"expected dark pixel for square {square}"

    # A corner untouched by either square stays light.
    dx, dy = geometry.user_to_displayed(195.0, 5.0)
    px, py = geometry.displayed_to_pixels(dx, dy, dlg.canvas._scale)
    assert not _is_dark(image, px, py)


def test_composite_caps_blended_pages(monkeypatch, tmp_path, dlg_factory):
    monkeypatch.setattr(picker, "MAX_BLEND_PAGES", 2)
    mediabox = (0, 0, 100, 100)
    specs = [(mediabox, None, 0)] * 3
    path = _make_pdf(tmp_path / "cap.pdf", specs)
    dlg = dlg_factory(path, [1, 2, 3])

    assert dlg._renderer is not None
    assert dlg._renderer.pages == [1, 2]


# --- keyboard nudge ---


def test_keyboard_nudges_move_point_in_displayed_and_user_space(tmp_path, dlg_factory):
    mediabox = (0, 0, 200, 100)
    path = _make_pdf(tmp_path / "nudge.pdf", [(mediabox, None, 0)])
    dlg = dlg_factory(path, [1])
    dlg.canvas.setFocus()

    assert dlg.canvas.point() == pytest.approx((100.0, 50.0))

    QTest.keyClick(dlg.canvas, Qt.Key.Key_Right)
    assert dlg.canvas.point() == pytest.approx((101.0, 50.0))

    QTest.keyClick(dlg.canvas, Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier)
    assert dlg.canvas.point() == pytest.approx((101.0, 60.0))

    QTest.keyClick(dlg.canvas, Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier)
    assert dlg.canvas.point() == pytest.approx((100.9, 60.0))

    picked = dlg._build_picked_point()
    geometry: PageGeometry = dlg.geometries[1]
    expected_user = geometry.displayed_to_user(*dlg.canvas.point())
    assert picked.user == pytest.approx(expected_user)


def test_home_end_jump_to_page_corners(tmp_path, dlg_factory):
    mediabox = (0, 0, 200, 100)
    path = _make_pdf(tmp_path / "corners.pdf", [(mediabox, None, 0)])
    dlg = dlg_factory(path, [1])
    dlg.canvas.setFocus()

    QTest.keyClick(dlg.canvas, Qt.Key.Key_Home)
    assert dlg.canvas.point() == pytest.approx((0.0, 0.0))

    QTest.keyClick(dlg.canvas, Qt.Key.Key_End)
    assert dlg.canvas.point() == pytest.approx((200.0, 100.0))


def test_nudge_clamps_to_page_bounds(tmp_path, dlg_factory):
    mediabox = (0, 0, 200, 100)
    path = _make_pdf(tmp_path / "clamp.pdf", [(mediabox, None, 0)])
    dlg = dlg_factory(path, [1])
    dlg.canvas.set_point(0.0, 0.0)

    QTest.keyClick(dlg.canvas, Qt.Key.Key_Left)
    assert dlg.canvas.point() == pytest.approx((0.0, 0.0))

    dlg.canvas.set_point(200.0, 100.0)
    QTest.keyClick(dlg.canvas, Qt.Key.Key_Right)
    assert dlg.canvas.point() == pytest.approx((200.0, 100.0))


# --- mouse ---


def test_mouse_click_sets_point(tmp_path, dlg_factory):
    mediabox = (0, 0, 200, 100)
    path = _make_pdf(tmp_path / "click.pdf", [(mediabox, None, 0)])
    dlg = dlg_factory(path, [1])

    QTest.mouseClick(dlg.canvas, Qt.MouseButton.LeftButton, pos=QPoint(40, 30))

    scale = dlg.canvas._scale
    dx, dy = dlg.canvas.point()
    assert dx == pytest.approx(40 / scale, abs=0.6)
    assert dy == pytest.approx(30 / scale, abs=0.6)


# --- unit combo ---


def test_unit_combo_changes_readout(tmp_path, dlg_factory):
    mediabox = (0, 0, 200, 100)
    path = _make_pdf(tmp_path / "unit.pdf", [(mediabox, None, 0)])
    dlg = dlg_factory(path, [1])

    dlg.unit_combo.setCurrentText("pt")
    assert "pt" in dlg.readout.text()

    dlg.unit_combo.setCurrentText("mm")
    assert "mm" in dlg.readout.text()
    assert "pt" not in dlg.readout.text()


# --- group stepping ---


def test_group_stepping_wraps_with_pageup_pagedown(tmp_path, dlg_factory):
    specs = [((0, 0, 200, 100), None, 0), ((0, 0, 300, 150), None, 0)]
    path = _make_pdf(tmp_path / "steps.pdf", specs)
    dlg = dlg_factory(path, [1, 2])
    assert dlg.group_index == 0
    assert "Group 1 of 2" in dlg.status.text()

    QTest.keyClick(dlg, Qt.Key.Key_PageDown)
    assert dlg.group_index == 1
    assert "Group 2 of 2" in dlg.status.text()

    QTest.keyClick(dlg, Qt.Key.Key_PageDown)
    assert dlg.group_index == 0

    QTest.keyClick(dlg, Qt.Key.Key_PageUp)
    assert dlg.group_index == 1


# --- accept / cancel ---


def test_enter_accepts_and_returns_point(tmp_path, dlg_factory):
    mediabox = (0, 0, 200, 100)
    path = _make_pdf(tmp_path / "enter.pdf", [(mediabox, None, 0)])
    dlg = dlg_factory(path, [1])

    QTest.keyClick(dlg, Qt.Key.Key_Return)

    assert dlg.result() == QDialog.DialogCode.Accepted
    assert dlg.picked_point is not None
    assert dlg.picked_point.pages == (1,)
    assert "," in dlg.picked_point.token


def test_escape_cancels(tmp_path, dlg_factory):
    mediabox = (0, 0, 200, 100)
    path = _make_pdf(tmp_path / "esc.pdf", [(mediabox, None, 0)])
    dlg = dlg_factory(path, [1])
    assert dlg.isVisible()

    QTest.keyClick(dlg, Qt.Key.Key_Escape)

    assert not dlg.isVisible()


# --- passwords ---


def test_password_callback_used_for_encrypted_pdf(qtbot, tmp_path, dlg_factory):
    mediabox = (0, 0, 200, 100)
    path = _make_pdf(tmp_path / "enc.pdf", [(mediabox, None, 0)], password="secret")

    dlg = dlg_factory(path, [1], passwords=lambda _p: "secret")

    assert dlg.error is None
    assert dlg.groups == [[1]]
    qtbot.waitUntil(lambda: dlg.canvas.has_image(), timeout=3000)


def test_wrong_password_sets_error(tmp_path, dlg_factory):
    mediabox = (0, 0, 200, 100)
    path = _make_pdf(tmp_path / "enc2.pdf", [(mediabox, None, 0)], password="secret")

    dlg = dlg_factory(path, [1], passwords=lambda _p: "wrong")

    assert dlg.error is not None
    assert "Error" in dlg.status.text()
    assert dlg.groups == []


def test_error_state_private_helpers_are_all_no_ops(tmp_path, dlg_factory):
    # With no groups (password error), every helper that depends on a
    # current group must degrade to a safe no-op/None rather than IndexError.
    mediabox = (0, 0, 200, 100)
    path = _make_pdf(tmp_path / "enc4.pdf", [(mediabox, None, 0)], password="secret")
    dlg = dlg_factory(path, [1], passwords=lambda _p: "wrong")

    assert dlg._group_geometry() is None
    dlg._load_group(0)
    assert dlg._renderer is None
    dlg._update_status()
    assert "Error" in dlg.status.text()
    dlg._update_readout()
    assert dlg.readout.text() == ""
    dlg._step_group(1)
    assert dlg.group_index == 0
    assert dlg._build_picked_point() is None

    QTest.keyClick(dlg, Qt.Key.Key_Return)
    assert dlg.result() != QDialog.DialogCode.Accepted
    assert dlg.picked_point is None


def test_stale_group_signals_are_ignored(tmp_path, dlg_factory):
    mediabox = (0, 0, 200, 100)
    path = _make_pdf(tmp_path / "stale.pdf", [(mediabox, None, 0)])
    dlg = dlg_factory(path, [1])

    dlg._on_composite_ready(999, picker.QImage())
    assert not dlg.canvas.has_image()
    dlg._on_composite_failed(999, "stale failure")
    assert "stale failure" not in dlg.status.text()


def test_no_password_callback_raises_for_encrypted_pdf(qtbot, tmp_path):
    mediabox = (0, 0, 200, 100)
    path = _make_pdf(tmp_path / "enc3.pdf", [(mediabox, None, 0)], password="secret")

    dlg = picker.CoordinatePicker(path, [1])
    try:
        assert dlg.error is not None
    finally:
        dlg.close()


# --- pick_point function API ---


def test_pick_point_returns_none_on_reject(qtbot, monkeypatch, tmp_path):
    # A monkeypatched exec() bypasses the real modal loop, so it must still
    # go through reject()/done() itself to stop the composite renderer's
    # pending timer. qtbot is requested (unused otherwise) so pytest-qt's
    # QApplication exists before CoordinatePicker() -- constructing any
    # QWidget with none running is undefined behaviour and can abort.
    path = _make_pdf(tmp_path / "reject.pdf", [((0, 0, 100, 100), None, 0)])

    def fake_exec(self):
        self.reject()
        return self.result()

    monkeypatch.setattr(picker.CoordinatePicker, "exec", fake_exec)

    assert picker.pick_point(None, path, [1]) is None


def test_pick_point_returns_picked_point_on_accept(qtbot, monkeypatch, tmp_path):
    path = _make_pdf(tmp_path / "accept.pdf", [((0, 0, 100, 100), None, 0)])
    expected = picker.PickedPoint(
        user=(1.0, 2.0), displayed=(3.0, 4.0), pages=(1,), unit="pt", token="1pt,2pt"
    )

    def fake_exec(self):
        self.picked_point = expected
        self.accept()
        return self.result()

    monkeypatch.setattr(picker.CoordinatePicker, "exec", fake_exec)

    assert picker.pick_point(None, path, [1]) is expected


# --- format_point_token ---


def test_format_point_token_absolute_unit():
    assert picker.format_point_token(70.0, 50.0, "pt") == "70pt,50pt"


def test_format_point_token_percent_relative_to_origin_and_total():
    token = picker.format_point_token(
        60.0, 30.0, "%", total_x=200.0, total_y=100.0, origin_x=10.0, origin_y=10.0
    )
    assert token == "25%,20%"


# --- composite render failure ---


def test_composite_render_failure_sets_status(qtbot, tmp_path, dlg_factory, monkeypatch):
    mediabox = (0, 0, 100, 100)
    path = _make_pdf(tmp_path / "fail.pdf", [(mediabox, None, 0)])
    dlg = dlg_factory(path, [1])

    def boom(*_args, **_kwargs):
        raise OSError("boom")

    monkeypatch.setattr(picker.pdfium, "PdfDocument", boom)
    dlg._load_group(0)

    qtbot.waitUntil(lambda: "Render failed" in dlg.status.text(), timeout=3000)


def test_render_next_page_failure_emits_failed_and_stops(qtbot, tmp_path):
    mediabox = (0, 0, 100, 100)
    path = _make_pdf(tmp_path / "badpage.pdf", [(mediabox, None, 0)])
    renderer = picker._CompositeRenderer(path, [99], None, 2.0, 0)
    failures = []
    renderer.failed.connect(lambda idx, msg: failures.append((idx, msg)))
    renderer.start()

    qtbot.waitUntil(lambda: len(failures) == 1, timeout=3000)
    assert failures[0][0] == 0


def test_finish_is_a_no_op_when_cancelled_or_empty(tmp_path):
    path = tmp_path / "unused.pdf"
    ready = []

    cancelled = picker._CompositeRenderer(path, [], None, 1.0, 0)
    cancelled.ready.connect(lambda idx, img: ready.append((idx, img)))
    cancelled._cancelled = True
    cancelled._finish()

    empty = picker._CompositeRenderer(path, [], None, 1.0, 0)
    empty.ready.connect(lambda idx, img: ready.append((idx, img)))
    empty._finish()

    assert ready == []


# --- composite scale ---


def test_composite_scale_caps_large_pages():
    scale = picker._composite_scale(2000.0, 1000.0)
    assert scale * 2000.0 == pytest.approx(picker._MAX_CANVAS_PX)


def test_composite_scale_uses_default_for_small_pages():
    assert picker._composite_scale(200.0, 100.0) == picker._MIN_RENDER_SCALE


# --- canvas edge cases ---


def test_canvas_without_geometry_ignores_input(qtbot):
    canvas = picker._PickerCanvas()
    qtbot.addWidget(canvas)

    canvas.set_scale(2.0)
    canvas.set_point(5.0, 5.0)
    assert canvas.point() is None

    QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=QPoint(1, 1))
    assert canvas.point() is None

    QTest.keyClick(canvas, Qt.Key.Key_Right)
    assert canvas.point() is None


def test_mouse_drag_updates_point(tmp_path, dlg_factory):
    mediabox = (0, 0, 200, 100)
    path = _make_pdf(tmp_path / "drag.pdf", [(mediabox, None, 0)])
    dlg = dlg_factory(path, [1])

    QTest.mousePress(dlg.canvas, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    QTest.mouseMove(dlg.canvas, QPoint(60, 40))
    QTest.mouseRelease(dlg.canvas, Qt.MouseButton.LeftButton, pos=QPoint(60, 40))

    scale = dlg.canvas._scale
    dx, dy = dlg.canvas.point()
    assert dx == pytest.approx(60 / scale, abs=0.6)
    assert dy == pytest.approx(40 / scale, abs=0.6)


def test_mouse_move_without_button_does_not_move_point(tmp_path, dlg_factory):
    mediabox = (0, 0, 200, 100)
    path = _make_pdf(tmp_path / "hover.pdf", [(mediabox, None, 0)])
    dlg = dlg_factory(path, [1])
    before = dlg.canvas.point()
    # QTest.mouseMove sends QTest's global button state, which an earlier
    # test's unfinished click can leave pressed.
    hover = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(60, 40),
        QPointF(dlg.canvas.mapToGlobal(QPoint(60, 40))),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(dlg.canvas, hover)

    assert dlg.canvas.point() == before


def test_pdfium_calls_wait_for_the_thumbnail_workers_lock(tmp_path):
    import threading

    from pdftl.gui.thumbs import PDFIUM_LOCK

    path = _make_pdf(tmp_path / "lock.pdf", [((0, 0, 100, 100), None, 0)])
    renderer = picker._CompositeRenderer(path, [1], None, 1.0, 0)
    for call in (renderer._open_document, renderer._render_next_page, renderer._close_document):
        with PDFIUM_LOCK:
            worker = threading.Thread(target=call)
            worker.start()
            worker.join(0.3)
            assert worker.is_alive(), call.__name__
        worker.join(5)
        assert not worker.is_alive()
    assert renderer._accumulator.shape[:2] == (100, 100)
