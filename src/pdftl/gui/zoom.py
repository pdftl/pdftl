# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/zoom.py

"""Text zoom for each part of the window: the focused part zooms."""

from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QWidget

ZOOM_KEY = "zoom/{}"
MIN_POINTS = 5.0
MAX_FACTOR = 4.0


class ZoomArea:
    """A widget whose font, and so its children's, grows and shrinks by whole points.

    The size is read back from the font, so Ctrl+wheel zoom in a text view
    counts too.
    """

    def __init__(self, name: str, label: str, widget: QWidget) -> None:
        self.name, self.label, self.widget = name, label, widget
        font = widget.font()
        points = font.pointSizeF()
        self.base = points if points > 0 else font.pixelSize() * 72 / widget.logicalDpiY()

    def contains(self, widget: QWidget | None) -> bool:
        return widget is not None and (widget is self.widget or self.widget.isAncestorOf(widget))

    def size(self) -> float:
        points = self.widget.font().pointSizeF()
        return points if points > 0 else self.base

    def set_size(self, points: float) -> None:
        font = QFont(self.widget.font())
        font.setPointSizeF(min(max(points, MIN_POINTS), self.base * MAX_FACTOR))
        self.widget.setFont(font)

    def step(self, steps: int) -> None:
        """Grow by `steps` points, shrink when negative; 0 restores the base size."""
        self.set_size(self.size() + steps if steps else self.base)

    def percent(self) -> int:
        return round(100 * self.size() / self.base)


class Zoom:
    """The window's zoom areas; the last one also takes focus anywhere else."""

    def __init__(self, areas: list[ZoomArea]) -> None:
        self.areas = areas

    def area_for(self, focused: QWidget | None) -> ZoomArea:
        return next((a for a in self.areas if a.contains(focused)), self.areas[-1])

    def step(self, focused: QWidget | None, steps: int) -> ZoomArea:
        area = self.area_for(focused)
        area.step(steps)
        return area

    def restore(self, settings: QSettings) -> None:
        for area in self.areas:
            offset = settings.value(ZOOM_KEY.format(area.name), 0.0, type=float)
            if offset:
                area.set_size(area.base + offset)

    def save(self, settings: QSettings) -> None:
        for area in self.areas:
            settings.setValue(ZOOM_KEY.format(area.name), area.size() - area.base)
