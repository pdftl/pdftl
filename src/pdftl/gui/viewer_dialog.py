# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/viewer_dialog.py

"""Choosing the PDF viewer, and keeping the choice in the settings."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QWidget,
)

from pdftl.gui.viewers import SYSTEM, Viewer, program_viewer

NAME_KEY = "viewer/name"
COMMAND_KEY = "viewer/command"
OTHER = "Other program…"


def load_viewer(settings: QSettings) -> Viewer:
    command = settings.value(COMMAND_KEY, [], type=list)
    if not command:
        return SYSTEM
    return Viewer(str(settings.value(NAME_KEY, command[0])), tuple(command))


def save_viewer(settings: QSettings, viewer: Viewer) -> None:
    if viewer.is_system:
        settings.remove(NAME_KEY)
        settings.remove(COMMAND_KEY)
        return
    settings.setValue(NAME_KEY, viewer.name)
    settings.setValue(COMMAND_KEY, list(viewer.command))


class ViewerDialog(QDialog):
    """System viewer, the viewers the OS lists, or any program by its path."""

    def __init__(self, current: Viewer, found: list[Viewer], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("PDF Viewer")
        self.found = [SYSTEM, *found]
        self.ask_program: Callable[..., tuple[str, str]] = QFileDialog.getOpenFileName
        self.choice = QComboBox()
        for viewer in self.found:
            self.choice.addItem(viewer.name)
        self.choice.addItem(OTHER)
        self.choice.setToolTip("The viewer that View and Follow open stage output in")
        self.program = QLineEdit()
        self.program.setPlaceholderText("path to the viewer program")
        self.program.setAccessibleName("Viewer program")
        self.browse = QPushButton("&Browse…")
        self.browse.setAutoDefault(False)
        self.browse.clicked.connect(self._browse)
        program_row = QHBoxLayout()
        program_row.addWidget(self.program, 1)
        program_row.addWidget(self.browse)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        form = QFormLayout(self)
        form.addRow("&Viewer:", self.choice)
        form.addRow("&Program:", program_row)
        form.addRow(buttons)
        self.choice.currentIndexChanged.connect(self._update)
        self.program.textChanged.connect(self._update)
        self._select(current)
        self._update()

    def _select(self, current: Viewer) -> None:
        if current in self.found:
            self.choice.setCurrentIndex(self.found.index(current))
            return
        self.choice.setCurrentIndex(len(self.found))
        command = current.command
        self.program.setText(command[2] if command[:2] == ("open", "-a") else command[0])

    def other(self) -> bool:
        return self.choice.currentIndex() == len(self.found)

    def _update(self) -> None:
        other = self.other()
        self.program.setEnabled(other)
        self.browse.setEnabled(other)
        self.ok.setEnabled(not other or bool(self.program.text().strip()))

    def _browse(self) -> None:
        path, _ = self.ask_program(self, "Choose the PDF viewer program")
        if path:
            self.program.setText(path)

    def viewer(self) -> Viewer:
        if self.other():
            return program_viewer(self.program.text().strip())
        return self.found[self.choice.currentIndex()]
