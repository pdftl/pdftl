# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/window_stages.py

"""The main window's stage boxes: adding, moving, deleting and focusing them."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtWidgets import QApplication

from pdftl.gui.pagesel import insert_into_args
from pdftl.gui.widgets import StageBox, StripBox

REVEAL_MARGIN = 6


class StagesMixin:
    """Stage boxes and which one is current."""

    def add_stage(self) -> None:
        self.insert_stage(self.current_index() + 1 if self.boxes else 0)

    def insert_stage_before(self) -> None:
        if self.current in self.boxes:
            self.insert_stage(self.current_index())

    def insert_stage(self, at: int) -> None:
        box = self._new_box(at)
        box.op.setFocus()
        self._reveal(box)
        self.schedule()

    def _insert_next_to(self, box: StageBox, offset: int) -> None:
        self.insert_stage(self.boxes.index(box) + offset)

    def _new_box(self, at: int) -> StageBox:
        box = StageBox(self.ops, self.thumbs)
        self._connect_box(box)
        box.edited.connect(lambda: self.schedule(QApplication.focusWidget()))
        box.op_changed.connect(lambda op: self.show_help(op.strip()))
        box.insert_requested.connect(self._insert_next_to)
        box.delete_requested.connect(self.delete_box)
        box.drag_started.connect(self.gestures.start_drag)
        for field in (box.op, box.op.lineEdit(), box.args, box.extra):
            field.installEventFilter(self._undo_keys)
        self.boxes.insert(at, box)
        self.stages_layout.insertWidget(at + 1, box)
        self._retitle()
        return box

    def delete_stage(self) -> None:
        if self.current in self.boxes:
            self.delete_box(self.current)

    def delete_box(self, box: StageBox) -> None:
        i = self.boxes.index(box)
        self._discard_box(self.boxes.pop(i))
        self._retitle()
        if self.boxes:
            self.boxes[min(i, len(self.boxes) - 1)].op.setFocus()
        self.schedule()
        self.statusBar().showMessage(f"Deleted stage {i + 1}; Ctrl+Z undoes", 5000)

    def _discard_box(self, box: StageBox) -> None:
        if box.key is not None:
            self.thumbs.cancel(box.key)
        self.followed.pop(box, None)
        if box is self.current:
            self._set_current(None)
        box.hide()
        self.stages_layout.removeWidget(box)
        box.deleteLater()

    def move_stage(self, delta: int) -> None:
        if self.current not in self.boxes:
            return
        target = max(0, min(self.current_index() + delta, len(self.boxes) - 1))
        self.move_box_to(self.current, target)

    def move_box_to(self, box: StageBox, target: int, reveal: bool = True) -> None:
        i = self.boxes.index(box)
        if target == i:
            return
        self.boxes.insert(target, self.boxes.pop(i))
        self.stages_layout.removeWidget(box)
        self.stages_layout.insertWidget(target + 1, box)
        self._retitle()
        if reveal:
            self._reveal(box)
        else:
            box.focus_target().setFocus(Qt.FocusReason.MouseFocusReason)
        self.schedule()
        self.statusBar().showMessage(f"Moved to stage {target + 1}; Ctrl+Z undoes", 5000)

    def _reveal(self, box: StageBox) -> None:
        """Scroll `box`'s top towards the viewport's top, once the layout has placed it."""
        QTimer.singleShot(0, lambda: self._apply_reveal(box))

    def _apply_reveal(self, box: StageBox) -> None:
        if box not in self.boxes:
            return
        QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        bar = self.scroll.verticalScrollBar()
        top = box.mapTo(self.stages_widget, QPoint(0, 0)).y() - REVEAL_MARGIN
        bar.setValue(top)
        self.scroll.horizontalScrollBar().setValue(0)

    def _retitle(self) -> None:
        for i, box in enumerate(self.boxes, 1):
            box.set_title(f"<b>Stage {i}</b>")
            box.extra.setVisible(i > 1)
        self._update_actions()
        anchor = self.boxes[-1].args if self.boxes else self.inputs_box.strip
        self.setTabOrder(anchor, self.output_box.options)
        self._sync_output_follow()

    def _focused_box(self) -> StripBox | None:
        focus = QApplication.focusWidget()
        for box in (self.inputs_box, *self.boxes):
            if focus is not None and box.isAncestorOf(focus):
                return box
        return None

    def current_index(self) -> int:
        """The current stage's index; with no current stage, the last one's."""
        return (
            self.boxes.index(self.current) if self.current in self.boxes else len(self.boxes) - 1
        )

    def _set_current(self, box: StripBox | None) -> None:
        if box is not None:
            self.output_box.set_current(False)
        if box is self.current:
            return
        if self.current is not None:
            self.current.set_current(False)
        self.current = box
        if box is not None:
            box.set_current(True)
        self._update_actions()

    def step(self, delta: int) -> None:
        if not self.boxes:
            return
        box = self.current
        i = self.boxes.index(box) if box in self.boxes else -1
        target = self.boxes[max(0, min(i + delta, len(self.boxes) - 1))]
        target.strip.setFocus()
        self._reveal(target)

    def _focus_part(self, part: str) -> None:
        if self.current in self.boxes:
            box = self.boxes[self.current_index()]
            getattr(box, part).setFocus()
            self._reveal(box)

    def focus_output_box(self) -> None:
        self.output_box.options.setFocus()
        QTimer.singleShot(0, self._reveal_output)

    def _reveal_output(self) -> None:
        QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
        bar = self.scroll.verticalScrollBar()
        top = self.output_box.mapTo(self.stages_widget, QPoint(0, 0)).y() - REVEAL_MARGIN
        bar.setValue(top)
        self.scroll.horizontalScrollBar().setValue(0)

    def insert_selection(self) -> None:
        box = self.current
        if box is None or not (spec := box.selection_spec()):
            self.statusBar().showMessage("Select pages in a strip first", 4000)
            return
        i = 0 if box is self.inputs_box else self.boxes.index(box) + 1
        if i == len(self.boxes):
            self._new_box(i)
        target = self.boxes[i].args
        text, pos = insert_into_args(target.text(), target.cursorPosition(), spec)
        target.setText(text)
        target.setCursorPosition(pos)
        target.setFocus()
        self.schedule()
