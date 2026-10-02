"""Tests for window_help: the help panel, console and zoom."""

import sys

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from pdftl.gui.interfaces import OpInfo, OpKind
from tests.gui.window_support import _focus_widget_is, fire, press


def test_clear_button_empties_console(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.console.append_plain("old output\n")
    qtbot.mouseClick(window.clear_button, Qt.MouseButton.LeftButton)
    assert window.console.toPlainText() == ""


def test_help_toggle_back_falls_back_when_return_widget_was_deleted(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.add_stage()
    window.boxes[1].op.setFocus()
    window.toggle_help()
    assert window.help.hasFocus()
    window.delete_stage()
    window.help.setFocus()
    window.toggle_help()
    assert QApplication.focusWidget() is window.boxes[0].op.lineEdit() or (
        QApplication.focusWidget() is window.boxes[0].op
    )


def test_help_toggle_back_with_no_stages_focuses_command_bar(make_window):
    window = make_window([])
    window.delete_stage()
    window._help_return = None
    window.help.setFocus()
    window.toggle_help()
    assert window.cli.hasFocus()


def test_find_text_empty_needle_is_a_noop(make_window, two_pages):
    window = make_window([str(two_pages)])
    before = window.statusBar().currentMessage()
    window.ask_text = lambda *a, **k: ""
    window.find_text()
    assert window.statusBar().currentMessage() == before


def test_find_text_not_found_reports_status(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.console.append_plain("nothing relevant\n")
    window.ask_text = lambda *a, **k: "xyzzy"
    window.find_text()
    assert "Not found" in window.statusBar().currentMessage()


def test_find_text_wraps_to_start_when_not_found_forward(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.console.append_plain("needle then more text\n")
    window.console.moveCursor(window.console.textCursor().MoveOperation.End)
    window.ask_text = lambda *a, **k: "needle"
    window.find_text()
    assert window.console.textCursor().hasSelection()


def test_save_console_empty_path_is_a_noop(make_window, two_pages, tmp_path):
    window = make_window([str(two_pages)])
    before = window.statusBar().currentMessage()
    window.ask_save_path = lambda *a, **k: ""
    window.save_console()
    assert window.statusBar().currentMessage() == before


def test_show_help_dedupes_same_op(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.show_help("cat")
    text_after_first = window.help.toPlainText()
    window._help_op = "cat"
    window.show_help("cat")
    assert window.help.toPlainText() == text_after_first


def test_show_help_unknown_op(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.show_help("not_a_real_operation")
    assert "Unknown operation" in window.help.toPlainText()


def test_show_help_reason_and_examples(monkeypatch, make_window, two_pages):
    window = make_window([str(two_pages)])
    captured = {}
    monkeypatch.setattr(
        window.help, "setMarkdown", lambda text: captured.__setitem__("text", text)
    )
    window.show_help("render")
    assert "*In the GUI:*" in captured["text"]
    assert "## Examples" in captured["text"]


def test_show_help_no_examples(monkeypatch, make_window, two_pages):
    """A synthetic OpInfo isolates the `info.examples` branch from any real
    operation's long_desc happening to mention "Examples" in its prose."""
    window = make_window([str(two_pages)])
    window.infos["_fake_op"] = OpInfo(
        name="_fake_op", kind=OpKind.PDF, desc="d", usage="u", long_desc="plain description"
    )
    captured = {}
    monkeypatch.setattr(
        window.help, "setMarkdown", lambda text: captured.__setitem__("text", text)
    )
    window.show_help("_fake_op")
    assert "## Examples" not in captured["text"]


def test_follow_focus_to_none_shows_general_help(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.show_help("cat")
    window._follow_focus(None, None)
    assert window._help_op == "cat"


def test_follow_focus_from_non_stage_widget(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.show_help("cat")
    window.cli.setFocus()
    qtbot.waitUntil(lambda: _focus_widget_is(window, window.cli))
    assert window._help_op == ""


def test_find_text_found_on_first_forward_search(make_window):
    window = make_window([])
    window.console.append_plain("needle right after the start\nmore text below\n")
    window.console.moveCursor(window.console.textCursor().MoveOperation.Start)
    window.ask_text = lambda *a, **k: "needle"
    window.find_text()
    assert window.console.textCursor().hasSelection()


def test_focusing_output_box_shows_output_options_help(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.output_box.options.setFocus()
    qtbot.waitUntil(lambda: "Output options" in window.help.toPlainText())


def test_cursor_on_a_known_option_highlights_it_first(qtbot, make_window, two_pages):
    """uncompress and flatten are both in the "General" group, where flatten
    sorts first alphabetically; putting the cursor on uncompress must still
    move it to the very front of that group."""
    window = make_window([str(two_pages)])
    window.output_box.options.setFocus()
    qtbot.keyClicks(window.output_box.options, "uncompress")
    window.output_box.options.setCursorPosition(2)
    text = window.help.toPlainText()
    assert text.index("uncompress") < text.index("flatten")


def test_the_console_is_a_dock_that_the_console_menu_brings_back(qtbot, make_window):
    window = make_window([])
    toggle = window.actions_by_id["show_console"]
    assert toggle is window.console_dock.toggleViewAction()
    assert window.console_dock.widget().isAncestorOf(window.console)
    assert window.console_dock.windowTitle().startswith("Console")
    window.console_dock.close()
    assert not window.console_dock.isVisible()
    toggle.trigger()
    qtbot.waitUntil(window.console_dock.isVisible)
    assert window.actions_by_id["show_help_panel"] is window.help_dock.toggleViewAction()


def test_focusing_the_console_shows_its_dock_first(qtbot, make_window):
    window = make_window([])
    window.console_dock.close()
    window.actions_by_id["focus_console"].trigger()
    qtbot.waitUntil(window.console.hasFocus)
    assert window.console_dock.isVisible()


def test_focusing_the_output_box_makes_it_current_instead_of_the_stage(qtbot, make_window):
    window = make_window([])
    stage = window.boxes[0]
    stage.args.setFocus()
    qtbot.waitUntil(lambda: stage.current)
    window.output_box.options.setFocus()
    qtbot.waitUntil(lambda: window.output_box.current)
    assert not stage.current
    assert window.current is None
    stage.args.setFocus()
    qtbot.waitUntil(lambda: stage.current)
    assert not window.output_box.current


@pytest.mark.parametrize(
    ("part", "name"), [("console", "Console"), ("help", "Help"), ("cli", "Pipeline")]
)
def test_zoom_keys_zoom_the_focused_part_only(qtbot, make_window, part, name):
    window = make_window([])
    window.show()
    qtbot.waitExposed(window)
    widget = getattr(window, part)
    others = [w for w in (window.console, window.help, window.cli) if w is not widget]
    before = {w: w.font().pointSizeF() for w in (widget, *others)}
    widget.setFocus()
    qtbot.waitUntil(widget.hasFocus)
    for seq in ("Ctrl+=", "Ctrl++"):
        press(qtbot, seq)
    assert widget.font().pointSizeF() == before[widget] + 2
    assert window.statusBar().currentMessage().startswith(f"{name} zoom ")
    press(qtbot, "Ctrl+-")
    assert widget.font().pointSizeF() == before[widget] + 1
    for other in others:
        assert other.font().pointSizeF() == before[other]
    press(qtbot, "Ctrl+0")
    assert widget.font().pointSizeF() == before[widget]


def test_the_stage_boxes_follow_the_pipeline_zoom(make_window):
    window = make_window([])
    before = window.boxes[0].args.font().pointSizeF()
    window.zoom_focused(1)
    assert window.boxes[0].args.font().pointSizeF() == before + 1


def test_f1_into_help_and_back(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    fire(qtbot, "focus_op")
    origin = QApplication.focusWidget()
    fire(qtbot, "help")
    assert _focus_widget_is(window, window.help)
    fire(qtbot, "help")
    assert QApplication.focusWidget() is origin


def test_ctrl_slash_shortcuts_then_f1_goes_to_the_current_stage(qtbot, make_window, two_pages):
    """Entering help via `shortcuts` records no return target; F1 falls back to the stage."""
    window = make_window([str(two_pages)])
    assert window._help_return is None
    fire(qtbot, "shortcuts")
    assert _focus_widget_is(window, window.help)
    fire(qtbot, "help")
    assert _focus_widget_is(window, window.boxes[0].op)


def test_ctrl_shift_s_save_console(qtbot, make_window, two_pages, tmp_path):
    window = make_window([str(two_pages)])
    window.console.append_plain("console contents\n")
    target = tmp_path / "console.txt"
    window.ask_save_path = lambda *a, **k: str(target)
    fire(qtbot, "focus_op")
    fire(qtbot, "save_console")
    assert target.read_text() == "console contents\n"


def test_window_has_the_app_icon_and_an_about_box(make_window):
    window = make_window()
    assert window.windowIcon().cacheKey() != 0 and not window.windowIcon().isNull()
    shown = []
    window.run_dialog = shown.append
    about = window.actions_by_id["about"]
    assert about.text() == "&About pdftl"
    about.trigger()
    (box,) = shown
    assert box.parent() is window
    if sys.platform != "darwin":  # macOS message boxes have no title
        assert box.windowTitle() == "About pdftl"
    help_menu = window.menuBar().actions()[-1].menu()
    assert help_menu.actions()[-1] is about and help_menu.actions()[-2].isSeparator()
