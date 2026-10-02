# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/gui/test_icons.py

"""Tests for icons: themed fallbacks, the painted delete icon, icon buttons."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QPushButton, QStyle, QToolButton

from pdftl.gui.icons import delete_icon, text_button, themed, tool_button, tooltip
from pdftl.gui.keymap import keys_for


def _pixels(icon, size):
    image = icon.pixmap(QSize(size, size)).toImage()
    return [image.pixelColor(x, y) for y in range(image.height()) for x in range(image.width())]


def test_delete_icon_is_a_red_cross_on_a_clear_background(qapp):
    icon = delete_icon()
    for size in (16, 32):
        pixels = _pixels(icon, size)
        red = [c for c in pixels if c.alpha() > 200 and c.red() > 150 and c.green() < 90]
        clear = [c for c in pixels if c.alpha() == 0]
        assert red, size
        assert len(clear) > len(pixels) // 2, size


def test_delete_icon_marks_both_diagonals(qapp):
    image = delete_icon().pixmap(QSize(32, 32)).toImage()
    for x, y in ((8, 8), (24, 24), (8, 24), (24, 8), (16, 16)):
        assert image.pixelColor(x, y).alpha() > 200, (x, y)
    for x, y in ((16, 4), (4, 16), (28, 16), (16, 28)):
        assert image.pixelColor(x, y).alpha() == 0, (x, y)


def test_themed_falls_back_to_the_standard_pixmap(qapp):
    style = qapp.style()
    icon = themed(style, "no-such-theme-icon-anywhere", QStyle.StandardPixmap.SP_ArrowUp)
    assert not icon.isNull()


def test_tooltip_adds_the_first_bound_key():
    assert tooltip("Save", "save") == f"Save ({keys_for('save')[0]})"
    assert tooltip("About", "about") == "About"
    assert tooltip("Plain", None) == "Plain"


def test_tool_button_is_icon_only_and_named(qapp):
    button = tool_button(delete_icon(), "Delete stage", "delete_stage", checkable=True)
    assert isinstance(button, QToolButton)
    assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    assert button.accessibleName() == "Delete stage"
    assert button.text() == "Delete stage"
    assert button.isCheckable()
    assert button.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert button.toolTip() == f"Delete stage ({keys_for('delete_stage')[0]})"


def test_text_button_shows_icon_and_text(qapp):
    button = text_button(delete_icon(), "Save", "save")
    assert isinstance(button, QPushButton)
    assert button.text() == "Save"
    assert not button.icon().isNull()
    assert not button.autoDefault()
    assert not button.isCheckable()
    assert text_button(delete_icon(), "Follow", checkable=True).isCheckable()
