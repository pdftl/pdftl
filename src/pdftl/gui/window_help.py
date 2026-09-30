# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/window_help.py

"""The main window's help panel, console and zoom."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QHBoxLayout,
    QStyle,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from pdftl.gui import stage_model
from pdftl.gui.about import about_box
from pdftl.gui.icons import text_button, themed
from pdftl.gui.window_menus import keys_markdown


class HelpMixin:
    """The help panel, the console and zooming the focused part."""

    def _console_panel(self) -> QWidget:
        clear = text_button(
            themed(self.style(), "edit-clear", QStyle.StandardPixmap.SP_DialogResetButton),
            "Clear",
            "clear_console",
        )
        clear.clicked.connect(self.console.clear)
        header = QHBoxLayout()
        header.addStretch(1)
        header.addWidget(clear)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(header)
        layout.addWidget(self.console)
        self.clear_button = clear
        return panel

    def _build_help(self) -> None:
        self.help = QTextBrowser()
        self.help.setOpenExternalLinks(True)
        self.help_dock = QDockWidget("Help (F1)")
        self.help_dock.setObjectName("help")
        self.help_dock.setWidget(self.help)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.help_dock)
        self.resizeDocks([self.help_dock], [380], Qt.Orientation.Horizontal)
        QApplication.instance().focusChanged.connect(self._follow_focus)

    def zoom_focused(self, steps: int) -> None:
        area = self.zoom.step(QApplication.focusWidget(), steps)
        self.statusBar().showMessage(f"{area.label} zoom {area.percent()}%", 3000)

    def focus_console(self) -> None:
        self.console_dock.show()
        self.console_dock.raise_()
        self.console.setFocus()

    # Console

    def find_text(self) -> None:
        needle = self.ask_text(self, "Find in console", "Text:")
        if not needle:
            return
        if not self.console.find(needle):
            self.console.moveCursor(self.console.textCursor().MoveOperation.Start)
            if not self.console.find(needle):
                self.statusBar().showMessage(f"Not found: {needle}", 4000)
                return
        self.console.setFocus()

    def save_console(self) -> None:
        path = self.ask_save_path(self, "Save console", "pdftl-console.txt", "Text (*.txt)")
        if path:
            self.console.save_to(Path(path))
            self.statusBar().showMessage(f"Saved {path}", 5000)

    # Help

    def _follow_focus(self, _old, new) -> None:
        if new is None or new is self.help or self.help.isAncestorOf(new):
            return
        self.history.break_merge()
        if self.output_box.isAncestorOf(new):
            self._set_current(None)
            self.output_box.set_current(True)
            self.show_output_help()
            return
        box = self._focused_box()
        if box is not None and box is not self.current:
            self._set_current(box)
            self.scroll.horizontalScrollBar().setValue(0)
        self.show_help(box.op.currentText().strip() if box in self.boxes else "")

    def _option_word_at_cursor(self) -> str:
        field = self.output_box.options
        return stage_model.word_at(field.text(), field.cursorPosition())

    def _maybe_refresh_output_help(self) -> None:
        if self.output_box.options.hasFocus():
            self.show_output_help()

    def show_output_help(self) -> None:
        """Help for every output option, the word under the cursor shown first."""
        self._help_op = None  # force show_help to redraw once focus leaves here
        word = self._option_word_at_cursor()
        self.help.setMarkdown(stage_model.output_options_help_markdown(word or None))

    def show_help(self, op: str) -> None:
        if op == self._help_op:
            return
        self._help_op = op
        info = self.infos.get(op)
        if info is None:
            self.help.setMarkdown(self._general_help(op))
            return
        parts = [f"# {info.name}", info.desc, f"**Usage:** `{info.usage}`"]
        if info.reason:
            parts.append(f"*In the GUI:* {info.reason}")
        parts.append(info.long_desc.strip())
        if info.examples:
            parts.append("## Examples")
            parts += [f"- `pdftl {cmd}`  \n  {desc}" for cmd, desc in info.examples]
        self.help.setMarkdown("\n\n".join(parts))

    @staticmethod
    def _general_help(op: str) -> str:
        unknown = f"**Unknown operation `{op}`.**\n\n" if op else ""
        keys = keys_markdown()
        return (
            f"{unknown}# pdftl GUI\n\nEach stage runs one operation on the previous stage's "
            "output. Focus a stage to see help for its operation here.\n\n"
            "Select pages in any strip and press Ctrl+E to insert them as a page spec into "
            "the next stage. Stages that print text (dump_text, usage, ...) write to the "
            "console and pass their pages through. Enter on a page opens a large preview.\n\n"
            f"## Keys\n\n{keys}"
        )

    def toggle_help(self) -> None:
        if self.help.hasFocus():
            target = self._help_return
            if target is None or not target.isVisible():
                target = self.boxes[self.current_index()].op if self.boxes else self.cli
            target.setFocus()
            return
        self._help_return = QApplication.focusWidget()
        self.help_dock.show()
        self.help.setFocus()

    def show_about(self) -> None:
        self.run_dialog(about_box(self))

    def show_shortcuts(self) -> None:
        self._help_op = None
        self.show_help("")
        self.help_dock.show()
        self.help.setFocus()
