# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/menu_style.py

"""Menus with shortcut text drawn in a muted colour."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QProxyStyle, QStyle, QStyleOptionMenuItem

SHORTCUT_MARGIN = 16


def blend(a: QColor, b: QColor, weight: float) -> QColor:
    """`weight` of `a` mixed with the rest of `b`."""
    mix = [weight * x + (1 - weight) * y for x, y in zip(a.getRgbF()[:3], b.getRgbF()[:3])]
    return QColor.fromRgbF(*mix)


class DimShortcutStyle(QProxyStyle):
    """Wraps the application style; only menu items with a shortcut draw differently."""

    def drawControl(self, element, option, painter, widget=None) -> None:
        if element != QStyle.ControlElement.CE_MenuItem or "\t" not in option.text:
            super().drawControl(element, option, painter, widget)
            return
        label, shortcut = option.text.split("\t", 1)
        plain = QStyleOptionMenuItem(option)
        plain.text = label
        super().drawControl(element, plain, painter, widget)
        palette = option.palette
        if option.state & QStyle.StateFlag.State_Selected:
            ink, ground = QPalette.ColorRole.HighlightedText, QPalette.ColorRole.Highlight
            colour = blend(palette.color(ink), palette.color(ground), 0.75)
        else:
            ink, ground = QPalette.ColorRole.Text, QPalette.ColorRole.Window
            colour = blend(palette.color(ink), palette.color(ground), 0.45)
        align = QStyle.visualAlignment(option.direction, Qt.AlignmentFlag.AlignRight)
        painter.save()
        painter.setPen(colour)
        painter.drawText(
            option.rect.adjusted(0, 0, -SHORTCUT_MARGIN, 0),
            int(align | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine),
            shortcut,
        )
        painter.restore()
