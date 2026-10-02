# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/preview_dialog.py

"""The large page preview: one page at a time, at one zoom for every page."""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QImage, QKeySequence, QMouseEvent, QPixmap, QShortcut, QWheelEvent
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout

from pdftl.gui import keymap
from pdftl.gui.thumbs import PdfiumThumbProvider

PREVIEW = 900
ZOOM_STEP = 1.25
MIN_ZOOM, MAX_ZOOM = 0.1, 4.0
MAX_RENDER_PX = 40_000_000
"""Pixels in one render; caps the zoom of large pages."""
FIT_MARGIN = 4
PAN_BUTTONS = (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton)


def _keys(action_id: str) -> str:
    native = QKeySequence.SequenceFormat.NativeText
    return "/".join(QKeySequence(k).toString(native) for k in keymap.keys_for(action_id))


def hint_text() -> str:
    ctrl = QKeySequence("Ctrl+A").toString(QKeySequence.SequenceFormat.NativeText)[:-1]
    return (
        f"{_keys('preview_prev_page')}, {_keys('preview_next_page')}: other pages. "
        f"{_keys('zoom_in')}, {_keys('zoom_out')} or {ctrl}wheel: zoom; "
        f"{_keys('zoom_reset')}: fit. Drag: pan. {_keys('close_preview')}: close."
    )


