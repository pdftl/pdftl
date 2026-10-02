# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/window_inputs.py

"""The main window's input files and their passwords."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pikepdf
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QListWidget, QStyle

from pdftl.gui import keymap, stage_model
from pdftl.gui.icons import delete_icon, themed, tool_button
from pdftl.gui.interfaces import ResultKind, plural
from pdftl.gui.widgets import StripBox
from pdftl.gui.window_recent import remember


class InputsMixin:
    """The Inputs box: its file list, previews and passwords."""

    def _build_inputs(self) -> None:
        self.inputs_box = StripBox("<b>Inputs</b>", self.thumbs)
        self._connect_box(self.inputs_box)
        self.input_list = QListWidget()
        self.input_list.setMaximumHeight(90)
        self.input_list.setAccessibleName("Input files")
        self.input_list.setToolTip("Input files and their handles; right-click for more")
        self.inputs_box.layout_.insertWidget(1, self.input_list)
        self.inputs_box.focus_fallback = self.input_list
        style = self.style()
        self.add_input_button = tool_button(
            themed(style, "list-add", QStyle.StandardPixmap.SP_DialogOpenButton),
            "Add input files",
            "add_input",
        )
        self.remove_input_button = tool_button(
            delete_icon(), "Remove selected input file", "remove_input"
        )
        self.add_input_button.clicked.connect(self.open_input)
        self.remove_input_button.clicked.connect(self.remove_current_input)
        for button in (self.add_input_button, self.remove_input_button):
            self.inputs_box.title_row.addWidget(button)
        self.inputs_box.install_click_filters()
        self.input_list.currentRowChanged.connect(self._preview_input)
        remove = QShortcut(QKeySequence(keymap.keys_for("remove_input")[0]), self.input_list)
        remove.setContext(Qt.ShortcutContext.WidgetShortcut)
        remove.activated.connect(self.remove_current_input)

    def open_input(self) -> None:
        for path in self.ask_open_paths(self):
            self.add_file(path)

    def add_file(self, path: str | Path) -> None:
        self.pipeline = stage_model.add_input(self.pipeline, Path(path))
        f = self.pipeline.inputs[-1]
        remember("inputs", f.path)
        self.input_list.addItem(f"{f.handle}  {f.path}")
        self.input_list.setCurrentRow(self.input_list.count() - 1)
        self.schedule()

    def remove_current_input(self) -> None:
        row = self.input_list.currentRow()
        if row < 0:
            return
        handle = self.pipeline.inputs[row].handle
        self.pipeline = stage_model.remove_input(self.pipeline, handle)
        self.input_list.blockSignals(True)
        self.input_list.takeItem(row)
        self.input_list.blockSignals(False)
        if self.pipeline.inputs:
            self._preview_input(self.input_list.currentRow())
        else:
            self.inputs_box.show_pdf(None, status="No input files")
        self.schedule()

    def _preview_input(self, row: int) -> None:
        if row < 0:
            return
        f = self.pipeline.inputs[row]
        self.inputs_box.handle = f.handle
        r = self.engine.preview_input(f)
        if r.needs_password and self.ask_input_password(row):
            return
        if r.kind is ResultKind.ERROR:
            self.inputs_box.show_pdf(None, status=f"{f.handle}: ✗ {r.error}")
            return
        status = f"{f.handle}: {f.path.name}, {plural(r.page_count, 'page')}"
        if r.password_kind:
            status += f" ({r.password_kind} password)"
        self.inputs_box.show_pdf(r.pdf_path, r.page_count, status, key=r.key)

    def _verify_password(self, path: Path, password: str) -> bool:
        try:
            with pikepdf.open(path, password=password):
                return True
        except pikepdf.PasswordError:
            return False

    def _prompt_password(self, path: Path, label: str) -> str | None:
        """Ask until the answer verifies; `""` if left blank, None if cancelled."""
        prompt = label
        while True:
            password = self.ask_password(self, "Password", prompt)
            if not password or self._verify_password(path, password):
                return password
            prompt = f"Wrong password. {label}"

    def ask_input_password(self, row: int) -> bool:
        """Prompt once per file for a password it needs; True if one was applied."""
        f = self.pipeline.inputs[row]
        if Path(f.path) in self._declined:
            return False
        password = self._prompt_password(Path(f.path), f"Password for {f.path.name}:")
        if not password:
            self._declined.add(Path(f.path))
            return False
        self._set_input_password(row, password)
        return True

    def _set_input_password(self, row: int, password: str | None) -> None:
        """Store a verified password (or clear it) for input `row`, as its own undo step."""
        f = self.pipeline.inputs[row]
        path = Path(f.path)
        inputs = list(self.pipeline.inputs)
        inputs[row] = replace(f, password=password)
        self.pipeline = replace(self.pipeline, inputs=tuple(inputs))
        (self._declined.discard if password else self._declined.add)(path)
        self._preview_input(row)
        self.schedule()

    def set_password(self) -> None:
        """Set, replace or clear the selected input file's password (blank clears it)."""
        row = self.input_list.currentRow()
        if row < 0:
            self.statusBar().showMessage("Select an input file first", 4000)
            return
        f = self.pipeline.inputs[row]
        label = f"New password for {f.path.name} (blank clears it):"
        password = self._prompt_password(Path(f.path), label)
        if password is not None:
            self._set_input_password(row, password or None)

    def _password_for(self, path: Path) -> str | None:
        return next((f.password for f in self.pipeline.inputs if Path(f.path) == path), None)

    def _row_for_path(self, path: Path) -> int | None:
        return next((i for i, f in enumerate(self.pipeline.inputs) if Path(f.path) == path), None)
