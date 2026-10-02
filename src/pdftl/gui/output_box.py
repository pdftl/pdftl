# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/output_box.py

"""The Output pseudo-stage: save target, output options, Save and Follow.

A `BoxFrame` like the stages, but with no pages of its own, so it needs no
page strip or thumbnail provider.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCompleter,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from pdftl.gui.box_frame import FAILED_STATUS_STYLE, BoxFrame
from pdftl.gui.icons import text_button, themed, tooltip

NO_TARGET = "(none — Save picks one)"


class OutputBox(BoxFrame):
    """A titled box for output-only CLI options, below the last stage.

    `options_edited` fires on every keystroke (`QLineEdit.textEdited`), like
    a stage's own arguments field; the main window turns that into an
    undoable, re-run-triggering edit the same way. `options_committed` fires
    once the field is left (Enter or losing focus): a completed `output
    <path>` clause is only resolved into the save target then, not
    mid-keystroke, when it would otherwise capture a partial path.
    """

    options_edited = Signal()
    options_committed = Signal()
    save_requested = Signal()
    follow_requested = Signal()

    def __init__(self, option_names: list[str]) -> None:
        super().__init__("<b>Output</b>")
        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        self.target = QLabel(NO_TARGET)
        self.target.setToolTip("Where Save writes the final PDF")
        layout.addWidget(self.target)
        self.error = QLabel("")
        layout.addWidget(self.error)
        self.stale = QLabel("")
        self.stale.setStyleSheet(FAILED_STATUS_STYLE)
        self.stale.setWordWrap(True)
        self.stale.hide()
        layout.addWidget(self.stale)

        self.options = QLineEdit()
        self.options.setPlaceholderText(
            "output options: uncompress, flatten, owner_pw <pw>, ... (Alt+T)"
        )
        self.options.setAccessibleName("Output options")
        self.options.setToolTip(
            tooltip("Output options, applied when the final PDF is written", "focus_output")
        )
        completer = QCompleter(sorted(option_names), self.options)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.options.setCompleter(completer)
        layout.addWidget(self.options)

        style = self.style()
        self.save = text_button(
            themed(style, "document-save-as", QStyle.StandardPixmap.SP_DialogSaveButton),
            "Save",
            "save",
        )
        self.follow = text_button(
            themed(style, "view-refresh", QStyle.StandardPixmap.SP_BrowserReload),
            "Follow",
            checkable=True,
        )
        self.follow.setToolTip(
            "Open the final output in the system PDF viewer and keep it updated"
        )
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.save)
        buttons.addWidget(self.follow)
        layout.addLayout(buttons)

        self.setTabOrder(self.options, self.save)
        self.setTabOrder(self.save, self.follow)

        self.options.textEdited.connect(lambda _text: self.options_edited.emit())
        self.options.editingFinished.connect(lambda: self.options_committed.emit())
        self.save.clicked.connect(lambda: self.save_requested.emit())
        self.follow.clicked.connect(lambda: self.follow_requested.emit())
        self.install_click_filters()

    def focus_target(self) -> QWidget:
        return self.options

    def set_target(self, path: Path | None) -> None:
        self.target.setText(f"Save target: {path}" if path is not None else NO_TARGET)

    def options_text(self) -> str:
        return self.options.text()

    def set_options_text(self, text: str) -> None:
        self.options.setText(text)

    def set_error(self, message: str) -> None:
        """Show a problem with the typed options, right here rather than on a stage."""
        self.error.setText(f"⚠ {message}" if message else "")

    def set_stale(self, message: str) -> None:
        """Say why the followed viewer file is out of date; "" once it is current."""
        self.stale.setText(message)
        self.stale.setVisible(bool(message))

    def set_following(self, following: bool) -> None:
        self.follow.setChecked(following)
