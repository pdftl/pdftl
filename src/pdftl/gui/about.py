# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/about.py

"""The application icon and the About box."""

from __future__ import annotations

from pathlib import Path

import PySide6
from PySide6.QtCore import QSize, Qt, qVersion
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QLabel, QMessageBox, QWidget

from pdftl import __version__

ICON_PATH = Path(__file__).with_name("pdftl.svg")
ABOUT_ICON_SIZE = 96

LINKS = (
    ("Documentation", "https://pdftl.readthedocs.io"),
    ("Source", "https://github.com/pdftl/pdftl"),
    ("Report an issue", "https://github.com/pdftl/pdftl/issues"),
)


def app_icon() -> QIcon:
    return QIcon(str(ICON_PATH))


def about_html() -> str:
    links = " · ".join(f'<a href="{url}">{text}</a>' for text, url in LINKS)
    return (
        f"<h2>pdftl {__version__}</h2>"
        "<p>PDF manipulation inspired by pdftk, on the command line and on the desktop.</p>"
        f"<p>{links}</p>"
        "<p>Licensed under the Mozilla Public License 2.0.</p>"
        "<p><small>The icon incorporates elements of Google Noto Emoji "
        "(Apache License 2.0).<br>"
        f"Qt {qVersion()}, PySide6 {PySide6.__version__}</small></p>"
    )


def about_box(parent: QWidget | None = None) -> QMessageBox:
    """A modal About box; its links open in the browser and are reachable with Tab."""
    box = QMessageBox(parent)
    box.setWindowTitle("About pdftl")
    box.setIconPixmap(app_icon().pixmap(QSize(ABOUT_ICON_SIZE, ABOUT_ICON_SIZE)))
    box.setTextFormat(Qt.TextFormat.RichText)
    box.setText(about_html())
    box.setStandardButtons(QMessageBox.StandardButton.Close)
    label = box.findChild(QLabel, "qt_msgbox_label")
    label.setOpenExternalLinks(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    return box
