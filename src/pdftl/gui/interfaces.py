# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/interfaces.py

"""Shared types and contracts between the GUI modules.

Qt-free. Contracts for Qt objects are Protocols listing the signals and
methods they must expose.

Page numbers are 1-based everywhere.
"""

from __future__ import annotations

import enum
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pdftl.gui import shell_style

PREVIOUS = "_"
"""CLI input token for the previous stage's output."""


def plural(n: int, noun: str) -> str:
    """`f"{n} {noun}"`, pluralised: `"1 page"`, `"2 pages"`, `"0 pages"`."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


class OpKind(enum.Enum):
    """How an operation behaves inside a GUI pipeline."""

    PDF = "pdf"
    """Produces a PDF; its pages are previewed."""

    DATA = "data"
    """Produces text or data (`skip_pipeline_save`); shown as text."""

    SIDE_EFFECT = "side_effect"
    """Writes files of its own; allowed only as the final stage."""

    BLOCKED = "blocked"
    """Never offered: `server`, `gui`, interactive-only operations."""


class ResultKind(enum.Enum):
    PDF = "pdf"
    TEXT = "text"
    ERROR = "error"


@dataclass(frozen=True)
class InputFile:
    handle: str
    """Single capital letter, `A` to `Z`."""

    path: Path
    password: str | None = None


@dataclass(frozen=True)
class Stage:
    op: str
    args_text: str = ""
    """Arguments as typed, split with the platform's shell rules."""

    inputs: tuple[str, ...] = ()
    """Handles of input files this stage also reads.

    Ignored on the first stage, which reads every input file. On later
    stages the previous output comes first and these follow; the CLI scopes
    handles to a stage, so each is declared again there.
    """

    def tokens(self) -> list[str]:
        """Raises ValueError on unbalanced quotes or a `---` stage separator.

        Split with `shell_style.default_style()`: POSIX rules, or Windows
        `CommandLineToArgvW` rules when running on Windows.
        """
        tokens = shell_style.split(self.args_text, shell_style.default_style())
        if "---" in tokens:
            raise ValueError("'---' separates stages; add a new stage instead")
        return tokens


def declare_inputs(files) -> list[str]:
    """`H=path` per file, then `input_pw` with any passwords."""
    argv = [f"{f.handle}={f.path}" for f in files]
    passwords = [f"{f.handle}={f.password}" for f in files if f.password is not None]
    return [*argv, "input_pw", *passwords] if passwords else argv


@dataclass(frozen=True)
class Pipeline:
    inputs: tuple[InputFile, ...] = ()
    stages: tuple[Stage, ...] = ()

    output_options: str = ""
    """Save/output option tokens (`flatten`, `uncompress`, `owner_pw <pw>`, ...),
    as typed in the GUI's Output box. Applied after the final stage, exactly as
    typing them there on the CLI would; split with the platform's shell rules,
    like `Stage.args_text`."""

    def output_tokens(self) -> list[str]:
        """Raises ValueError on unbalanced quotes or a `---` stage separator."""
        tokens = shell_style.split(self.output_options, shell_style.default_style())
        if "---" in tokens:
            raise ValueError("'---' separates stages; output options only follow the last one")
        return tokens

    def to_argv(self, output: Path | None = None) -> list[str]:
        """CLI arguments for this pipeline, without the program name.

        The first stage's `inputs` are ignored: the input files are its
        inputs, unless it is a source operation such as `create`, which reads
        none. Raises ValueError on unbalanced quotes or an undefined handle.
        """
        from pdftl.gui.op_policy import is_source

        by_handle = {f.handle: f for f in self.inputs}
        source = bool(self.stages) and is_source(self.stages[0].op)
        argv = [] if source else declare_inputs(self.inputs)
        for i, stage in enumerate(self.stages):
            if i:
                missing = [h for h in stage.inputs if h not in by_handle]
                if missing:
                    raise ValueError(f"stage {i + 1} uses undefined handles {missing}")
                argv += ["---", *declare_inputs([by_handle[h] for h in stage.inputs])]
            argv += [stage.op, *stage.tokens()]
        if output is not None:
            argv += ["output", str(output)]
        argv += self.output_tokens()
        return argv


