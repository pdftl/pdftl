# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/command_bar.py

"""Parse a typed or pasted pdftl command line into a `Pipeline`.

Splitting and stage structure reuse pdftl's own CLI parser so semantics
match exactly. The GUI layer (`CommandBar`) is a `QLineEdit` that parses on
Enter and keeps a history of what was submitted.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from pathlib import Path

import pdftl.core.constants as c
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QLineEdit

from pdftl.cli.parser import (
    _find_operation_and_split,
    parse_cli_stage,
    parse_options_and_specs,
    split_args_by_separator,
)
from pdftl.cli.pipeline import CliStage
from pdftl.exceptions import UserCommandLineError
from pdftl.gui import shell_style
from pdftl.gui.interfaces import InputFile, Pipeline, Stage
from pdftl.gui.menu_style import blend
from pdftl.gui.stage_model import option_tokens, to_cli
from pdftl.registry_init import initialize_registry

_PROGRAM_NAMES = ("pdftl", "pdftl.exe")


@dataclass(frozen=True)
class ParsedCommand:
    pipeline: Pipeline
    output: Path | None = None


def _drop_program_token(tokens: list[str]) -> list[str]:
    if not tokens:
        return tokens
    name = tokens[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    return tokens[1:] if name in _PROGRAM_NAMES else tokens


def _reject_non_str(tokens: list, i: int) -> None:
    if any(not isinstance(t, str) for t in tokens):
        raise ValueError(
            f"stage {i + 1}: inline sub-pipelines (JOB...DONE) and EACH blocks are not supported"
        )


def _validate_inputs(stage: CliStage, i: int) -> None:
    for filename in stage.inputs:
        if filename == "PROMPT":
            raise ValueError(f"stage {i + 1}: 'PROMPT' input placeholders are not supported")
        if filename == "-":
            raise ValueError(f"stage {i + 1}: reading input from stdin ('-') is not supported")
        if filename == "_" and i == 0:
            raise ValueError(
                f"stage {i + 1}: '_' in the first stage reads from stdin, which is not supported"
            )


def _reverse_handles(handles: dict) -> dict:
    return {idx: name for name, idx in handles.items() if name != "_"}


def _password_at(stage: CliStage, idx: int) -> str | None:
    """`stage.input_passwords` is always as long as `stage.inputs`."""
    return stage.input_passwords[idx]


def _free_handle(by_handle: dict, reserved: set) -> str:
    for letter in string.ascii_uppercase:
        if letter not in by_handle and letter not in reserved:
            return letter
    raise ValueError("all 26 input handles A-Z are in use")


def _same_path(a: Path, b: Path) -> bool:
    return a.expanduser().resolve() == b.expanduser().resolve()


def _first_stage_inputs(stage: CliStage, pipeline_inputs: list, by_handle: dict) -> None:
    """Every first-stage input becomes an InputFile.

    A handle typed twice in the same stage (`A=a.pdf A=b.pdf`) is not
    flagged: pdftl's own parser keeps only the last `A=` association, so
    the earlier file is treated here as unnamed and gets a fresh handle.
    """
    rev = _reverse_handles(stage.handles)
    reserved = set(rev.values())
    for idx, filename in enumerate(stage.inputs):
        handle_name = rev.get(idx)
        password = _password_at(stage, idx)
        if handle_name is None:
            handle_name = _free_handle(by_handle, reserved)
        entry = InputFile(handle_name, Path(filename), password)
        pipeline_inputs.append(entry)
        by_handle[handle_name] = entry


def _later_stage_inputs(
    stage: CliStage, pipeline_inputs: list, by_handle: dict, i: int
) -> tuple[str, ...]:
    rev = _reverse_handles(stage.handles)
    reserved = set(rev.values())
    extra: list[str] = []
    for idx, filename in enumerate(stage.inputs):
        if filename == "_":
            continue
        handle_name = rev.get(idx)
        password = _password_at(stage, idx)
        path = Path(filename)
        if handle_name is None:
            handle_name = _free_handle(by_handle, reserved)
            entry = InputFile(handle_name, path, password)
            pipeline_inputs.append(entry)
            by_handle[handle_name] = entry
        else:
            existing = by_handle.get(handle_name)
            if existing is None:
                entry = InputFile(handle_name, path, password)
                pipeline_inputs.append(entry)
                by_handle[handle_name] = entry
            elif not _same_path(existing.path, path):
                raise ValueError(
                    f"stage {i + 1}: handle {handle_name} already refers to "
                    f"{existing.path}; cannot reassign it to {path} "
                    "(renaming handles is not supported)"
                )
        extra.append(handle_name)
    return tuple(extra)


def _process_stage(
    span: list, i: int, is_last: bool, pipeline_inputs: list, by_handle: dict, style: str
) -> tuple[Stage, Path | None, dict]:
    try:
        stage_args_core, stage_options = parse_options_and_specs(span)
        _reject_non_str(stage_args_core, i)
        operation, _pre, _post = _find_operation_and_split(stage_args_core)
        if not stage_args_core or operation is None:
            raise ValueError(f"stage {i + 1} has no operation; type an explicit operation")
        stage = parse_cli_stage(stage_args_core, is_first_stage=(i == 0))
        stage.options.update(stage_options)
    except UserCommandLineError as exc:
        raise ValueError(str(exc)) from exc
    _validate_inputs(stage, i)
    if i == 0:
        _first_stage_inputs(stage, pipeline_inputs, by_handle)
        extra: tuple[str, ...] = ()
    else:
        extra = _later_stage_inputs(stage, pipeline_inputs, by_handle, i)
    output = None
    if c.OUTPUT in stage.options:
        if not is_last:
            raise ValueError(f"stage {i + 1} has 'output'; only the last stage may write output")
        output = Path(stage.options[c.OUTPUT])
    if is_last:
        # Everything but 'output' is a save/output option: the Output box's job,
        # not this (last) stage's arguments.
        leftover = {k: v for k, v in stage.options.items() if k != c.OUTPUT}
        args_text = shell_style.join(list(stage.operation_args), style)
    else:
        leftover = {}
        args_text = shell_style.join([*stage.operation_args, *option_tokens(stage.options)], style)
    return Stage(op=stage.operation, args_text=args_text, inputs=extra), output, leftover


def parse_command(text: str, *, style: str | None = None) -> ParsedCommand:
    """Parse a full pdftl command line into a GUI `Pipeline`.

    `style` selects the shell quoting rules used to split `text`: `posix`,
    `windows`, or None (default) for the platform's own style
    (`shell_style.default_style()`). See `parse_tokens` for the errors this
    (and that) can raise.
    """
    if style is None:
        style = shell_style.default_style()
    if style not in ("posix", "windows"):
        raise ValueError(f"unknown style {style!r}")
    tokens = _drop_program_token(shell_style.split(text, style))
    return parse_tokens(tokens, style=style)


def parse_tokens(tokens: list[str], *, style: str | None = None) -> ParsedCommand:
    """Parse already-split arguments (e.g. loaded from a `--args` YAML file) into a
    GUI `Pipeline`. Tokens are taken literally, one argument each: this is not a
    shell command line and must not be quoted or escaped.

    `style` selects the shell quoting rules used to re-render each stage's
    arguments text: `posix`, `windows`, or None (default) for the platform's
    own style. Raises ValueError for anything the GUI pipeline model cannot
    represent: an empty command, stdin (`-`), `PROMPT` as a filename, inline
    sub-pipelines or EACH blocks, a stage without an explicit operation, an
    undeclared or conflicting input handle, or `output` outside the last stage.
    """
    if style is None:
        style = shell_style.default_style()
    if style not in ("posix", "windows"):
        raise ValueError(f"unknown style {style!r}")
    initialize_registry()
    tokens = list(tokens)
    if not tokens:
        raise ValueError("empty command")
    spans = split_args_by_separator(tokens)
    pipeline_inputs: list[InputFile] = []
    by_handle: dict[str, InputFile] = {}
    stages: list[Stage] = []
    output: Path | None = None
    output_options: dict = {}
    last = len(spans) - 1
    for i, span in enumerate(spans):
        stage, stage_output, leftover = _process_stage(
            span, i, i == last, pipeline_inputs, by_handle, style
        )
        stages.append(stage)
        if stage_output is not None:
            output = stage_output
        if leftover:
            output_options = leftover
    pipeline = Pipeline(
        inputs=tuple(pipeline_inputs),
        stages=tuple(stages),
        output_options=shell_style.join(option_tokens(output_options), style),
    )
    return ParsedCommand(pipeline, output)


class CommandBar(QLineEdit):
    """A `QLineEdit` that turns a typed pdftl command into a `Pipeline`.

    Enter parses the current text: `submitted` carries the result, `failed`
    an error message. Up/Down cycle through previously submitted commands.
    Esc restores the text last set by `show_pipeline`. Leaving the bar with
    an unapplied edit does the same, keeps the edit in the history and
    emits `discarded`; a menu or another window taking focus keeps it.
    """

    submitted = Signal(object)
    failed = Signal(str)
    discarded = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setPlaceholderText("Type a pdftl command and press Enter")
        self._history: list[str] = []
        self._history_pos: int | None = None
        self._draft = ""
        self._shown_text: str | None = None
        self._base_palette = QPalette(self.palette())
        self.textChanged.connect(self._show_dirty)

    def is_dirty(self) -> bool:
        """Whether the text differs from the pipeline last shown."""
        return self._shown_text is not None and self.text() != self._shown_text

    def _show_dirty(self, _text: str = "") -> None:
        palette = QPalette(self._base_palette)
        if self.is_dirty():
            base = palette.base().color()
            tint = blend(palette.highlight().color(), base, 0.15)
            palette.setColor(QPalette.ColorRole.Base, tint)
        self.setPalette(palette)

    def focusOutEvent(self, event) -> None:
        keep = (Qt.FocusReason.PopupFocusReason, Qt.FocusReason.ActiveWindowFocusReason)
        if self.is_dirty() and event.reason() not in keep:
            draft = self.text()
            if not self._history or self._history[-1] != draft:
                self._history.append(draft)
            self._history_pos = None
            self._restore_shown()
            self.discarded.emit(draft)
        super().focusOutEvent(event)

    def show_pipeline(self, p: Pipeline, output: Path | None) -> None:
        """Show `p` (and `output`) as a command line, without emitting `submitted`."""
        text = to_cli(p, output, mask_passwords=True)
        self._shown_text = text
        self.setText(text)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._submit()
        elif key == Qt.Key.Key_Up:
            self._history_prev()
        elif key == Qt.Key.Key_Down:
            self._history_next()
        elif key == Qt.Key.Key_Escape:
            self._restore_shown()
        else:
            super().keyPressEvent(event)

    def _submit(self) -> None:
        text = self.text()
        try:
            parsed = parse_command(text)
        except ValueError as exc:
            self.failed.emit(str(exc))
            return
        self._history_pos = None
        self._history.append(text)
        self.submitted.emit(parsed)

    def _history_prev(self) -> None:
        if not self._history:
            return
        if self._history_pos is None:
            self._draft = self.text()
            self._history_pos = len(self._history) - 1
        elif self._history_pos > 0:
            self._history_pos -= 1
        self.setText(self._history[self._history_pos])

    def _history_next(self) -> None:
        if self._history_pos is None:
            return
        if self._history_pos < len(self._history) - 1:
            self._history_pos += 1
            self.setText(self._history[self._history_pos])
        else:
            self._history_pos = None
            self.setText(self._draft)

    def _restore_shown(self) -> None:
        if self._shown_text is not None:
            self.setText(self._shown_text)
