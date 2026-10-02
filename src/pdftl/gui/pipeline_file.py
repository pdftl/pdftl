# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/pipeline_file.py

"""Save and load a Pipeline as a hand-editable pdftl `--args` YAML file. Qt-free."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import yaml

from pdftl.cli.args_loader import load_yaml_args
from pdftl.exceptions import UserCommandLineError
from pdftl.gui.command_bar import ParsedCommand, parse_tokens
from pdftl.gui.interfaces import Pipeline
from pdftl.gui.stage_model import with_masked_passwords


class PipelineFileError(Exception):
    """A pipeline file could not be saved or loaded; the message is user-facing."""


def _absolutize(path: Path, cwd: Path) -> Path:
    return path if path.is_absolute() else cwd / path


def _with_absolute_inputs(p: Pipeline, cwd: Path) -> Pipeline:
    inputs = tuple(replace(f, path=_absolutize(Path(f.path), cwd)) for f in p.inputs)
    return replace(p, inputs=inputs)


def _split_stages(argv: list[str]) -> list[list[str]]:
    docs: list[list[str]] = [[]]
    for token in argv:
        if token == "---":  # nosec B105 # the stage separator
            docs.append([])
        else:
            docs[-1].append(token)
    return docs


def save(pipeline: Pipeline, path: Path, output: Path | None, cwd: Path) -> None:
    """Write `pipeline` as a `--args` YAML file at `path`.

    Input file paths are made absolute against `cwd`, so the file runs the
    same from any directory; arguments typed as free text are left as-is.
    Passwords are never written: each becomes PROMPT, pdftl's keyword for
    asking interactively. `output` becomes the final stage's `output` clause;
    with none, the saved pipeline runs every stage but writes no file (in the
    GUI, Save is what picks a target, the same as `stage_model.to_cli`).

    Raises OSError if `path` cannot be written.
    """
    resolved = with_masked_passwords(_with_absolute_inputs(pipeline, Path(cwd)))
    docs = _split_stages(resolved.to_argv(output))
    header = f"# run with: pdftl --args {Path(path).name}\n"
    path.write_text(header + yaml.safe_dump_all(docs, allow_unicode=True), encoding="utf-8")


def load(path: Path) -> ParsedCommand:
    """Load a `--args` YAML file into a GUI `Pipeline`.

    Raises PipelineFileError, with a message fit to show the user, for a
    missing or unreadable file, malformed YAML, or content the GUI pipeline
    model cannot represent (see `command_bar.parse_tokens`).
    """
    try:
        tokens = load_yaml_args(str(path))
    except UserCommandLineError as exc:
        raise PipelineFileError(str(exc)) from exc
    try:
        return parse_tokens(tokens)
    except ValueError as exc:
        raise PipelineFileError(str(exc)) from exc
