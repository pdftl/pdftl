# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/gui/test_about.py

import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QMessageBox

import pdftl
from pdftl.gui.about import ABOUT_ICON_SIZE, ICON_PATH, about_box, about_html, app_icon

REPO_ICON = Path(__file__).parents[2] / ".github" / "assets" / "pdftl.svg"


def test_packaged_icon_matches_the_project_icon():
    assert ICON_PATH.read_bytes() == REPO_ICON.read_bytes()


def test_icon_renders_the_artwork(qtbot):
    image = app_icon().pixmap(QSize(64, 64)).toImage()
    assert image.width() == 64
    colours = {QColor(image.pixel(x, y)).name() for x in range(64) for y in range(64)}
    opaque = sum(image.pixelColor(x, y).alpha() > 0 for x in range(64) for y in range(64))
    assert 500 < opaque < 64 * 64
    assert len(colours) > 10


def test_about_text_names_version_links_and_licences():
    html = about_html()
    assert f"pdftl {pdftl.__version__}" in html
    hrefs = re.findall(r'href="([^"]+)"', html)
    assert hrefs == [
        "https://pdftl.readthedocs.io",
        "https://github.com/pdftl/pdftl",
        "https://github.com/pdftl/pdftl/issues",
    ]
    assert "Mozilla Public License 2.0" in html
    assert "Noto Emoji" in html and "Apache License 2.0" in html


def test_about_box_shows_the_icon_and_opens_links(qtbot):
    box = about_box()
    qtbot.addWidget(box)
    if sys.platform != "darwin":  # macOS message boxes have no title
        assert box.windowTitle() == "About pdftl"
    assert box.iconPixmap().width() == ABOUT_ICON_SIZE
    label = box.findChild(QLabel, "qt_msgbox_label")
    assert label.openExternalLinks()
    assert label.textInteractionFlags() & Qt.TextInteractionFlag.LinksAccessibleByKeyboard
    assert box.standardButtons() == QMessageBox.StandardButton.Close