class PreviewDialog(QDialog):
    """Pages share one zoom, where 100% is the page's printed size; opening fits
    the first page to the window."""

    def __init__(
        self,
        provider: PdfiumThumbProvider,
        key: str,
        path: Path,
        page: int,
        total: int,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.provider, self.key, self.path, self.total = provider, f"{key}#preview", path, total
        self.sizes: dict[int, tuple[float, float]] | None = None
        self.zoom: float | None = None
        self._want: tuple[int, int] | None = None
        self._drag: QPointF | None = None
        self.image = QLabel("Rendering...")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.image)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        self.scroll.viewport().installEventFilter(self)
        self.zoom_label = QLabel()
        hint = QLabel(hint_text())
        hint.setWordWrap(True)
        bottom = QHBoxLayout()
        bottom.addWidget(hint, 1)
        bottom.addWidget(self.zoom_label)
        layout = QVBoxLayout(self)
        layout.addWidget(self.scroll)
        layout.addLayout(bottom)
        self.resize(PREVIEW + 60, int(PREVIEW * 1.2))
        self._bind_keys()
        provider.sizes_ready.connect(self._sizes_ready)
        provider.thumb_ready.connect(self._thumb_ready)
        provider.request_sizes(self.key, path, total)
        self.show_page(page)

    def _bind_keys(self) -> None:
        # Shortcuts, not keyPressEvent: the focused scroll area scrolls on arrows.
        slots = {
            "preview_prev_page": lambda: self.show_page(self.page - 1),
            "preview_next_page": lambda: self.show_page(self.page + 1),
            "zoom_in": lambda: self.zoom_by(ZOOM_STEP),
            "zoom_out": lambda: self.zoom_by(1 / ZOOM_STEP),
            "zoom_reset": self.fit,
        }
        for action_id, slot in slots.items():
            for key in keymap.keys_for(action_id):
                shortcut = QShortcut(QKeySequence(key), self)
                shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                shortcut.activated.connect(slot)

    # Pages and zoom

    def show_page(self, page: int) -> None:
        self.page = max(1, min(page, self.total))
        self.setWindowTitle(f"Page {self.page} of {self.total}")
        if self.zoom is not None:
            self._render(stretch=False)

    def _page_size(self) -> tuple[float, float] | None:
        return (self.sizes or {}).get(self.page)

    def _pt_to_px(self) -> float:
        return self.logicalDpiX() / 72

    def _clamp(self, zoom: float) -> float:
        size = self._page_size()
        top = MAX_ZOOM
        if size is not None:
            ratio = self.devicePixelRatioF() * self._pt_to_px()
            top = min(top, math.sqrt(MAX_RENDER_PX / (size[0] * size[1])) / ratio)
        return max(MIN_ZOOM, min(zoom, top))

    def _fit_zoom(self) -> float:
        size = self._page_size()
        if size is None:
            return 1.0 if self.zoom is None else self.zoom
        room = self.scroll.viewport().size() - QSize(FIT_MARGIN, FIT_MARGIN)
        base = self._pt_to_px()
        return self._clamp(min(room.width() / (size[0] * base), room.height() / (size[1] * base)))

    def _sizes_ready(self, key: str, sizes: dict[int, tuple[float, float]]) -> None:
        if key != self.key:
            return
        self.sizes = sizes
        self.zoom = self._fit_zoom()
        self._render(stretch=False)

    def zoom_by(self, factor: float, anchor: QPoint | None = None) -> None:
        if self.zoom is not None:
            self._set_zoom(self.zoom * factor, anchor)

    def fit(self) -> None:
        if self.zoom is not None:
            self._set_zoom(self._fit_zoom(), None)

    def _set_zoom(self, zoom: float, anchor: QPoint | None) -> None:
        """Keep the page point under `anchor` (default: the middle) where it is."""
        viewport = self.scroll.viewport()
        anchor = viewport.rect().center() if anchor is None else anchor
        old = self.image.geometry()
        fx = (anchor.x() - old.x()) / max(1, old.width())
        fy = (anchor.y() - old.y()) / max(1, old.height())
        self.zoom = self._clamp(zoom)
        self._render(stretch=True)
        new = self.image.size()
        self.scroll.horizontalScrollBar().setValue(round(fx * new.width() - anchor.x()))
        self.scroll.verticalScrollBar().setValue(round(fy * new.height() - anchor.y()))

    def _render(self, stretch: bool) -> None:
        """Ask for the page at the current zoom; meanwhile stretch the old image, or
        say it is rendering."""
        size = self._page_size()
        if size is None:
            self._want = None
            self.image.setPixmap(QPixmap())
            self.image.setText("Could not read this page")
            self.image.adjustSize()
            self.zoom_label.setText("")
            return
        scale = self.zoom * self._pt_to_px()
        logical = QSize(round(size[0] * scale), round(size[1] * scale))
        dpr = self.devicePixelRatioF()
        width_px = round(logical.width() * dpr)
        self._want = (self.page, width_px)
        old = self.image.pixmap()
        if stretch and not old.isNull():
            stretched = old.scaled(width_px, round(logical.height() * dpr))
            stretched.setDevicePixelRatio(dpr)
            self.image.setPixmap(stretched)
        else:
            self.image.setPixmap(QPixmap())
            self.image.setText("Rendering...")
        self.image.resize(logical)
        self.zoom_label.setText(f"{round(self.zoom * 100)}%")
        self.provider.request(self.key, self.path, self.page, width_px)

    def _thumb_ready(self, key: str, page: int, image: QImage) -> None:
        if key != self.key or image.isNull() or self._want is None:
            return
        want_page, want_width = self._want
        if page == want_page and abs(image.width() - want_width) <= 1:
            pixmap = QPixmap.fromImage(image)
            pixmap.setDevicePixelRatio(self.devicePixelRatioF())
            self.image.setPixmap(pixmap)

    # Mouse: Ctrl+wheel zooms, dragging pans

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        handlers = {
            QEvent.Type.Wheel: self._wheel,
            QEvent.Type.MouseButtonPress: self._press,
            QEvent.Type.MouseMove: self._move,
            QEvent.Type.MouseButtonRelease: self._release,
        }
        handler = handlers.get(event.type())
        if handler is not None and handler(event):
            return True
        return super().eventFilter(watched, event)

    def _wheel(self, event: QWheelEvent) -> bool:
        dy = event.angleDelta().y()
        if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier) or not dy:
            return False
        self.zoom_by(ZOOM_STEP if dy > 0 else 1 / ZOOM_STEP, event.position().toPoint())
        return True

    def _press(self, event: QMouseEvent) -> bool:
        if event.button() not in PAN_BUTTONS:
            return False
        self._drag = event.globalPosition()
        self.scroll.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        return True

    def _move(self, event: QMouseEvent) -> bool:
        if self._drag is None:
            return False
        delta = event.globalPosition() - self._drag
        self._drag = event.globalPosition()
        for bar, step in (
            (self.scroll.horizontalScrollBar(), delta.x()),
            (self.scroll.verticalScrollBar(), delta.y()),
        ):
            bar.setValue(bar.value() - round(step))
        return True

    def _release(self, event: QMouseEvent) -> bool:
        if self._drag is None or event.button() not in PAN_BUTTONS:
            return False
        self._drag = None
        self.scroll.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        return True
