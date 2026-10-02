# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/window_outputs.py

"""The main window's outputs: viewing, following, exporting and saving them."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from pdftl.gui import stage_model, viewers, widgets
from pdftl.gui.console import parse_ansi
from pdftl.gui.interfaces import Pipeline, ResultKind, StageResult
from pdftl.gui.output_box import OutputBox
from pdftl.gui.viewer_dialog import ViewerDialog, load_viewer, save_viewer
from pdftl.gui.preview_dialog import PreviewDialog
from pdftl.gui.widgets import StageBox, StripBox
from pdftl.gui.window_dialogs import open_in_system_viewer
from pdftl.gui.window_run import Saver

CHOOSE_VIEWER_HINT = "View > Choose PDF Viewer picks another"


class OutputsMixin:
    """Stage outputs, the Output box, and the PDF viewer."""

    def _build_output(self) -> None:
        self.output_box = OutputBox(sorted(stage_model.output_option_names()))
        self.output_box.menu_requested.connect(self._show_box_menu)
        self.output_box.options_edited.connect(self._output_options_edited)
        self.output_box.options_committed.connect(self._output_options_committed)
        self.output_box.save_requested.connect(self.save)
        self.output_box.follow_requested.connect(self.toggle_output_follow)
        self.output_box.options.cursorPositionChanged.connect(
            lambda _old, _new: self._maybe_refresh_output_help()
        )

    def open_url(self, path: Path) -> bool:
        """Open `path` in the chosen PDF viewer."""
        viewer = load_viewer(widgets.settings_factory())
        return open_in_system_viewer(path) if viewer.is_system else viewers.launch(viewer, path)

    def choose_viewer(self) -> None:
        settings = widgets.settings_factory()
        dialog = ViewerDialog(load_viewer(settings), self.discover_viewers(), self)
        if self.run_dialog(dialog):
            save_viewer(settings, dialog.viewer())
            self.statusBar().showMessage(f"PDF viewer: {dialog.viewer().name}", 4000)
        dialog.deleteLater()

    def _output_options_edited(self) -> None:
        """Validate the box's text on every keystroke; a stray `output` here is
        shown as a problem on the box itself until the field is committed (Enter
        or losing focus), which resolves it into the save target instead."""
        text = self.output_box.options_text()
        try:
            stage_model.parse_output_options(text)
        except ValueError as exc:
            self.output_box.set_error(str(exc))
        else:
            self.output_box.set_error("")
        self.schedule(QApplication.focusWidget())

    def _output_options_committed(self) -> None:
        """`output <path>` typed here sets the save target directly, like the
        command bar's own `output` clause, once the field is committed."""
        text = self.output_box.options_text()
        try:
            remaining, target = stage_model.extract_output_clause(text)
        except ValueError:
            return  # already reported by _output_options_edited
        if target is None:
            return
        self.save_target = target
        self.output_box.set_target(target)
        self.output_box.set_options_text(remaining)
        self.output_box.set_error("")
        self.statusBar().showMessage(f"Output target set to {target} (Ctrl+S writes it)", 6000)
        self.schedule(QApplication.focusWidget())

    def _connect_box(self, box: StripBox) -> None:
        box.menu_requested.connect(self._show_box_menu)
        box.view_requested.connect(self.view_output)
        box.export_requested.connect(self.export_output)
        box.save_text_requested.connect(self.save_output_text)
        box.follow_requested.connect(self.toggle_follow)
        box.output_changed.connect(self._update_actions)
        box.preview_requested.connect(self.preview_page)

    def _box_name(self, box: StripBox) -> str:
        if box in self.boxes:
            return stage_model.output_stem(self.current_pipeline(), self.boxes.index(box))
        return box.path.stem if box.path is not None else "input"

    def _focused_output(self) -> StripBox | None:
        box = self.current
        if box is None or box.path is None:
            self.statusBar().showMessage("Focus a stage that has output first", 4000)
            return None
        return box

    def view_focused(self) -> None:
        if box := self._focused_output():
            self.view_output(box)

    def export_focused(self) -> None:
        if box := self._focused_output():
            self.export_output(box)

    def _output_dir(self, name: str) -> Path:
        path = self.engine.cache_dir / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def view_output(self, box: StripBox) -> None:
        """Open a snapshot: later runs never change the file the viewer shows."""
        target = box.path
        if box is not self.inputs_box:
            target = self._output_dir("view") / f"{self._box_name(box)}-{box.key[:12]}.pdf"
            if not target.exists():
                try:
                    shutil.copyfile(box.path, target)
                except OSError as exc:
                    self._warn(f"Could not open {target.name}: {exc}")
                    return
        if not self.open_url(target):
            self._warn(f"No viewer could open {target}; {CHOOSE_VIEWER_HINT}")

    def follow_focused(self) -> None:
        box = self.current
        if box in self.boxes:
            self.toggle_follow(box)
        else:
            self.statusBar().showMessage("Focus a stage to follow its output", 4000)

    def toggle_follow(self, box: StageBox) -> None:
        """Open a file that is rewritten whenever `box`'s output changes."""
        if self.followed.pop(box, None) is not None:
            box.follow.setChecked(False)
            self._update_actions()
            self._sync_output_follow()
            return
        box.follow.setChecked(False)
        if box.path is None:
            self.statusBar().showMessage("This stage has no output to follow yet", 4000)
            return
        self._follow_serial += 1
        name = f"{self._box_name(box)}-live{self._follow_serial}.pdf"
        self.followed[box] = (self._output_dir("follow") / name, None)
        self._refresh_follow(box)
        if self.open_url(self.followed[box][0]):
            box.follow.setChecked(True)
        else:
            self._warn(f"No viewer could open {self.followed.pop(box)[0]}; {CHOOSE_VIEWER_HINT}")
        self._sync_output_follow()

    def toggle_output_follow(self) -> None:
        """Follow the final stage's output, as the Output box's own Follow button."""
        if not self.boxes:
            self.statusBar().showMessage("No stage output to follow yet", 4000)
            return
        self.toggle_follow(self.boxes[-1])

    def _sync_output_follow(self) -> None:
        following = bool(self.boxes) and self.boxes[-1] in self.followed
        self.output_box.set_following(following)
        if not following:
            self.output_box.set_stale("")

    def _refresh_follow(self, box: StageBox) -> None:
        target, written = self.followed[box]
        if box.path is None or box.key == written:
            return
        try:
            # In place, not rename: evince reloads on "changed", not "created".
            shutil.copyfile(box.path, target)
        except OSError as exc:
            self._warn(f"Could not update {target.name}: {exc}")
            return
        self.followed[box] = (target, box.key)

    def export_output(self, box: StripBox) -> None:
        path = self.ask_save_path(
            self, "Save stage output", f"{self._box_name(box)}.pdf", "PDF files (*.pdf)"
        )
        if not path:
            return
        try:
            shutil.copyfile(box.path, path)
        except OSError as exc:
            self._warn(f"Could not save {path}: {exc}")
            return
        self.statusBar().showMessage(f"Saved {path}", 5000)

    def save_output_text(self, box: StripBox) -> None:
        path = self.ask_save_path(
            self, "Save stage text", f"{self._box_name(box)}.txt", "Text files (*.txt)"
        )
        if not path:
            return
        plain = "".join(text for text, _style in parse_ansi(box.text)[0])
        try:
            Path(path).write_text(plain, encoding="utf-8")
        except OSError as exc:
            self._warn(f"Could not save {path}: {exc}")
            return
        self.statusBar().showMessage(f"Saved {path}", 5000)

    def preview_page(self, box: StripBox, page: int) -> None:
        if box.path is None or box.key is None:
            return
        self.preview = PreviewDialog(self.thumbs, box.key, box.path, page, box.total, self)
        self.preview.destroyed.connect(self._preview_closed)
        self.preview.show()

    def _preview_closed(self) -> None:
        self.preview = None

    def copy_cli(self) -> None:
        text = self._cli_text(self.current_pipeline(), self.save_target)
        QGuiApplication.clipboard().setText(text)
        self.statusBar().showMessage("Command copied (passwords shown as PROMPT)", 3000)

    def save(self) -> None:
        p = self.current_pipeline()
        if not p.stages:
            return
        default = str(self.save_target or "")
        path = self.ask_save_path(self, "Save output", default, "PDF files (*.pdf)")
        if not path:
            return
        if self.saver is not None and self.saver.isRunning():
            self.statusBar().showMessage("A save is already running", 4000)
            return
        p = self._with_output_passwords(p)
        if p is None:
            return
        self.save_target = Path(path)
        self.output_box.set_target(self.save_target)
        self._say(f"$ {self._cli_text(p, Path(path))}")
        self.statusBar().showMessage(f"Saving {path}...")
        self.saver = Saver(self.engine, p, Path(path))
        self.saver.saved.connect(self._save_done)
        self.saver.start()

    def _with_output_passwords(self, p: Pipeline) -> Pipeline | None:
        """`p` with each PROMPT output password asked for; None if one is cancelled."""
        values = {}
        for option, value in stage_model.output_passwords(p.output_options).items():
            if value == stage_model.MASK:
                who = option.removesuffix("_pw")
                answer = self.ask_password(
                    self, "Output password", f"{who} password for the saved PDF:"
                )
                if answer is None:
                    return None
                values[option] = answer
        return replace(
            p, output_options=stage_model.fill_output_passwords(p.output_options, values)
        )

    def _save_done(self, result: StageResult) -> None:
        self._announced = True
        self._log_result(result, "Save")
        ok = result.kind is not ResultKind.ERROR
        path = result.pdf_path
        if ok:
            self.statusBar().showMessage(f"Saved {path}", 5000)
        else:
            self._warn("Save failed; see console")
