# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/gui/test_stage_list.py

"""Tests for stage_list: double-click a gap to insert, drag a stage to move it."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QFrame, QScrollArea, QVBoxLayout, QWidget

from pdftl.gui.stage_list import DRAG_PIXMAP_WIDTH, MARKER_HEIGHT, MIME, StageListGestures

BOX_HEIGHT = 60


class _List:
    """Three fixed-height boxes in a scroll area, as the main window lays out stages."""

    def __init__(self, qtbot, width=200):
        self.widget = QWidget()
        layout = QVBoxLayout(self.widget)
        layout.setSpacing(10)
        self.boxes = []
        for _ in range(3):
            box = QFrame()
            box.setFixedSize(width, BOX_HEIGHT)
            layout.addWidget(box)
            self.boxes.append(box)
        layout.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.widget)
        self.scroll.resize(width + 40, 600)
        qtbot.addWidget(self.scroll)
        self.inserted, self.moved = [], []
        self.gestures = StageListGestures(
            self.widget,
            self.scroll,
            lambda: self.boxes,
            self.inserted.append,
            lambda box, at: self.moved.append((box, at)),
        )
        self.scroll.show()
        qtbot.waitExposed(self.scroll)

    def top(self, i):
        return self.boxes[i].geometry().top()

    def bottom(self, i):
        return self.boxes[i].geometry().bottom()


@pytest.fixture
def stages(qtbot):
    return _List(qtbot)


def _mime(fmt=MIME):
    mime = QMimeData()
    mime.setData(fmt, b"")
    return mime


def _drag_event(cls, y, mime):
    return cls(
        QPoint(20, y),
        Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _drop_event(y, mime):
    return QDropEvent(
        QPointF(20, y),
        Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _double_click(widget, y):
    event = QMouseEvent(
        QEvent.Type.MouseButtonDblClick,
        QPointF(5, y),
        QPointF(widget.mapToGlobal(QPoint(5, y))),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    return QApplication.sendEvent(widget, event)


def test_gap_index_counts_the_boxes_above(stages):
    assert stages.gestures.gap_index(0) == 0
    assert stages.gestures.gap_index(stages.bottom(0) + 5) == 1
    assert stages.gestures.gap_index(stages.bottom(2) + 5) == 3


def test_double_click_in_a_gap_inserts_there(stages):
    gap = (stages.bottom(0) + stages.top(1)) // 2
    _double_click(stages.widget, gap)
    assert stages.inserted == [1]


def test_double_click_on_a_box_is_not_a_gap(stages):
    handled = QApplication.sendEvent(
        stages.widget,
        QMouseEvent(
            QEvent.Type.MouseButtonDblClick,
            QPointF(20, stages.top(1) + 5),
            QPointF(0, 0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )
    assert stages.inserted == []
    assert handled is False


def test_drop_index_ignores_the_dragged_box(stages):
    stages.gestures.dragging = stages.boxes[0]
    below_all = stages.bottom(2) + 5
    assert stages.gestures.drop_index(0) == 0
    assert stages.gestures.drop_index(below_all) == 2
    # just below box 1's middle: after it, i.e. between boxes 1 and 2
    assert stages.gestures.drop_index(stages.top(1) + BOX_HEIGHT // 2 + 2) == 1


def _drive(stages, y, events):
    """Run a drag of box 0 whose 'exec' delivers `events` to the list widget."""
    seen = {}

    def fake_exec(drag):
        seen["pixmap"] = drag.pixmap()
        seen["mime"] = drag.mimeData()
        for make in events:
            event = make(seen["mime"])
            QApplication.sendEvent(stages.widget, event)
            seen.setdefault("accepted", []).append(event.isAccepted())
            seen.setdefault("marker", []).append(stages.gestures.marker.isVisible())
            seen.setdefault("marker_y", []).append(stages.gestures.marker.geometry().top())

    stages.gestures.run_drag = fake_exec
    stages.gestures.start_drag(stages.boxes[0])
    return seen


def test_dragging_box_0_below_the_last_moves_it_to_the_end(stages):
    y = stages.bottom(2) + 5
    seen = _drive(
        stages,
        y,
        [
            lambda m: _drag_event(QDragEnterEvent, y, m),
            lambda m: _drag_event(QDragMoveEvent, y, m),
            lambda m: _drop_event(y, m),
        ],
    )
    assert stages.moved == [(stages.boxes[0], 2)]
    assert seen["accepted"] == [True, True, True]
    assert seen["marker"] == [True, True, False]
    # the marker sits in the gap just below the last box, clear of it
    assert seen["marker_y"][1] > stages.bottom(2)
    assert seen["mime"].hasFormat(MIME)
    assert not seen["mime"].hasText()
    assert stages.gestures.dragging is None
    assert not stages.gestures.marker.isVisible()


def test_the_marker_sits_between_the_two_boxes_it_would_land_between(stages):
    y = stages.top(2) - 2
    seen = _drive(
        stages,
        y,
        [
            lambda m: _drag_event(QDragEnterEvent, y, m),
            lambda m: _drag_event(QDragMoveEvent, y, m),
        ],
    )
    top = seen["marker_y"][1]
    assert stages.bottom(1) < top
    assert top + MARKER_HEIGHT <= stages.top(2)


def test_a_drag_pixmap_is_at_most_the_cap_wide(qtbot):
    wide = _List(qtbot, width=DRAG_PIXMAP_WIDTH + 200)
    seen = _drive(wide, 0, [])
    assert seen["pixmap"].width() == DRAG_PIXMAP_WIDTH
    narrow = _List(qtbot, width=100)
    assert _drive(narrow, 0, [])["pixmap"].width() == 100


def test_leaving_hides_the_marker(stages):
    y = stages.top(1)
    seen = _drive(
        stages,
        y,
        [lambda m: _drag_event(QDragEnterEvent, y, m), lambda _m: QDragLeaveEvent()],
    )
    assert seen["marker"] == [True, False]
    assert stages.moved == []


def test_a_foreign_drag_is_ignored(stages):
    """Filtered directly: Qt itself drops a move or drop with no accepted enter."""
    y = stages.top(1)
    other = _mime("text/plain")
    stages.gestures.dragging = stages.boxes[0]
    for event in (
        _drag_event(QDragEnterEvent, y, other),
        _drag_event(QDragMoveEvent, y, other),
        _drop_event(y, other),
    ):
        assert stages.gestures.eventFilter(stages.widget, event) is False
        assert not event.isAccepted()
    assert stages.moved == []
    assert not stages.gestures.marker.isVisible()


def test_a_stage_mime_drop_with_no_drag_in_progress_is_ignored(stages):
    event = _drop_event(stages.top(1), _mime())
    assert stages.gestures.eventFilter(stages.widget, event) is False
    assert not event.isAccepted()
    assert stages.moved == []


def test_with_only_the_dragged_box_there_is_no_marker(qtbot):
    one = _List(qtbot)
    one.boxes = one.boxes[:1]
    seen = _drive(one, 5, [lambda m: _drag_event(QDragEnterEvent, 5, m)])
    assert seen["marker"] == [False]


def test_the_default_drag_runner_is_qdrag_exec(monkeypatch):
    from PySide6.QtGui import QDrag

    from pdftl.gui import stage_list

    calls = []
    monkeypatch.setattr(QDrag, "exec", lambda self, action: calls.append(action))
    stage_list._run_drag(QDrag(QWidget()))
    assert calls == [Qt.DropAction.MoveAction]


def test_other_events_pass_through(stages):
    assert stages.gestures.eventFilter(stages.widget, QEvent(QEvent.Type.Enter)) is False
