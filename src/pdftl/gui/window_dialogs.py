# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/window_dialogs.py

"""The main window's dialogs, as plain functions tests can replace."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QLineEdit, QMessageBox

PIPELINE_FILTER = "pdftl pipeline (*.yaml *.yml)"


def ask_save_path(parent, title: str, default: str, filters: str) -> str:
    return QFileDialog.getSaveFileName(parent, title, default, filters)[0]


def ask_open_paths(parent) -> list[str]:
    return QFileDialog.getOpenFileNames(
        parent, "Add input PDFs", "", "PDF files (*.pdf);;All files (*)"
    )[0]


def ask_text(parent, title: str, label: str) -> str:
    text, ok = QInputDialog.getText(parent, title, label)
    return text if ok else ""


def ask_password(parent, title: str, label: str) -> str | None:
    """None if cancelled."""
    text, ok = QInputDialog.getText(parent, title, label, QLineEdit.EchoMode.Password)
    return text if ok else None


def open_in_system_viewer(path: Path) -> bool:
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def ask_pipeline_open_path(parent) -> str:
    return QFileDialog.getOpenFileName(parent, "Open Pipeline", "", PIPELINE_FILTER)[0]


def ask_pipeline_save_path(parent, default: str) -> str:
    dialog = QFileDialog(parent, "Save Pipeline", default, PIPELINE_FILTER)
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    dialog.setDefaultSuffix("yaml")
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return ""
    files = dialog.selectedFiles()
    return files[0] if files else ""


def ask_discard_pipeline(parent) -> str:
    """ "save", "discard" or "cancel"."""
    box = QMessageBox(parent)
    box.setWindowTitle("Unsaved Pipeline")
    box.setText("This pipeline has unsaved changes. Save it before continuing?")
    save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
    discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
    box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(save)
    box.exec()
    return {save: "save", discard: "discard"}.get(box.clickedButton(), "cancel")
