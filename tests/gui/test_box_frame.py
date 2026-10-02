# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/gui/test_box_frame.py

"""Tests for box_frame: click-anywhere focus, tint, right-click and drag start."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QContextMenuEvent, QMouseEvent, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pdftl.gui.box_frame import BoxFrame
from pdftl.gui.icons import ERROR_RED


class _Box(BoxFrame):
    def __init__(self, draggable=False):
        super().__init__("<b>Box</b>", draggable)
        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        self.label = QLabel("status")
        self.field = QLineEdit()
        self.off = QPushButton("off")
        self.off.setEnabled(False)
        self.skipped = QLabel("not watched")
        for w in (self.label, self.field, self.off, self.skipped):
            layout.addWidget(w)
        self.install_click_filters()

    def focus_target(self):
        return self.field

    def filters_child(self, child):
        return child is not self.skipped


def _shown(qtbot, box):
    """Show `box` beside a field that holds focus before each click; returns the field."""
    holder = QWidget()
    layout = QVBoxLayout(holder)
    outside = QLineEdit()
    layout.addWidget(outside)
    layout.addWidget(box)
    qtbot.addWidget(holder)
    box.holder = holder  # qtbot only holds a weak reference
    holder.show()
    qtbot.waitExposed(holder)
    holder.activateWindow()
    outside.setFocus()
    qtbot.waitUntil(outside.hasFocus)
    return outside


def _mouse(kind, widget, pos=QPoint(3, 3), button=Qt.MouseButton.LeftButton, buttons=None):
    local = QPointF(pos)
    return QMouseEvent(
        kind,
        local,
        QPointF(widget.mapToGlobal(pos)),
        button,
        button if buttons is None else buttons,
        Qt.KeyboardModifier.NoModifier,
    )


def _send(widget, kind, pos=QPoint(3, 3), button=Qt.MouseButton.LeftButton, buttons=None):
    QApplication.sendEvent(widget, _mouse(kind, widget, pos, button, buttons))


def test_default_focus_target_is_the_box_and_every_child_is_watched(qtbot):
    box = BoxFrame("t")
    qtbot.addWidget(box)
    assert box.focus_target() is box
    assert box.filters_child(QLabel())


@pytest.mark.parametrize("part", ["label", "title", "off"])
def test_a_click_on_a_non_focusing_child_focuses_the_target(qtbot, part):
    box = _Box()
    _shown(qtbot, box)
    _send(getattr(box, part), QEvent.Type.MouseButtonPress)
    assert box.field.hasFocus()


def test_a_click_on_the_frame_itself_focuses_the_target(qtbot):
    box = _Box()
    _shown(qtbot, box)
    qtbot.mouseClick(box, Qt.MouseButton.LeftButton, pos=QPoint(1, 1))
    assert box.field.hasFocus()


def test_a_focusing_child_keeps_its_own_click(qtbot):
    box = _Box()
    _shown(qtbot, box)
    target = QLineEdit()
    box.layout().addWidget(target)
    box.install_click_filters()
    qtbot.waitUntil(target.isVisible)
    qtbot.mouseClick(target, Qt.MouseButton.LeftButton)
    assert target.hasFocus()


def test_an_unwatched_child_is_left_alone(qtbot):
    box = _Box()
    outside = _shown(qtbot, box)
    assert box.skipped not in box._click_filtered
    assert box.eventFilter(box.skipped, _mouse(QEvent.Type.MouseButtonPress, box.skipped)) is False
    assert outside.hasFocus()


def test_install_click_filters_twice_watches_once(qtbot):
    box = _Box()
    qtbot.addWidget(box)
    before = set(box._click_filtered)
    box.install_click_filters()
    assert box._click_filtered == before


def test_unrelated_events_pass_through(qtbot):
    box = _Box()
    qtbot.addWidget(box)
    assert box.eventFilter(box.label, QEvent(QEvent.Type.Enter)) is False
    assert box.eventFilter(QLabel(), _mouse(QEvent.Type.MouseButtonPress, box)) is False


def test_right_click_emits_menu_requested_at_the_global_point(qtbot):
    box = _Box()
    qtbot.addWidget(box)
    got = []
    box.menu_requested.connect(lambda b, p: got.append((b, p)))
    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(2, 2), QPoint(50, 60))
    QApplication.sendEvent(box.label, event)
    assert got == [(box, QPoint(50, 60))]
    assert event.isAccepted()


def _drag_from(widget, box, distance, buttons=Qt.MouseButton.LeftButton):
    _send(widget, QEvent.Type.MouseButtonPress)
    _send(
        widget,
        QEvent.Type.MouseMove,
        QPoint(3 + distance, 3),
        Qt.MouseButton.NoButton,
        buttons,
    )


@pytest.mark.parametrize("from_frame", [False, True])
def test_dragging_a_draggable_box_past_the_threshold_starts_one_drag(qtbot, from_frame):
    box = _Box(draggable=True)
    _shown(qtbot, box)
    started = []
    box.drag_started.connect(started.append)
    widget = box if from_frame else box.label
    far = QApplication.startDragDistance() + 2
    _drag_from(widget, box, 1)
    assert started == []
    _send(
        widget,
        QEvent.Type.MouseMove,
        QPoint(3 + far, 3),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
    )
    _send(
        widget,
        QEvent.Type.MouseMove,
        QPoint(3 + 2 * far, 3),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
    )
    assert started == [box]


def test_a_draggable_box_shows_a_grab_cursor_on_its_title(qtbot):
    box = _Box(draggable=True)
    qtbot.addWidget(box)
    assert box.title.cursor().shape() == Qt.CursorShape.OpenHandCursor


@pytest.mark.parametrize("from_frame", [False, True])
def test_release_cancels_a_pending_drag(qtbot, from_frame):
    box = _Box(draggable=True)
    _shown(qtbot, box)
    started = []
    box.drag_started.connect(started.append)
    widget = box if from_frame else box.label
    _send(widget, QEvent.Type.MouseButtonPress)
    _send(widget, QEvent.Type.MouseButtonRelease, buttons=Qt.MouseButton.NoButton)
    _drag_from(widget, box, 0)
    _send(widget, QEvent.Type.MouseButtonRelease, buttons=Qt.MouseButton.NoButton)
    far = QApplication.startDragDistance() + 5
    _send(
        widget,
        QEvent.Type.MouseMove,
        QPoint(3 + far, 3),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
    )
    assert started == []


def test_no_drag_without_the_left_button_or_when_not_draggable(qtbot):
    far = QApplication.startDragDistance() + 5
    fixed = _Box(draggable=False)
    _shown(qtbot, fixed)
    started = []
    fixed.drag_started.connect(started.append)
    _drag_from(fixed.label, fixed, far)

    movable = _Box(draggable=True)
    _shown(qtbot, movable)
    movable.drag_started.connect(started.append)
    _send(movable.label, QEvent.Type.MouseButtonPress, button=Qt.MouseButton.RightButton)
    _send(
        movable.label,
        QEvent.Type.MouseMove,
        QPoint(3 + far, 3),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.RightButton,
    )
    _send(movable.label, QEvent.Type.MouseButtonPress)
    _send(
        movable.label,
        QEvent.Type.MouseMove,
        QPoint(3 + far, 3),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
    )
    assert started == []


def test_set_current_tints_and_marks_the_title(qtbot):
    box = _Box()
    qtbot.addWidget(box)
    plain = box.palette().color(QPalette.ColorRole.Window)
    box.set_current(True)
    assert box.autoFillBackground()
    assert box.palette().color(QPalette.ColorRole.Window) != plain
    assert box.title.text().startswith("▶ <b>Box</b>")
    box.set_title("<b>Renamed</b>")
    assert "Renamed" in box.title.text() and "(current)" in box.title.text()
    box.set_current(False)
    assert not box.autoFillBackground()
    assert box.palette().color(QPalette.ColorRole.Window) == plain
    assert box.title.text() == "<b>Renamed</b>"


def _edge_pixel(box):
    image = box.grab().toImage()
    return image.pixelColor(1, image.height() // 2)


def test_a_failed_box_has_a_red_border_and_a_red_cross_in_its_title(qtbot):
    box = _Box()
    _shown(qtbot, box)
    box.resize(200, 150)
    assert _edge_pixel(box).rgb() != ERROR_RED.rgb()
    box.set_failed(True)
    red = _edge_pixel(box)
    assert red.rgb() == ERROR_RED.rgb()
    assert "✗" in box.title.text() and "#d0312d" in box.title.text()
    box.set_current(True)
    assert box.title.text().startswith("▶ <span")
    box.set_failed(True)
    box.set_failed(False)
    assert _edge_pixel(box).rgb() != ERROR_RED.rgb()
    assert "✗" not in box.title.text()
