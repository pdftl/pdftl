# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/icons.py

"""Portable icons and icon buttons.

Theme icons (freedesktop names) fall back to Qt's standard pixmaps, which
every platform style provides. The delete icon is painted: themes draw
`edit-delete` as anything from a trash can to a grey box.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QPushButton, QStyle, QToolButton

from pdftl.gui.keymap import keys_for

DELETE_RED = QColor(208, 49, 45)
ERROR_RED = DELETE_RED
_DELETE_SIZES = (16, 24, 32, 48)


def themed(style: QStyle, theme: str, fallback: QStyle.StandardPixmap) -> QIcon:
    return QIcon.fromTheme(theme, style.standardIcon(fallback))


def delete_icon() -> QIcon:
    """A bold red ✕, drawn at several sizes so it stays crisp."""
    icon = QIcon()
    for size in _DELETE_SIZES:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(DELETE_RED, max(2.0, size / 7))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        lo, hi = size * 0.22, size * 0.78
        painter.drawLine(QPointF(lo, lo), QPointF(hi, hi))
        painter.drawLine(QPointF(lo, hi), QPointF(hi, lo))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def tooltip(label: str, action_id: str | None) -> str:
    """`label`, plus its bound shortcut in parens if `action_id` has one."""
    keys = keys_for(action_id) if action_id else ()
    return f"{label} ({keys[0]})" if keys else label


def tool_button(
    icon: QIcon,
    label: str,
    action_id: str | None = None,
    checkable: bool = False,
) -> QToolButton:
    """Icon-only button; accessibleName carries `label` for lookups and screen readers."""
    button = QToolButton()
    button.setIcon(icon)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setCheckable(checkable)
    button.setAutoRaise(True)
    button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    button.setText(label)
    button.setAccessibleName(label)
    button.setToolTip(tooltip(label, action_id))
    return button


def text_button(
    icon: QIcon, label: str, action_id: str | None = None, checkable: bool = False
) -> QPushButton:
    """A prominent button with both icon and text."""
    button = QPushButton(icon, label)
    button.setCheckable(checkable)
    button.setAutoDefault(False)
    button.setToolTip(tooltip(label, action_id))
    return button
