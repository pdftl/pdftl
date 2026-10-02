# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/window_run.py

"""The main window's runs: the command bar, running stages and showing failures."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import QLabel, QProgressBar

from pdftl.gui import op_policy, stage_model
from pdftl.gui.box_frame import FAILED_STATUS_STYLE
from pdftl.gui.command_bar import ParsedCommand
from pdftl.gui.engine import SubprocessEngine
from pdftl.gui.history import Snapshot
from pdftl.gui.interfaces import Pipeline, ResultKind, StageResult, plural

WARN_STYLE = f"QStatusBar {{ {FAILED_STATUS_STYLE} }}"

STALE_NOTE = "the PDF viewer still shows the last good output"

SPINNER = "◐◓◑◒"


class Runner(QThread):
    """Iterates the engine off the GUI thread, emitting each StageResult."""

    stage_done = Signal(object)

    def __init__(self, engine: SubprocessEngine, pipeline: Pipeline) -> None:
        super().__init__()
        self.setObjectName("pdftl-runner")
        self.engine, self.pipeline = engine, pipeline
        self.cancel = threading.Event()

    def run(self) -> None:
        for result in self.engine.run(self.pipeline, self.cancel):
            self.stage_done.emit(result)

    def stop(self) -> None:
        self.cancel.set()
        self.wait()


class Saver(QThread):
    """Runs a one-shot save off the GUI thread."""

    saved = Signal(object)

    def __init__(self, engine: SubprocessEngine, pipeline: Pipeline, path: Path) -> None:
        super().__init__()
        self.setObjectName("pdftl-saver")
        self.engine, self.pipeline, self.path = engine, pipeline, path
        self.cancel = threading.Event()

    def run(self) -> None:
        self.saved.emit(self.engine.save(self.pipeline, self.path, self.cancel))

    def stop(self) -> None:
        self.cancel.set()
        self.wait()


class RunMixin:
    """The command bar, the debounced run, and each stage's result."""

    # Command bar

    def current_pipeline(self) -> Pipeline:
        """A bad Output box edit is never fed to the engine: it stays reported on the
        box itself (`_output_options_edited`) instead of failing "the last stage"."""
        text = self.output_box.options_text()
        try:
            stage_model.parse_output_options(text)
        except ValueError:
            text = ""
        return Pipeline(self.pipeline.inputs, tuple(b.stage() for b in self.boxes), text)

    @staticmethod
    def _cli_text(p: Pipeline, output: Path | None = None) -> str:
        try:
            return stage_model.to_cli(p, output)
        except ValueError as exc:
            return f"(not runnable: {exc})"

    def focus_command(self) -> None:
        self.cli.setFocus()
        self.cli.selectAll()

    def _warn(self, text: str, timeout: int = 5000) -> None:
        """Show `text` in bold red until the next status message replaces it."""
        bar = self.statusBar()
        bar.showMessage(text, timeout)
        bar.setStyleSheet(WARN_STYLE)

    def _unwarn(self, _text: str = "") -> None:
        if self.statusBar().styleSheet():
            self.statusBar().setStyleSheet("")

    def _command_failed(self, message: str) -> None:
        self._warn(f"Command not applied: {message}", 8000)
        self.console.append_ansi(f"\x1b[1;31mCommand not applied: {message}\x1b[0m\n")

    def apply_command(self, parsed: ParsedCommand) -> None:
        """Replace inputs and stages; a masked PROMPT password keeps the known one, or
        None for a path this window hasn't seen a verified password for."""
        known = {Path(f.path): f.password for f in self.pipeline.inputs if f.password}
        inputs = tuple(
            replace(f, password=known.get(Path(f.path))) if f.password == stage_model.MASK else f
            for f in parsed.pipeline.inputs
        )
        options = stage_model.fill_output_passwords(
            parsed.pipeline.output_options,
            stage_model.output_passwords(self.current_pipeline().output_options),
        )
        pipeline = Pipeline(inputs, parsed.pipeline.stages, options)
        self._restore(Snapshot(pipeline, parsed.output))
        self._show_command()
        if parsed.output is not None:
            self.statusBar().showMessage(
                f"Command applied; output target set to {parsed.output} (Ctrl+S writes it)", 6000
            )
        else:
            self.statusBar().showMessage("Command applied", 3000)
        self.run_pipeline()

    # Running

    def schedule(self, merge_key: object | None = None) -> None:
        self._record(merge_key)
        if not self.cli.hasFocus():
            self._show_command()
        self._update_title()
        self.debounce.start()

    def _show_command(self) -> None:
        try:
            self.cli.show_pipeline(self.current_pipeline(), self.save_target)
        except ValueError as exc:
            self.cli.setText(f"(not runnable: {exc})")

    def _command_discarded(self, _draft: str) -> None:
        self._show_command()
        self.statusBar().showMessage(
            "Unapplied command edit discarded; Up in the command bar recalls it", 8000
        )

    def run_pipeline(self) -> None:
        self.debounce.stop()
        if self._closed:
            return
        p = self.current_pipeline()
        if not p.stages:
            return
        self.stop_runner()
        if not (p.inputs or op_policy.is_source(p.stages[0].op)):
            for box in self.boxes:
                box.show_pdf(None, status="No input files")
            return
        if self.statusBar().currentMessage() == self._failure_message:
            self.statusBar().clearMessage()
        for box in self.boxes:
            box.status.setText("waiting...")
        for i, msg in stage_model.stage_problems(p):
            self.boxes[i].status.setText(f"⚠ {msg}")
        self._set_running(0)
        self._announced = False
        self._command = self._cli_text(p)
        self.runner = Runner(self.engine, p)
        self.runner.stage_done.connect(self._stage_done)
        self.runner.start()

    def stop_runner(self) -> None:
        if self.runner is not None:
            self.runner.stop()
        self._set_running(None)

    def _build_busy(self) -> None:
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setTextVisible(False)
        self.busy.setMaximumWidth(100)
        self.busy_label = QLabel()
        self.statusBar().addPermanentWidget(self.busy_label)
        self.statusBar().addPermanentWidget(self.busy)
        self.spinner = QTimer(self, interval=150)
        self.spinner.timeout.connect(self._spin)
        self._running: int | None = None
        self._spin_frame = 0
        self._set_running(None)

    def _set_running(self, index: int | None) -> None:
        """Mark stage `index` as the one running now; None, or past the end, when done."""
        self._running = index if index is not None and index < len(self.boxes) else None
        active = self._running is not None
        self.busy.setVisible(active)
        self.busy_label.setText(
            f"Running stage {self._running + 1} of {len(self.boxes)}" if active else ""
        )
        if active:
            self.spinner.start()
            self._spin()
        else:
            self.spinner.stop()

    def _spin(self) -> None:
        if self._running is None or self._running >= len(self.boxes):
            return
        self._spin_frame = (self._spin_frame + 1) % len(SPINNER)
        self.boxes[self._running].status.setText(f"{SPINNER[self._spin_frame]} running...")

    def _say(self, text: str) -> None:
        self.console.append_plain(f"{text}\n")

    def _log_result(self, result: StageResult, label: str) -> None:
        if not self._announced:
            self._say(f"$ {self._command}")
            self._announced = True
        for stream in (result.text, result.stderr):
            if stream.strip():
                self.console.append_ansi(f"\x1b[1m--- {label} ---\x1b[0m\n{stream.rstrip()}\n")
        if result.error:
            self.console.append_ansi(f"\x1b[1;31m--- {label} failed: {result.error}\x1b[0m\n")

    def _stage_done(self, result: StageResult) -> None:
        if self.sender() is not self.runner or result.index >= len(self.boxes):
            return
        box = self.boxes[result.index]
        failed = result.kind is ResultKind.ERROR
        self._set_running(None if failed else result.index + 1)
        label = f"Stage {result.index + 1} ({box.op.currentText()})"
        self._log_result(result, label)
        if failed:
            if result.needs_password and result.password_path is not None:
                row = self._row_for_path(result.password_path)
                if row is not None and self.ask_input_password(row):
                    return
            self._show_failure(result.index, label, result.error)
            return
        note = plural(result.page_count, "page") + (" (cached)" if result.cached else "")
        if result.kind is ResultKind.TEXT:
            note += "; text output in console, pages passed through"
        box.show_pdf(result.pdf_path, result.page_count, note, result.key, result.text)
        if box in self.followed:
            self._refresh_follow(box)
        if box is self.boxes[-1]:
            self.output_box.set_stale("")

    def _show_failure(self, index: int, label: str, error: str) -> None:
        """Mark stage `index` failed, the later ones not run, and any followed file stale."""
        self._failure_message = f"{label} failed: {error}; see console"
        self._warn(self._failure_message, 0)
        for box in self.boxes[index:]:
            status = f"✗ {error}; see console" if box is self.boxes[index] else "not run"
            if box in self.followed:
                status += f"; {STALE_NOTE}"
            if box is self.boxes[index]:
                box.show_failure(status)
            else:
                box.show_pdf(None, status=status)
        if self.boxes[-1] in self.followed:
            self.output_box.set_stale(f"{label} failed, so {STALE_NOTE}")
