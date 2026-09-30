# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/picker.py

"""Coordinate picker dialog.

Same-geometry pages are composited darkest-wins, as Briss does; the user
clicks or nudges a point on the composite and reads it in PDF user space
or displayed space. Pages render one per event-loop turn, so the dialog
stays responsive without a thread; each pdfium call holds the lock the
thumbnail worker uses, since pdfium is not thread-safe.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pikepdf
import pypdfium2 as pdfium
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from pdftl.gui.interfaces import plural
from pdftl.gui.page_geometry import (
    PageGeometry,
    group_by_geometry,
    pts_to_dim_str,
    read_page_geometries,
)
from pdftl.gui.thumbs import PDFIUM_LOCK, open_pdf

MAX_BLEND_PAGES = 50
"""Cap on how many pages of a group are rendered into one composite."""

UNITS = ("pt", "mm", "cm", "in", "%")

_MAX_CANVAS_PX = 1200.0
_MIN_RENDER_SCALE = 2.0


def _composite_scale(displayed_width: float, displayed_height: float) -> float:
    """Pixels per point: 2, or less so no side exceeds ~1200 px."""
    largest = max(displayed_width, displayed_height, 1.0) * _MIN_RENDER_SCALE
    if largest <= _MAX_CANVAS_PX:
        return _MIN_RENDER_SCALE
    return _MAX_CANVAS_PX / max(displayed_width, displayed_height, 1.0)


def format_point_token(
    x_pts: float,
    y_pts: float,
    unit: str,
    total_x: float | None = None,
    total_y: float | None = None,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> str:
    """Formats a point as `"x,y"` in `unit`.

    For `'%'`, `total_x`/`total_y` are the page dimensions and
    `origin_x`/`origin_y` are subtracted first, so the percentage is of
    page size rather than of absolute PDF coordinates.
    """

    def value(v: float, total: float | None, origin: float) -> str:
        return pts_to_dim_str(v - origin, "%", total) if unit == "%" else pts_to_dim_str(v, unit)

    return f"{value(x_pts, total_x, origin_x)},{value(y_pts, total_y, origin_y)}"


@dataclass(frozen=True)
class PickedPoint:
    """A point accepted from `CoordinatePicker`."""

    user: tuple[float, float]
    """Absolute PDF user-space point, in points."""

    displayed: tuple[float, float]
    """Displayed-space point (rotation applied, top-left origin, y down), in points."""

    pages: tuple[int, ...]
    """1-based pages of the geometry group the point was picked against."""

    unit: str
    """Unit selected in the dialog when the point was accepted."""

    token: str
    """`"x,y"` in `unit`, PDF user-space (see `format_point_token`)."""


class _CompositeRenderer(QObject):
    """Blends one geometry group's pages by per-channel minimum, one page per
    event-loop turn so input and repaints run in between."""

    ready = Signal(int, QImage)
    failed = Signal(int, str)

    def __init__(
        self,
        pdf_path: Path,
        pages: list[int],
        password: str | None,
        scale: float,
        group_index: int,
    ) -> None:
        super().__init__()
        self.pdf_path, self.pages, self.password = pdf_path, pages, password
        self.scale, self.group_index = scale, group_index
        self._doc: pdfium.PdfDocument | None = None
        self._accumulator: np.ndarray | None = None
        self._index = 0
        self._cancelled = False

    def start(self) -> None:
        QTimer.singleShot(0, self._step)

    def cancel(self) -> None:
        """Stops before the next page; safe to call at any time, repeatedly."""
        self._cancelled = True
        self._close_document()

    def _close_document(self) -> None:
        if self._doc is not None:
            with PDFIUM_LOCK:
                self._doc.close()
            self._doc = None

    def _open_document(self) -> bool:
        try:
            with PDFIUM_LOCK:
                self._doc = open_pdf(self.pdf_path, self.password)
        except (pdfium.PdfiumError, OSError) as exc:
            self.failed.emit(self.group_index, str(exc))
            return False
        return True

    def _render_next_page(self) -> bool:
        page_number = self.pages[self._index]
        self._index += 1
        try:
            with PDFIUM_LOCK:
                frame = self._render_frame(page_number)
        except (pdfium.PdfiumError, OSError) as exc:
            self.failed.emit(self.group_index, str(exc))
            return False
        self._accumulator = (
            frame if self._accumulator is None else np.minimum(self._accumulator, frame)
        )
        return True

    def _render_frame(self, page_number: int) -> np.ndarray:
        page = self._doc[page_number - 1]
        try:
            bitmap = page.render(
                scale=self.scale, rev_byteorder=True, fill_color=(255, 255, 255, 255)
            )
            try:
                # A view of the bitmap's buffer, which close() frees.
                return bitmap.to_numpy().copy()
            finally:
                bitmap.close()
        finally:
            page.close()

    def _step(self) -> None:
        if self._cancelled:
            return
        if self._doc is None and not self._open_document():
            return
        if self._index >= len(self.pages):
            self._finish()
            return
        if self._render_next_page():
            QTimer.singleShot(0, self._step)

    def _finish(self) -> None:
        self._close_document()
        if self._cancelled or self._accumulator is None:
            return
        height, width, _channels = self._accumulator.shape
        data = np.ascontiguousarray(self._accumulator).tobytes()
        image = QImage(data, width, height, width * 3, QImage.Format.Format_RGB888).copy()
        self.ready.emit(self.group_index, image)


class _PickerCanvas(QWidget):
    """Displays a composite image and a keyboard/mouse-movable crosshair.

    Coordinates are tracked in displayed space (points); pixel conversion
    uses the render scale passed to `set_scale`.
    """

    point_changed = Signal(float, float)

    def __init__(self) -> None:
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self._pixmap = QPixmap()
        self._scale = 1.0
        self._geometry: PageGeometry | None = None
        self._point: tuple[float, float] | None = None
        self.setMinimumSize(100, 100)

    def set_geometry(self, geometry: PageGeometry) -> None:
        """Starts a new group: clears the image and centers the crosshair."""
        self._geometry = geometry
        self._pixmap = QPixmap()
        self._point = (geometry.displayed_width / 2, geometry.displayed_height / 2)
        self.update()

    def set_scale(self, scale: float) -> None:
        self._scale = scale
        if self._geometry is not None:
            width = max(1, round(self._geometry.displayed_width * scale))
            height = max(1, round(self._geometry.displayed_height * scale))
            self.setFixedSize(width, height)
        self.update()

    def set_image(self, image: QImage) -> None:
        self._pixmap = QPixmap.fromImage(image)
        self.update()

    def has_image(self) -> bool:
        return not self._pixmap.isNull()

    def point(self) -> tuple[float, float] | None:
        return self._point

    def set_point(self, dx: float, dy: float) -> None:
        if self._geometry is None:
            return
        dx = max(0.0, min(dx, self._geometry.displayed_width))
        dy = max(0.0, min(dy, self._geometry.displayed_height))
        self._point = (dx, dy)
        self.update()
        self.point_changed.emit(dx, dy)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            if self._pixmap.isNull():
                painter.fillRect(self.rect(), QColor(220, 220, 220))
            else:
                painter.drawPixmap(0, 0, self._pixmap)
            if self._point is not None and self._geometry is not None:
                px, py = self._geometry.displayed_to_pixels(*self._point, self._scale)
                painter.setPen(QPen(QColor(255, 0, 0), 1))
                painter.drawLine(int(px) - 8, int(py), int(px) + 8, int(py))
                painter.drawLine(int(px), int(py) - 8, int(px), int(py) + 8)
        finally:
            painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self._set_from_mouse(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._set_from_mouse(event)

    def _set_from_mouse(self, event: QMouseEvent) -> None:
        if self._geometry is None:
            return
        pos = event.position()
        dx, dy = self._geometry.pixels_to_displayed(pos.x(), pos.y(), self._scale)
        self.set_point(dx, dy)

    _NUDGE = {
        Qt.Key.Key_Left: (-1.0, 0.0),
        Qt.Key.Key_Right: (1.0, 0.0),
        Qt.Key.Key_Up: (0.0, -1.0),
        Qt.Key.Key_Down: (0.0, 1.0),
    }

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._geometry is None:
            super().keyPressEvent(event)
            return
        key = Qt.Key(event.key())
        delta = self._NUDGE.get(key)
        if delta is not None:
            step = self._nudge_step(event.modifiers())
            dx0, dy0 = self._point or (0.0, 0.0)
            self.set_point(dx0 + delta[0] * step, dy0 + delta[1] * step)
            event.accept()
            return
        if key == Qt.Key.Key_Home:
            self.set_point(0.0, 0.0)
            event.accept()
            return
        if key == Qt.Key.Key_End:
            self.set_point(self._geometry.displayed_width, self._geometry.displayed_height)
            event.accept()
            return
        super().keyPressEvent(event)

    @staticmethod
    def _nudge_step(modifiers: Qt.KeyboardModifier) -> float:
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            return 10.0
        if modifiers & Qt.KeyboardModifier.AltModifier:
            return 0.1
        return 1.0


class CoordinatePicker(QDialog):
    """Modal dialog: pick one point on a Briss-style composite of same-geometry pages.

    Displayed-space readout uses a top-left origin with y increasing
    downward -- the same convention pdfium renders in and the mouse
    reports in -- alongside absolute PDF user-space (y up from the page's
    own origin), since that is what CLI arguments consume.
    """

    def __init__(
        self,
        pdf_path: str | Path,
        pages: Sequence[int],
        passwords: Callable[[Path], str | None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pick a point")
        self.pdf_path = Path(pdf_path)
        self.pages = list(pages)
        self._passwords = passwords
        self._password: str | None = None
        self.picked_point: PickedPoint | None = None
        self.error: str | None = None
        self.geometries: dict[int, PageGeometry] = {}
        self.groups: list[list[int]] = []
        self.group_index = 0
        self._renderer: _CompositeRenderer | None = None

        self._load_geometries()
        if not self.error:
            self.groups = group_by_geometry(self.geometries)

        self.status = QLabel("")
        self.unit_combo = QComboBox()
        self.unit_combo.addItems(UNITS)
        self.unit_combo.setToolTip("Unit for the readout and accepted point")
        self.unit_combo.currentTextChanged.connect(lambda _text: self._update_readout())
        self.canvas = _PickerCanvas()
        self.canvas.point_changed.connect(lambda _x, _y: self._update_readout())
        self.readout = QLabel("")
        self.readout.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        scroll = QScrollArea()
        scroll.setWidget(self.canvas)
        scroll.setWidgetResizable(False)

        unit_row = QHBoxLayout()
        unit_row.addWidget(QLabel("Unit:"))
        unit_row.addWidget(self.unit_combo)
        unit_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self.status)
        layout.addLayout(unit_row)
        layout.addWidget(scroll, 1)
        layout.addWidget(self.readout)
        layout.addWidget(
            QLabel(
                "Arrows: move 1pt (Shift: 10pt, Alt: 0.1pt). Click or drag: set point. "
                "Home/End: page corners. PageUp/PageDown: other group. "
                "Enter: accept. Esc: cancel."
            )
        )
        self.resize(900, 700)

        if self.error:
            self.status.setText(f"Error: {self.error}")
        else:
            self._load_group(0)
            self.canvas.setFocus(Qt.FocusReason.OtherFocusReason)

    def _load_geometries(self) -> None:
        password = self._passwords(self.pdf_path) if self._passwords is not None else None
        try:
            self.geometries = read_page_geometries(self.pdf_path, self.pages, password)
            self._password = password
        except pikepdf.PasswordError as exc:
            self.error = str(exc) or "Password required"

    def _group_geometry(self) -> PageGeometry | None:
        if self.error or not self.groups:
            return None
        return self.geometries[self.groups[self.group_index][0]]

    def _load_group(self, index: int) -> None:
        self._stop_renderer()
        self.group_index = index
        geometry = self._group_geometry()
        if geometry is None:
            return
        self.canvas.set_geometry(geometry)
        scale = _composite_scale(geometry.displayed_width, geometry.displayed_height)
        self.canvas.set_scale(scale)
        self._update_status()
        self._update_readout()
        pages = self.groups[index][:MAX_BLEND_PAGES]
        self._renderer = _CompositeRenderer(self.pdf_path, pages, self._password, scale, index)
        self._renderer.ready.connect(self._on_composite_ready)
        self._renderer.failed.connect(self._on_composite_failed)
        self._renderer.start()

    def _stop_renderer(self) -> None:
        if self._renderer is not None:
            self._renderer.ready.disconnect(self._on_composite_ready)
            self._renderer.failed.disconnect(self._on_composite_failed)
            self._renderer.cancel()
            self._renderer = None

    def _on_composite_ready(self, group_index: int, image: QImage) -> None:
        if group_index != self.group_index:
            return
        self.canvas.set_image(image)
        self._update_status()

    def _on_composite_failed(self, group_index: int, message: str) -> None:
        if group_index != self.group_index:
            return
        self.status.setText(f"Render failed: {message}")

    def _update_status(self) -> None:
        if self.error or not self.groups:
            return
        geometry = self._group_geometry()
        pages = self.groups[self.group_index]
        rendering = "" if self.canvas.has_image() else " (rendering...)"
        self.status.setText(
            f"Group {self.group_index + 1} of {len(self.groups)} "
            f"({plural(len(pages), 'page')}, "
            f"{geometry.displayed_width:.1f}×{geometry.displayed_height:.1f} pt)"
            f"{rendering}"
        )

    def _update_readout(self) -> None:
        point = self.canvas.point()
        geometry = self._group_geometry()
        if point is None or geometry is None:
            self.readout.setText("")
            return
        dx, dy = point
        ux, uy = geometry.displayed_to_user(dx, dy)
        unit = self.unit_combo.currentText()
        displayed_token = format_point_token(
            dx, dy, unit, geometry.displayed_width, geometry.displayed_height
        )
        user_token = format_point_token(
            ux, uy, unit, geometry.width, geometry.height, geometry.origin_x, geometry.origin_y
        )
        self.readout.setText(f"Displayed: {displayed_token}   PDF: {user_token}")

    def _step_group(self, delta: int) -> None:
        if self.error or not self.groups:
            return
        self._load_group((self.group_index + delta) % len(self.groups))
        self.canvas.setFocus(Qt.FocusReason.OtherFocusReason)

    def _build_picked_point(self) -> PickedPoint | None:
        point = self.canvas.point()
        geometry = self._group_geometry()
        if point is None or geometry is None:
            return None
        dx, dy = point
        ux, uy = geometry.displayed_to_user(dx, dy)
        unit = self.unit_combo.currentText()
        token = format_point_token(
            ux, uy, unit, geometry.width, geometry.height, geometry.origin_x, geometry.origin_y
        )
        pages = tuple(self.groups[self.group_index])
        return PickedPoint(user=(ux, uy), displayed=(dx, dy), pages=pages, unit=unit, token=token)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = Qt.Key(event.key())
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            picked = self._build_picked_point()
            if picked is not None:
                self.picked_point = picked
                self.accept()
            event.accept()
            return
        if key == Qt.Key.Key_PageDown:
            self._step_group(1)
            event.accept()
            return
        if key == Qt.Key.Key_PageUp:
            self._step_group(-1)
            event.accept()
            return
        super().keyPressEvent(event)

    def done(self, result: int) -> None:
        self._stop_renderer()
        super().done(result)


def pick_point(
    parent,
    pdf_path: str | Path,
    pages: Sequence[int],
    passwords: Callable[[Path], str | None] | None = None,
) -> PickedPoint | None:
    """Opens `CoordinatePicker` modally; `None` if the user cancels."""
    dialog = CoordinatePicker(pdf_path, pages, passwords=passwords, parent=parent)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    return dialog.picked_point if accepted else None
