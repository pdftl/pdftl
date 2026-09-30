"""Tests for main_window: the pdftl GUI's top-level window.

The keyboard end-to-end test drives the window using only
`qtbot.keyClick`/`keyClicks`/`keySequence` on `QApplication.focusWidget()`,
never a direct method call for a user action, and never a mouse event.
Dialog stand-ins (`ask_save_path`, `ask_text`, `open_url`) are the only
things replaced directly, as the class docstring invites.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence

from pdftl.gui import keymap
from pdftl.gui.interfaces import ResultKind, StageResult
from tests.gui.window_support import (
    _focus_widget_is,
    _make_pdf,
    _pages,
    fire,
    press,
    replace_selection,
    type_text,
)


def test_keyboard_only_rotate_cat_save(qtbot, make_window, tmp_path, two_pages, run_pdftl):
    window = make_window([str(two_pages)])
    assert len(window.boxes) == 1
    stage1 = window.boxes[0]

    fire(qtbot, "focus_op")
    assert _focus_widget_is(window, stage1.op)
    replace_selection(qtbot, "rotate")
    assert stage1.op.currentText() == "rotate"

    fire(qtbot, "focus_args")
    assert _focus_widget_is(window, stage1.args)
    replace_selection(qtbot, "1-2east")

    qtbot.waitUntil(lambda: "page" in stage1.status.text(), timeout=15000)
    assert "✗" not in stage1.status.text()

    fire(qtbot, "focus_pages")
    assert _focus_widget_is(window, stage1.strip)
    qtbot.keyClick(stage1.strip, Qt.Key.Key_Home)
    qtbot.keyClick(stage1.strip, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert stage1.selection_spec() == "1-end"

    fire(qtbot, "insert_selection")
    assert len(window.boxes) == 2
    stage2 = window.boxes[1]
    assert _focus_widget_is(window, stage2.args)
    assert stage2.args.text() == "1-end"

    fire(qtbot, "focus_op")
    assert _focus_widget_is(window, stage2.op)
    replace_selection(qtbot, "cat")
    assert stage2.op.currentText() == "cat"

    qtbot.waitUntil(lambda: "page" in stage2.status.text(), timeout=15000)
    assert "✗" not in stage2.status.text()

    saved = tmp_path / "saved.pdf"
    window.ask_save_path = lambda *a, **k: str(saved)
    fire(qtbot, "save")
    qtbot.waitUntil(lambda: "Saved" in window.statusBar().currentMessage(), timeout=30000)
    assert saved.exists()

    expected = tmp_path / "expected.pdf"
    argv = window.current_pipeline().to_argv(expected)
    run_pdftl(argv)
    assert expected.exists()
    assert _pages(saved) == _pages(expected)
    assert _pages(saved) == [(100, 90), (200, 90)]


def test_ctrl_o_add_input(qtbot, make_window, tmp_path):
    window = make_window([])
    other = _make_pdf(tmp_path / "b.pdf", [50])
    window.ask_open_paths = lambda *a, **k: [str(other)]
    assert window.input_list.count() == 0
    fire(qtbot, "add_input")
    assert window.input_list.count() == 1
    window.ask_open_paths = lambda *a, **k: []
    fire(qtbot, "add_input")
    assert window.input_list.count() == 1


def test_ctrl_n_add_stage(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    assert len(window.boxes) == 1
    fire(qtbot, "add_stage")
    assert len(window.boxes) == 2
    assert _focus_widget_is(window, window.boxes[1].op)


def test_ctrl_up_down_move_stage(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    fire(qtbot, "add_stage")
    assert window.current_index() == 1
    first_id = id(window.boxes[0])
    fire(qtbot, "move_stage_up")
    assert window.current_index() == 0
    assert id(window.boxes[0]) != first_id
    fire(qtbot, "move_stage_down")
    assert window.current_index() == 1


def test_alt_up_down_navigate(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    fire(qtbot, "add_stage")
    fire(qtbot, "focus_op")
    assert window.current_index() == 1
    fire(qtbot, "prev_stage")
    assert window.current_index() == 0
    assert _focus_widget_is(window, window.boxes[0].strip)
    fire(qtbot, "next_stage")
    assert window.current_index() == 1
    assert _focus_widget_is(window, window.boxes[1].strip)


def test_alt_up_down_from_editable_combo_still_navigates(qtbot, make_window, two_pages):
    """Finding: Alt+Up/Down fires even while an editable WheelSafeCombo has focus."""
    window = make_window([str(two_pages)])
    fire(qtbot, "add_stage")
    fire(qtbot, "focus_op")
    assert _focus_widget_is(window, window.boxes[1].op)
    fire(qtbot, "prev_stage")
    assert window.current_index() == 0


def test_delete_stage_shortcut_from_strip(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    fire(qtbot, "add_stage")
    assert len(window.boxes) == 2
    fire(qtbot, "focus_pages")
    fire(qtbot, "delete_stage")
    assert len(window.boxes) == 1


def test_ctrl_del_in_line_edit_deletes_a_word_not_the_stage(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    stage1 = window.boxes[0]
    fire(qtbot, "focus_args")
    type_text(qtbot, "hello world")
    press(qtbot, "Home")
    press(qtbot, "Ctrl+Del")
    assert len(window.boxes) == 1
    word_delete = QKeySequence.keyBindings(QKeySequence.StandardKey.DeleteEndOfWord)
    if QKeySequence("Ctrl+Del") in word_delete:  # not on macOS, where Ctrl is Cmd
        assert stage1.args.text() == "world"


def test_ctrl_k_apply_command(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    fire(qtbot, "focus_command")
    assert _focus_widget_is(window, window.cli)
    replace_selection(qtbot, f"{two_pages} rotate 1east")
    press(qtbot, "Return")
    assert len(window.boxes) == 1
    assert window.boxes[0].op.currentText() == "rotate"
    assert window.boxes[0].args.text() == "1east"


def test_command_bar_failure_message(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    fire(qtbot, "focus_command")
    replace_selection(qtbot, "not_a_real_operation")
    press(qtbot, "Return")
    assert "Command not applied" in window.statusBar().currentMessage()
    assert "Command not applied" in window.console.toPlainText()


def test_ctrl_l_clear_console(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.console.append_plain("hello\n")
    fire(qtbot, "focus_console")
    assert _focus_widget_is(window, window.console)
    fire(qtbot, "clear_console")
    assert window.console.toPlainText() == ""


def test_ctrl_f_find_text(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.console.append_plain("a needle in a haystack\n")
    window.ask_text = lambda *a, **k: "needle"
    fire(qtbot, "focus_console")
    fire(qtbot, "find_text")
    assert window.console.textCursor().hasSelection()


def test_delete_in_input_list_removes_input(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.input_list.setFocus()
    qtbot.waitUntil(lambda: _focus_widget_is(window, window.input_list))
    assert window.input_list.count() == 1
    fire(qtbot, "remove_input")
    assert window.input_list.count() == 0
    assert window.pipeline.inputs == ()


def test_ctrl_r_run(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    stage1 = window.boxes[0]
    fire(qtbot, "focus_args")
    type_text(qtbot, "1")
    fire(qtbot, "run")
    qtbot.waitUntil(lambda: "page" in stage1.status.text(), timeout=15000)


def test_ctrl_q_quits(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    fire(qtbot, "focus_op")
    assert window.isVisible()
    fire(qtbot, "quit")
    assert not window.isVisible()


def test_every_window_scope_shortcut_fires_via_its_key(qtbot, make_window, two_pages, tmp_path):
    """Self-contained: presses every non-native window-scope `keymap` action.

    Kept independent of the other tests (and of test execution order, which
    pytest-randomly and xdist both may change) so the coverage claim below
    does not depend on anything else in this file having run first.
    """
    window = make_window([str(two_pages)])
    stage1 = window.boxes[0]
    exercised: set[str] = set()

    def go(action_id: str) -> None:
        fire(qtbot, action_id)
        exercised.add(action_id)

    window.ask_open_paths = lambda *a, **k: []
    go("add_input")
    go("set_password")
    go("add_stage")
    go("insert_stage_before")
    go("undo")
    go("redo")
    go("undo")
    go("move_stage_up")
    go("move_stage_down")
    go("focus_op")
    go("prev_stage")
    go("next_stage")
    go("focus_args")
    replace_selection(qtbot, "1")
    go("run")
    qtbot.waitUntil(lambda: "page" in stage1.status.text(), timeout=15000)

    go("focus_pages")
    qtbot.keyClick(stage1.strip, Qt.Key.Key_Home)
    go("insert_selection")

    window.ask_save_path = lambda *a, **k: str(tmp_path / "saved.pdf")
    go("save")

    window.ask_pipeline_save_path = lambda *a, **k: str(tmp_path / "pipe.yaml")
    go("save_pipeline")
    window.ask_pipeline_open_path = lambda *a, **k: ""
    go("open_pipeline")

    go("focus_console")
    go("zoom_in")
    go("zoom_out")
    go("zoom_reset")
    go("save_console")
    window.ask_text = lambda *a, **k: ""
    go("find_text")
    go("clear_console")

    go("focus_command")

    go("focus_op")
    stage2 = window.boxes[window.current_index()]
    go("run")
    qtbot.waitUntil(lambda: "page" in stage2.status.text(), timeout=15000)
    window.open_url = lambda p: True
    go("view_stage")
    go("follow_stage")
    go("follow_stage")
    window.ask_save_path = lambda *a, **k: str(tmp_path / "exported.pdf")
    go("export_stage")

    go("copy_cli")
    go("focus_output")
    go("help")
    go("shortcuts")
    go("delete_stage")
    go("new_pipeline")
    go("quit")

    required = {a.id for a in keymap.ACTIONS if a.scope == "window" and not a.native and a.keys}
    missing = required - exercised
    assert not missing, f"window-scope shortcuts never exercised via keys: {missing}"


def test_close_stops_a_running_save(qtbot, make_window, two_pages, tmp_path):
    window = make_window([str(two_pages)])

    def cancellable_save(pipeline, output, cancel):
        cancel.wait(10)
        return StageResult(index=0, kind=ResultKind.ERROR, key="save", error="Cancelled")

    window.engine.save = cancellable_save
    window.ask_save_path = lambda *a, **k: str(tmp_path / "out.pdf")
    window.save()
    assert window.saver.isRunning()
    window.close()
    assert not window.saver.isRunning()


def test_close_event_ignored_when_discard_is_cancelled(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.ask_discard_pipeline = lambda *a: "cancel"
    window.close()
    assert window.isVisible()


def test_close_event_proceeds_when_discard_chosen(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.ask_discard_pipeline = lambda *a: "discard"
    window.close()
    assert not window.isVisible()


def test_dock_layout_and_window_size_survive_a_restart(qtbot, make_window):
    first = make_window([])
    first.help_dock.setFloating(True)
    first.console_dock.close()
    first.resize(640, 480)
    first.close()
    second = make_window([])
    assert second.help_dock.isFloating()
    assert not second.console_dock.isVisible()
    assert second.size().width() == 640


def test_zoom_survives_a_restart(qtbot, make_window):
    first = make_window([])
    size = first.console.font().pointSizeF()
    first.console.setFocus()
    first.zoom_focused(2)
    first.close()
    second = make_window([])
    assert second.console.font().pointSizeF() == size + 2
