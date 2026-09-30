# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/box_frame.py

"""The titled box every pipeline part (inputs, stages, output) is drawn in."""

from __future__ import annotations

import shiboken6
from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QMouseEvent, QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QWidget

from pdftl.gui.icons import ERROR_RED
from pdftl.gui.menu_style import blend

FAILED_BORDER = 3
FAILED_STATUS_STYLE = f"color: {ERROR_RED.name()}; font-weight: bold;"


class BoxFrame(QFrame):
    """A titled box that a click anywhere selects.

    A click on a child that takes focus itself (a field, an enabled
    button) goes to that child; any other click, including one on a label
    or a disabled button, focuses `focus_target()`. Event filters see a
    disabled widget's clicks before it drops them.

    A right click anywhere without its own menu emits `menu_requested`; a
    left drag from such a spot of a `draggable` box emits `drag_started`.

    A failed box gets a red border and a red cross in its title.
    """

    menu_requested = Signal(object, QPoint)
    drag_started = Signal(object)

    def __init__(self, title: str, draggable: bool = False) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.title = QLabel(title)
        self.title_text = title
        self.current = False
        self.failed = False
        self.base_palette = QPalette(self.palette())
        self.draggable = draggable
        if draggable:
            self.title.setCursor(Qt.CursorShape.OpenHandCursor)
        self._click_filtered: set[QWidget] = set()
        self._press: QPoint | None = None

    def focus_target(self) -> QWidget:
        return self

    def filters_child(self, _child: QWidget) -> bool:
        """Whether `install_click_filters` should watch `_child`."""
        return True

    def install_click_filters(self) -> None:
        for child in self.findChildren(QWidget):
            if child not in self._click_filtered and self.filters_child(child):
                child.installEventFilter(self)
                self._click_filtered.add(child)

    def set_title(self, text: str) -> None:
        self.title_text = text
        self._show_title()

    def set_current(self, current: bool) -> None:
        """Tint the box and mark its title while it is the window's current part."""
        self.current = current
        palette = QPalette(self.base_palette)
        if current:
            tint = blend(
                palette.color(QPalette.ColorRole.Highlight), palette.window().color(), 0.2
            )
            palette.setColor(QPalette.ColorRole.Window, tint)
        self.setPalette(palette)
        self.setAutoFillBackground(current)
        self._show_title()

    def set_failed(self, failed: bool) -> None:
        if failed != self.failed:
            self.failed = failed
            self._show_title()
            self.update()

    def _show_title(self) -> None:
        marked = self.title_text
        if self.failed:
            marked = f"<span style='color:{ERROR_RED.name()}'><b>✗</b></span> {marked}"
        if self.current:
            marked = f"▶ {marked} <i>(current)</i>"
        self.title.setText(marked)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self.failed:
            return
        painter = QPainter(self)
        pen = QPen(ERROR_RED, FAILED_BORDER)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        inset = FAILED_BORDER / 2
        painter.drawRect(QRectF(self.rect()).adjusted(inset, inset, -inset, -inset))
        painter.end()

    def eventFilter(self, watched: QObject, event) -> bool:
        if not shiboken6.isValid(self) or watched not in self._click_filtered:
            return False
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            self._pressed(watched, event)
        elif kind == QEvent.Type.MouseMove:
            self._moved(event)
        elif kind == QEvent.Type.MouseButtonRelease:
            self._press = None
        return False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._pressed(self, event)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._moved(event)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._press = None
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.menu_requested.emit(self, event.globalPos())
        event.accept()

    def _pressed(self, watched: QWidget, event: QMouseEvent) -> None:
        click_focus = int(Qt.FocusPolicy.ClickFocus)
        if watched.isEnabled() and int(watched.focusPolicy()) & click_focus:
            return
        self.focus_target().setFocus(Qt.FocusReason.MouseFocusReason)
        if self.draggable and event.button() == Qt.MouseButton.LeftButton:
            self._press = event.globalPosition().toPoint()

    def _moved(self, event: QMouseEvent) -> None:
        if self._press is None or not event.buttons() & Qt.MouseButton.LeftButton:
            return
        moved = event.globalPosition().toPoint() - self._press
        if moved.manhattanLength() >= QApplication.startDragDistance():
            self._press = None
            self.drag_started.emit(self)
