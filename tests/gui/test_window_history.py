"""Tests for window_history: undo and redo."""

import shlex

import pytest

pytest.importorskip("PySide6")


from pdftl.gui.command_bar import parse_command
from pdftl.gui.interfaces import Stage
from tests.gui.window_support import (
    _focus_stage,
    _focus_widget_is,
    _make_pdf,
    fire,
    press,
    replace_selection,
    type_text,
)


def test_undo_redo_start_disabled(make_window, two_pages):
    window = make_window([str(two_pages)])
    undo, redo = window.actions_by_id["undo"], window.actions_by_id["redo"]
    assert not undo.isEnabled() and not redo.isEnabled()
    assert undo.text() == "&Undo" and redo.text() == "&Redo"
    window.undo()
    window.redo()
    assert len(window.boxes) == 1


def test_undo_restores_a_deleted_stage_and_redo_deletes_it_again(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.boxes[0].args.setText("2")
    fire(qtbot, "add_stage")
    replace_selection(qtbot, "rotate")
    fire(qtbot, "focus_args")
    type_text(qtbot, "1east")
    first = window.boxes[0]
    fire(qtbot, "delete_stage")
    assert [b.stage() for b in window.boxes] == [Stage("cat", "2")]
    assert window.actions_by_id["undo"].text() == "&Undo delete stage 2"
    fire(qtbot, "undo")
    assert [b.stage() for b in window.boxes] == [Stage("cat", "2"), Stage("rotate", "1east")]
    assert window.boxes[0] is first
    assert window.current is window.boxes[1]
    assert "Undid delete stage 2" in window.statusBar().currentMessage()
    assert window.actions_by_id["redo"].text() == "&Redo delete stage 2"
    fire(qtbot, "redo")
    assert [b.stage() for b in window.boxes] == [Stage("cat", "2")]
    assert "Redid delete stage 2" in window.statusBar().currentMessage()


def test_ctrl_z_in_a_field_undoes_the_whole_typing_session(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    fire(qtbot, "focus_args")
    type_text(qtbot, "1-2")
    fire(qtbot, "focus_op")
    replace_selection(qtbot, "rotate")
    fire(qtbot, "focus_args")
    type_text(qtbot, " x")
    assert window.boxes[0].stage() == Stage("rotate", "1-2 x")
    press(qtbot, "Ctrl+Z")
    assert window.boxes[0].stage() == Stage("rotate", "1-2")
    press(qtbot, "Ctrl+Z")
    assert window.boxes[0].stage() == Stage("cat", "1-2")
    press(qtbot, "Ctrl+Z")
    assert window.boxes[0].stage() == Stage("cat", "")
    press(qtbot, "Ctrl+Y")
    assert window.boxes[0].stage() == Stage("cat", "1-2")
    press(qtbot, "Ctrl+Shift+Z")
    assert window.boxes[0].stage() == Stage("rotate", "1-2")


def test_other_shortcuts_in_a_field_stay_native(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    fire(qtbot, "focus_args")
    type_text(qtbot, "abc")
    press(qtbot, "Ctrl+A")
    assert window.boxes[0].args.selectedText() == "abc"


def test_undo_of_edits_keeps_the_other_boxes(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    fire(qtbot, "add_stage")
    fire(qtbot, "add_stage")
    boxes = list(window.boxes)
    _focus_stage(qtbot, window, boxes[1])
    type_text(qtbot, "1")
    fire(qtbot, "undo")
    assert window.boxes == boxes
    assert window.boxes[1].args.text() == ""


def test_undo_of_move_and_inputs(qtbot, make_window, two_pages, tmp_path):
    other = _make_pdf(tmp_path / "other.pdf", [300])
    window = make_window([str(two_pages)])
    window.boxes[0].args.setText("1")
    fire(qtbot, "add_stage")
    replace_selection(qtbot, "rotate")
    fire(qtbot, "move_stage_up")
    assert window.actions_by_id["undo"].text() == "&Undo move stage"
    fire(qtbot, "undo")
    assert [b.op.currentText() for b in window.boxes] == ["cat", "rotate"]
    window.add_file(other)
    assert window.input_list.count() == 2
    fire(qtbot, "undo")
    assert window.input_list.count() == 1
    assert [f.path for f in window.pipeline.inputs] == [two_pages]
    fire(qtbot, "redo")
    assert window.input_list.count() == 2


def test_undo_of_removing_the_last_input(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.input_list.setCurrentRow(0)
    window.remove_current_input()
    assert window.pipeline.inputs == ()
    fire(qtbot, "undo")
    assert [f.path for f in window.pipeline.inputs] == [two_pages]
    fire(qtbot, "redo")
    assert window.pipeline.inputs == ()
    assert window.inputs_box.status.text() == "No input files"


def test_undo_of_an_applied_command(qtbot, make_window, two_pages, tmp_path):
    window = make_window([str(two_pages)])
    window.boxes[0].args.setText("2")
    window.schedule()
    out = tmp_path / "o.pdf"
    window.apply_command(
        parse_command(
            f"{shlex.quote(str(two_pages))} rotate 1east --- delete 1 "
            f"output {shlex.quote(str(out))}"
        )
    )
    assert window.save_target == out
    assert window.actions_by_id["undo"].text() == "&Undo change stages"
    fire(qtbot, "undo")
    assert [b.stage() for b in window.boxes] == [Stage("cat", "2")]
    assert window.save_target is None


def test_undo_of_adding_a_stage_focuses_the_last_remaining_one(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    fire(qtbot, "add_stage")
    fire(qtbot, "undo")
    assert len(window.boxes) == 1
    assert _focus_widget_is(window, window.boxes[0].strip)


def test_undo_back_to_no_stages_focuses_the_inputs(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    _focus_stage(qtbot, window, window.boxes[0])
    fire(qtbot, "delete_stage")
    fire(qtbot, "add_stage")
    fire(qtbot, "undo")
    assert window.boxes == []
    assert _focus_widget_is(window, window.inputs_box.strip)
