# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/gui/test_history.py

from pathlib import Path

import pytest

from pdftl.gui.history import History, Snapshot, describe
from pdftl.gui.interfaces import InputFile, Pipeline, Stage

A = InputFile("A", Path("a.pdf"))
B = InputFile("B", Path("b.pdf"))


def snap(*stages, inputs=(A,), output=None, output_options=""):
    return Snapshot(Pipeline(tuple(inputs), tuple(stages), output_options), output)


CAT, ROT, DEL = Stage("cat", "1-2"), Stage("rotate", "1east"), Stage("delete", "3")


@pytest.mark.parametrize(
    ("old", "new", "label"),
    [
        (snap(CAT), snap(CAT, ROT), "add stage 2"),
        (snap(CAT, ROT), snap(DEL, CAT, ROT), "add stage 1"),
        (snap(CAT, ROT, DEL), snap(CAT, DEL), "delete stage 2"),
        (snap(CAT, ROT), snap(CAT), "delete stage 2"),
        (snap(CAT, ROT), snap(CAT, Stage("rotate", "1west")), "edit stage 2 arguments"),
        (snap(CAT), snap(Stage("shuffle", "1-2")), "edit stage 1 operation"),
        (snap(CAT, ROT), snap(CAT, Stage("rotate", "1east", ("B",))), "edit stage 2 extra inputs"),
        (snap(CAT, ROT, DEL), snap(ROT, CAT, DEL), "move stage"),
        (snap(CAT, ROT), snap(DEL, Stage("x")), "change stages"),
        (snap(CAT), snap(ROT, DEL), "change stages"),
        (snap(CAT, ROT), snap(DEL), "change stages"),
        (snap(CAT), snap(CAT, inputs=(A, B)), "add input"),
        (snap(CAT, inputs=(A, B)), snap(CAT), "remove input"),
        (snap(CAT), snap(CAT, inputs=(B,)), "change inputs"),
        (snap(CAT), snap(ROT, inputs=(B,)), "apply command"),
        (snap(CAT), snap(CAT, output=Path("o.pdf")), "change output"),
        (snap(CAT), snap(CAT, output_options="flatten"), "change output"),
    ],
)
def test_describe_names_the_change(old, new, label):
    assert describe(old, new) == label


def test_record_undo_redo_round_trip():
    h = History(snap(CAT))
    assert not h.can_undo() and not h.can_redo() and h.undo_label() == ""
    assert h.record(snap(CAT, ROT))
    assert h.record(snap(ROT))
    assert h.undo_label() == "delete stage 1"
    assert h.undo() == snap(CAT, ROT)
    assert h.redo_label() == "delete stage 1"
    assert h.undo() == snap(CAT)
    assert not h.can_undo()
    assert h.redo() == snap(CAT, ROT)
    assert h.redo() == snap(ROT)
    assert not h.can_redo() and h.redo_label() == ""


def test_unchanged_state_is_not_recorded():
    h = History(snap(CAT))
    assert not h.record(snap(CAT))
    assert not h.can_undo()


def test_new_record_clears_redo():
    h = History(snap(CAT))
    h.record(snap(CAT, ROT))
    h.undo()
    h.record(snap(DEL))
    assert not h.can_redo()
    assert h.undo() == snap(CAT)


def test_typing_in_one_field_is_one_step():
    h = History(snap(Stage("cat", "")))
    for text in ("1", "1-", "1-2"):
        h.record(snap(Stage("cat", text)), merge_key="args")
    assert h.undo_label() == "edit stage 1 arguments"
    assert h.undo() == snap(Stage("cat", ""))
    assert not h.can_undo()


def test_another_field_or_a_break_starts_a_new_step():
    h = History(snap(Stage("cat", "")))
    h.record(snap(Stage("cat", "1")), merge_key="args")
    h.record(snap(Stage("rotate", "1")), merge_key="op")
    h.break_merge()
    h.record(snap(Stage("rotate", "1east")), merge_key="op")
    assert h.undo() == snap(Stage("rotate", "1"))
    assert h.undo() == snap(Stage("cat", "1"))
    assert h.undo() == snap(Stage("cat", ""))


def test_typing_back_to_the_start_drops_the_step_and_the_merge():
    h = History(snap(CAT))
    h.record(snap(CAT, ROT))
    h.record(snap(CAT, Stage("rotate", "1eas")), merge_key="args")
    h.record(snap(CAT, ROT), merge_key="args")
    assert h.state == snap(CAT, ROT) and h.undo_label() == "add stage 2"
    h.record(snap(CAT, Stage("rotate", "1")), merge_key="args")
    assert h.undo() == snap(CAT, ROT)
    assert h.undo() == snap(CAT)


def test_undo_after_merging_starts_fresh():
    h = History(snap(Stage("cat", "")))
    h.record(snap(Stage("cat", "1")), merge_key="args")
    h.undo()
    h.record(snap(Stage("cat", "2")), merge_key="args")
    assert h.undo() == snap(Stage("cat", ""))


def test_history_is_capped_keeping_the_newest_steps():
    h = History(snap(Stage("cat", "0")), limit=3)
    for i in range(1, 6):
        h.record(snap(Stage("cat", str(i))))
    states = []
    while h.can_undo():
        states.append(h.undo().pipeline.stages[0].args_text)
    assert states == ["4", "3", "2"]


def test_undo_and_redo_at_the_ends_raise():
    h = History(snap(CAT))
    with pytest.raises(IndexError):
        h.undo()
    with pytest.raises(IndexError):
        h.redo()
