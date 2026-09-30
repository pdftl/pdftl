# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/history.py

"""Undo/redo over whole editor states. Qt-free."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from pdftl.gui.interfaces import Pipeline, Stage

MAX_STEPS = 500

_FIELDS = (("op", "operation"), ("args_text", "arguments"), ("inputs", "extra inputs"))


@dataclass(frozen=True)
class Snapshot:
    pipeline: Pipeline
    output: Path | None = None


def _edited_field(old: Stage, new: Stage) -> str:
    return next(label for name, label in _FIELDS if getattr(old, name) != getattr(new, name))


def _describe_stages(a: tuple[Stage, ...], b: tuple[Stage, ...]) -> str:
    changed = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    first = changed[0] if changed else min(len(a), len(b))
    if b[:first] + b[first + 1 :] == a:
        return f"add stage {first + 1}"
    if a[:first] + a[first + 1 :] == b:
        return f"delete stage {first + 1}"
    if len(a) == len(b) and len(changed) == 1:
        return f"edit stage {first + 1} {_edited_field(a[first], b[first])}"
    if len(a) == len(b) and Counter(a) == Counter(b):
        return "move stage"
    return "change stages"


def describe(old: Snapshot, new: Snapshot) -> str:
    """A short name for the change from `old` to `new`, for "Undo <name>"."""
    a, b = old.pipeline, new.pipeline
    if a.inputs != b.inputs and a.stages != b.stages:
        return "apply command"
    if a.inputs != b.inputs:
        if len(b.inputs) > len(a.inputs):
            return "add input"
        return "remove input" if len(b.inputs) < len(a.inputs) else "change inputs"
    if a.stages != b.stages:
        return _describe_stages(a.stages, b.stages)
    return "change output"


class History:
    """A stack of snapshots; consecutive records with one merge key are one step."""

    def __init__(self, initial: Snapshot, limit: int = MAX_STEPS) -> None:
        self._undo: list[tuple[Snapshot, str]] = [(initial, "")]
        self._redo: list[tuple[Snapshot, str]] = []
        self._merge_key: object | None = None
        self.limit = limit

    @property
    def state(self) -> Snapshot:
        return self._undo[-1][0]

    def record(self, state: Snapshot, merge_key: object | None = None) -> bool:
        """Push `state` unless unchanged; returns whether history changed."""
        if state == self.state:
            return False
        self._redo.clear()
        if merge_key is not None and merge_key == self._merge_key:
            label = self._undo.pop()[1]
            if state == self.state:
                self._merge_key = None
                return True
            self._undo.append((state, label))
        else:
            self._undo.append((state, describe(self.state, state)))
            del self._undo[: max(0, len(self._undo) - self.limit - 1)]
        self._merge_key = merge_key
        return True

    def break_merge(self) -> None:
        """Make the next record a new step even with the same merge key."""
        self._merge_key = None

    def can_undo(self) -> bool:
        return len(self._undo) > 1

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_label(self) -> str:
        return self._undo[-1][1] if self.can_undo() else ""

    def redo_label(self) -> str:
        return self._redo[-1][1] if self._redo else ""

    def undo(self) -> Snapshot:
        """Step back; the caller shows the returned state. Raises IndexError at the start."""
        if not self.can_undo():
            raise IndexError("nothing to undo")
        self._redo.append(self._undo.pop())
        self._merge_key = None
        return self.state

    def redo(self) -> Snapshot:
        """Step forward again. Raises IndexError when there is nothing to redo."""
        self._undo.append(self._redo.pop())
        self._merge_key = None
        return self.state
