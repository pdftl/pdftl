# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/window_files.py

"""The main window's pipeline files: new, open and save."""

from __future__ import annotations

from pathlib import Path

from pdftl.gui import pipeline_file
from pdftl.gui.history import Snapshot
from pdftl.gui.interfaces import Pipeline, Stage
from pdftl.gui.window_recent import remember


class FilesMixin:
    """New, Open and Save Pipeline, and the unsaved-changes mark."""

    def _pipeline_nonempty(self) -> bool:
        """Whether there is anything worth nagging to save: a new window always has
        one default stage but is not "in progress" until it has an input file."""
        return bool(self.pipeline.inputs)

    def _pipeline_is_dirty(self) -> bool:
        current = self._snapshot()
        if self._pipeline_saved is None:
            return self._pipeline_nonempty()
        return current != self._pipeline_saved

    def _update_title(self) -> None:
        name = self.pipeline_path.name if self.pipeline_path else "pdftl"
        self.setWindowTitle(f"{name}[*]")
        self.setWindowModified(self._pipeline_is_dirty())

    def _confirm_discard_pipeline(self) -> bool:
        """Ask to save, discard or cancel if the pipeline is dirty and non-empty.

        Returns whether the caller may proceed (discard, save, or nothing to lose).
        """
        if not (self._pipeline_is_dirty() and self._pipeline_nonempty()):
            return True
        choice = self.ask_discard_pipeline(self)
        if choice == "discard":
            return True
        if choice == "save":
            return self.save_pipeline()
        return False

    def new_pipeline(self) -> None:
        """Reset to one empty stage, no inputs, no save target.

        One undo step, like opening a pipeline file replaces the current one
        (`_restore` -> `schedule` records it); `_confirm_discard_pipeline`
        offers to save first, same as Open Pipeline.
        """
        if not self._confirm_discard_pipeline():
            return
        self._restore(Snapshot(Pipeline(stages=(Stage("cat"),))))
        self.pipeline_path = None
        self._pipeline_saved = None
        self._update_title()
        self.statusBar().showMessage("New pipeline", 3000)

    def open_pipeline(self) -> None:
        if not self._confirm_discard_pipeline():
            return
        path = self.ask_pipeline_open_path(self)
        if path:
            self._load_pipeline_file(Path(path))

    def _load_pipeline_file(self, path: Path) -> None:
        try:
            parsed = pipeline_file.load(path)
        except pipeline_file.PipelineFileError as exc:
            self._warn(f"Could not open {path}: {exc}")
            return
        self.apply_command(parsed)
        remember("pipelines", path)
        self.pipeline_path = path
        self._pipeline_saved = self._snapshot()
        self._update_title()
        self.statusBar().showMessage(f"Opened pipeline {path}", 5000)

    def save_pipeline(self) -> bool:
        """Save to `pipeline_path`, or prompt for one as Save As does. True on success."""
        if self.pipeline_path is None:
            return self.save_pipeline_as()
        return self._write_pipeline(self.pipeline_path)

    def save_pipeline_as(self) -> bool:
        default = str(self.pipeline_path or "pipeline.yaml")
        path = self.ask_pipeline_save_path(self, default)
        return self._write_pipeline(Path(path)) if path else False

    def _write_pipeline(self, path: Path) -> bool:
        try:
            pipeline_file.save(self.current_pipeline(), path, self.save_target, self.engine.cwd)
        except OSError as exc:
            self._warn(f"Could not save {path}: {exc}")
            return False
        remember("pipelines", path)
        self.pipeline_path = path
        self._pipeline_saved = self._snapshot()
        self._update_title()
        self.statusBar().showMessage(f"Saved pipeline {path}", 5000)
        return True