@dataclass(frozen=True)
class StageResult:
    index: int
    """Stage position; -1 for an input file's own preview."""

    kind: ResultKind
    key: str
    """Content hash identifying this result; thumbnails are cached by it."""

    pdf_path: Path | None = None
    """The PDF this stage hands to the next one. Owned by the engine; read-only.

    Set for PDF results, and for TEXT results whose op passes its input
    through unchanged (as the CLI does for a data op mid-pipeline).
    """

    page_count: int = 0
    text: str = ""
    """What the stage printed to stdout, for any kind; the GUI's console shows it."""

    stderr: str = ""
    """What the stage printed to stderr (warnings), for any kind."""

    error: str = ""
    cached: bool = False
    needs_password: bool = False
    """ERROR because the file is encrypted and the password is missing or wrong."""

    password_path: Path | None = None
    """Which input file `needs_password` refers to, when it isn't the one previewed."""

    password_kind: str | None = None
    """Which credential opened the file: `"owner"`, `"user"`, or None if unencrypted."""


@dataclass(frozen=True)
class OpInfo:
    name: str
    kind: OpKind
    desc: str = ""
    usage: str = ""
    long_desc: str = ""
    examples: tuple[tuple[str, str], ...] = ()
    """(command, description) pairs."""

    reason: str = ""
    """Why the operation got this kind; shown for BLOCKED/SIDE_EFFECT."""


class Engine(Protocol):
    """Runs pipelines stage by stage, caching each stage's output.

    Stage i's cache key hashes stage i-1's key, the stage's op, tokens and
    inputs, and each referenced input file's path, size and mtime. A run
    after an edit therefore reuses every result before the edited stage.

    Stages run in a worker process, so a crash or hang never takes down
    the GUI.
    """

    def run(self, pipeline: Pipeline, cancel: threading.Event) -> Iterator[StageResult]:
        """Yield one result per stage, in order.

        Stops after the first ERROR result, or as soon as `cancel` is set
        (killing any stage in progress). Blocking; call off the GUI thread.
        """
        ...

    def preview_input(self, item: InputFile) -> StageResult:
        """Result for an input file itself (index -1), for its page strip."""
        ...

    def save(self, pipeline: Pipeline, output: Path, cancel: threading.Event) -> StageResult:
        """Run the whole pipeline in one go, exactly as `to_argv` would.

        Guarantees the saved file matches the CLI's output for the command
        shown by Copy CLI. Blocking.
        """
        ...

    def close(self) -> None:
        """Stop the worker and delete cached files."""
        ...


class ThumbProvider(Protocol):
    """Renders page thumbnails off the GUI thread.

    Qt signal: `thumb_ready(key: str, page: int, image: QImage)`.
    """

    thumb_ready: Any

    def request(self, key: str, pdf_path: Path, page: int, width_px: int) -> None:
        """Queue a render; most recent requests are served first."""
        ...

    def cancel(self, key: str) -> None:
        """Drop queued requests for `key`."""
        ...

    def close(self) -> None: ...


class PageStripView(Protocol):
    """Horizontal, keyboard-navigable, multi-select strip of page thumbnails.

    Qt signals:
      `selection_changed(pages: list[int])`, sorted.
      `page_activated(page: int)`, on Enter or double-click.
    """

    selection_changed: Any
    page_activated: Any

    def set_source(
        self, key: str, pdf_path: Path | None, page_count: int, provider: ThumbProvider
    ) -> None:
        """Show the pages of a result. `pdf_path` None clears the strip."""
        ...

    def selected_pages(self) -> list[int]: ...

    def select_pages(self, pages: Sequence[int]) -> None: ...
