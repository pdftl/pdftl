# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/stage_list.py

"""Mouse gestures on the list of stages: double-click a gap, drag a stage."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QMimeData, QObject, Qt
from PySide6.QtGui import QDrag, QPalette
from PySide6.QtWidgets import QFrame, QScrollArea, QWidget

MIME = "application/x-pdftl-stage"
MARKER_HEIGHT = 3
DRAG_PIXMAP_WIDTH = 320
AUTOSCROLL_MARGIN = 40


def _run_drag(drag: QDrag) -> object:
    return drag.exec(Qt.DropAction.MoveAction)


class StageListGestures(QObject):
    """Event filter on the widget holding the stage boxes.

    A double-click on empty space calls `insert(index)` for the gap there.
    `start_drag(box)` drags a stage; dropping it calls `move(box, index)`,
    `index` being its new place among the stages. A line marks the drop
    point while dragging.
    """

    run_drag: Callable[[QDrag], object] = staticmethod(_run_drag)

    def __init__(
        self,
        widget: QWidget,
        scroll: QScrollArea,
        boxes: Callable[[], list[QWidget]],
        insert: Callable[[int], None],
        move: Callable[[QWidget, int], None],
    ) -> None:
        super().__init__(widget)
        self.widget, self.scroll, self.boxes = widget, scroll, boxes
        self.insert, self.move = insert, move
        self.dragging: QWidget | None = None
        self.marker = QFrame(widget)
        self.marker.setAutoFillBackground(True)
        palette = self.marker.palette()
        palette.setColor(QPalette.ColorRole.Window, palette.color(QPalette.ColorRole.Highlight))
        self.marker.setPalette(palette)
        self.marker.hide()
        widget.setAcceptDrops(True)
        widget.installEventFilter(self)

    def gap_index(self, y: int) -> int:
        """Where a stage inserted at height `y` goes."""
        return sum(box.geometry().top() < y for box in self.boxes())

    def drop_index(self, y: int) -> int:
        """The dragged stage's new index if dropped at height `y`."""
        others = [box for box in self.boxes() if box is not self.dragging]
        return sum(box.geometry().center().y() < y for box in others)

    def start_drag(self, box: QWidget) -> None:
        mime = QMimeData()
        mime.setData(MIME, b"")
        drag = QDrag(box)
        drag.setMimeData(mime)
        pixmap = box.grab()
        if pixmap.width() > DRAG_PIXMAP_WIDTH:
            pixmap = pixmap.scaledToWidth(
                DRAG_PIXMAP_WIDTH, Qt.TransformationMode.SmoothTransformation
            )
        drag.setPixmap(pixmap)
        self.dragging = box
        try:
            self.run_drag(drag)
        finally:
            self.dragging = None
            self.marker.hide()

    def _accepts(self, event) -> bool:
        return self.dragging is not None and event.mimeData().hasFormat(MIME)

    def _show_marker(self, y: int) -> None:
        others = [box for box in self.boxes() if box is not self.dragging]
        if not others:
            return
        index = self.drop_index(y)
        spacing = max(0, self.widget.layout().spacing())
        if index < len(others):
            line = others[index].geometry().top() - (spacing + MARKER_HEIGHT) // 2
        else:
            line = others[-1].geometry().bottom() + (spacing - MARKER_HEIGHT) // 2 + 1
        left = others[0].geometry().left()
        self.marker.setGeometry(left, line, others[0].width(), MARKER_HEIGHT)
        self.marker.raise_()
        self.marker.show()

    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        if kind == QEvent.Type.MouseButtonDblClick:
            return self._double_click(watched, event)
        if kind in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            return self._drag_over(event)
        if kind == QEvent.Type.DragLeave:
            self.marker.hide()
        elif kind == QEvent.Type.Drop:
            return self._drop(event)
        return False

    def _double_click(self, watched, event) -> bool:
        pos = event.position().toPoint()
        if watched.childAt(pos) not in (None, self.marker):
            return False
        self.insert(self.gap_index(pos.y()))
        return True

    def _drag_over(self, event) -> bool:
        if not self._accepts(event):
            return False
        event.acceptProposedAction()
        pos = event.position().toPoint()
        self.scroll.ensureVisible(pos.x(), pos.y(), 0, AUTOSCROLL_MARGIN)
        self._show_marker(pos.y())
        return True

    def _drop(self, event) -> bool:
        self.marker.hide()
        if not self._accepts(event):
            return False
        event.acceptProposedAction()
        self.move(self.dragging, self.drop_index(event.position().toPoint().y()))
        return True
