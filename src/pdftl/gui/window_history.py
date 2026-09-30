# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/window_history.py

"""The main window's undo and redo."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QKeySequence

from pdftl.gui import keymap
from pdftl.gui.history import Snapshot
from pdftl.gui.interfaces import Pipeline


class UndoKeysToWindow(QObject):
    """Lets the window's Undo and Redo win over a stage field's own text undo."""

    def eventFilter(self, _watched, event) -> bool:
        if event.type() != QEvent.Type.ShortcutOverride:
            return False
        pressed = QKeySequence(event.keyCombination())
        return any(
            pressed == QKeySequence(key)
            for action_id in ("undo", "redo")
            for key in keymap.keys_for(action_id)
        )


class HistoryMixin:
    """Undo and redo, and restoring a snapshot into the boxes."""

    def _snapshot(self) -> Snapshot:
        return Snapshot(self.current_pipeline(), self.save_target)

    def _record(self, merge_key: object | None = None) -> None:
        if not self._restoring:
            self.history.record(self._snapshot(), merge_key)
            self._update_undo_actions()

    def _update_undo_actions(self) -> None:
        for action_id, can, label in (
            ("undo", self.history.can_undo(), self.history.undo_label()),
            ("redo", self.history.can_redo(), self.history.redo_label()),
        ):
            action = self.actions_by_id[action_id]
            action.setEnabled(can)
            action.setText(f"&{action_id.title()} {label}".rstrip())

    def undo(self) -> None:
        if self.history.can_undo():
            label = self.history.undo_label()
            state = self.history.undo()
            self._sync_declined(state.pipeline.inputs)
            self._restore(state, focus=True)
            self.statusBar().showMessage(f"Undid {label}", 3000)

    def redo(self) -> None:
        if self.history.can_redo():
            label = self.history.redo_label()
            state = self.history.redo()
            self._sync_declined(state.pipeline.inputs)
            self._restore(state, focus=True)
            self.statusBar().showMessage(f"Redid {label}", 3000)

    def _sync_declined(self, inputs) -> None:
        """Stepping through history to a password-less state must not re-prompt for it."""
        for f in inputs:
            (self._declined.discard if f.password else self._declined.add)(Path(f.path))

    def _restore(self, state: Snapshot, focus: bool = False) -> None:
        """Show `state`, rebuilding only the stages that differ from the current ones."""
        self._restoring = True
        try:
            if state.pipeline.inputs != self.pipeline.inputs:
                self._set_inputs(state.pipeline.inputs)
            changed = self._set_stages(state.pipeline.stages)
            self.output_box.set_options_text(state.pipeline.output_options)
            self.output_box.set_error("")
            self.save_target = state.output
            self.output_box.set_target(self.save_target)
        finally:
            self._restoring = False
        self._update_undo_actions()
        if focus:
            self._focus_change(changed)
        self.schedule()

    def _set_inputs(self, inputs) -> None:
        self.pipeline = Pipeline(tuple(inputs), ())
        self.input_list.clear()
        for f in inputs:
            self.input_list.addItem(f"{f.handle}  {f.path}")
        if inputs:
            self.input_list.setCurrentRow(0)
        else:
            self.inputs_box.show_pdf(None, status="No input files")

    def _set_stages(self, stages) -> int | None:
        """Keep the common prefix and suffix boxes; returns the first changed index."""
        old = [b.stage() for b in self.boxes]
        new = list(stages)
        head = 0
        while head < min(len(old), len(new)) and old[head] == new[head]:
            head += 1
        tail = 0
        while tail < min(len(old), len(new)) - head and old[-1 - tail] == new[-1 - tail]:
            tail += 1
        reused = min(len(old), len(new)) - head - tail
        for i in range(head, head + reused):
            self.boxes[i].set_stage(new[i])
        extra = slice(head + reused, len(old) - tail)
        for box in self.boxes[extra]:
            self._discard_box(box)
        del self.boxes[extra]
        for i in range(head + reused, len(new) - tail):
            self._new_box(i).set_stage(new[i])
        self._retitle()
        return head if old != new else None

    def _focus_change(self, index: int | None) -> None:
        if index is None:
            return
        if index < len(self.boxes):
            box = self.boxes[index]
            box.args.setFocus()
            self._reveal(box)
        elif self.boxes:
            self.boxes[-1].strip.setFocus()
            self._reveal(self.boxes[-1])
        else:
            self.inputs_box.strip.setFocus()
