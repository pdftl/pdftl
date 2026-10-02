"""Tests for window_stages: adding, moving, deleting and focusing stages."""

import shlex
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pdftl.gui import window_menus, window_stages
from pdftl.gui.command_bar import parse_command
from pdftl.gui.interfaces import Stage
from pdftl.gui.main_window import MainWindow
from tests.gui.window_support import (
    _enabled,
    _expected_top,
    _focus_stage,
    _focus_widget_is,
    _gap_between,
    _run_args,
    _settle,
    _top_in_viewport,
    fire,
)


def test_insert_selection_no_strip_focused_shows_status(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.cli.setFocus()
    qtbot.waitUntil(lambda: _focus_widget_is(window, window.cli))
    window.insert_selection()
    assert "Select pages" in window.statusBar().currentMessage()


def test_insert_selection_targets_existing_next_stage(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.add_stage()
    stage1, stage2 = window.boxes
    stage1.args.setText("1-2")
    stage1.strip.setFocus()
    qtbot.waitUntil(lambda: _focus_widget_is(window, stage1.strip))
    qtbot.waitUntil(lambda: stage1.total == 2, timeout=15000)
    qtbot.keyClick(stage1.strip, Qt.Key.Key_Home)
    window.insert_selection()
    assert len(window.boxes) == 2
    assert stage2.args.text() == "1"


def test_new_stage_is_scrolled_into_view(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.resize(900, 500)
    for _ in range(6):
        fire(qtbot, "add_stage")
    newest = window.boxes[-1]
    qtbot.waitUntil(lambda: not newest.visibleRegion().isEmpty())
    assert window.inputs_box.visibleRegion().isEmpty()


def test_reveal_ignores_a_stage_deleted_before_layout(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    fire(qtbot, "add_stage")
    box = window.boxes[-1]
    window._reveal(box)
    fire(qtbot, "delete_stage")
    qtbot.wait(20)
    assert box not in window.boxes


def test_deleted_stage_is_detached_and_its_renders_cancelled(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.add_stage()
    box = window.boxes[1]
    box.key = "some-key"
    cancelled = []
    window.thumbs.cancel = cancelled.append
    box.strip.setFocus()
    window.delete_stage()
    assert cancelled == ["some-key"]
    assert window.stages_layout.indexOf(box) == -1
    assert not box.isVisible()


def test_delete_stage_with_no_boxes_is_a_noop(make_window):
    window = make_window([])
    window.delete_stage()
    window.delete_stage()
    assert window.boxes == []


def test_move_stage_with_no_boxes_is_a_noop(make_window):
    window = make_window([])
    window.delete_stage()
    window.move_stage(1)
    assert window.boxes == []


def test_move_stage_clamped_at_the_end_is_a_noop(make_window, two_pages):
    window = make_window([str(two_pages)])
    assert len(window.boxes) == 1
    box = window.boxes[0]
    window.move_stage(-1)
    window.move_stage(1)
    assert window.boxes == [box]


def test_step_with_no_boxes_is_a_noop(make_window):
    window = make_window([])
    window.delete_stage()
    window.step(1)
    assert window.boxes == []


def test_focus_part_with_no_boxes_is_a_noop(make_window):
    window = make_window([])
    window.delete_stage()
    window._focus_part("op")


def test_current_stage_is_highlighted_and_moves_with_focus(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    fire(qtbot, "add_stage")
    first, second = window.boxes
    assert window.current is second
    assert "(current)" in second.title.text() and "(current)" not in first.title.text()
    assert second.autoFillBackground() and not first.autoFillBackground()
    tinted = second.palette().window().color()
    assert tinted != first.palette().window().color()
    first.args.setFocus()
    qtbot.waitUntil(lambda: window.current is first)
    assert "(current)" in first.title.text() and "(current)" not in second.title.text()
    assert first.palette().window().color() == tinted


def test_current_stage_survives_focus_leaving_the_stages(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    fire(qtbot, "add_stage")
    fire(qtbot, "add_stage")
    first = window.boxes[0]
    first.args.setFocus()
    qtbot.waitUntil(lambda: window.current is first)
    window.console.setFocus()
    qtbot.waitUntil(lambda: _focus_widget_is(window, window.console))
    assert window.current is first
    survivors = window.boxes[1:]
    fire(qtbot, "delete_stage")
    assert window.boxes == survivors


def test_stage_actions_are_disabled_without_a_current_stage(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.boxes[0].args.setFocus()
    qtbot.waitUntil(lambda: window.current is window.boxes[0])
    window.inputs_box.strip.setFocus()
    qtbot.waitUntil(lambda: window.current is window.inputs_box)
    for action_id in window_menus.STAGE_ACTIONS + (
        "move_stage_up",
        "move_stage_down",
        "follow_stage",
    ):
        assert not _enabled(window, action_id), action_id
    assert _enabled(window, "add_stage")
    fire(qtbot, "delete_stage")
    assert len(window.boxes) == 1
    window.delete_stage()
    window.move_stage(1)
    window._focus_part("op")
    assert len(window.boxes) == 1


def test_deleting_the_only_stage_leaves_no_current_stage(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.boxes[0].args.setFocus()
    qtbot.waitUntil(lambda: window.current is window.boxes[0])
    fire(qtbot, "delete_stage")
    assert window.boxes == [] and window.current is None
    assert not _enabled(window, "delete_stage")
    assert not _enabled(window, "prev_stage") and not _enabled(window, "insert_selection")


def test_move_actions_follow_the_current_position(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    fire(qtbot, "add_stage")
    assert _enabled(window, "move_stage_up") and not _enabled(window, "move_stage_down")
    fire(qtbot, "move_stage_up")
    assert window.current is window.boxes[0]
    assert not _enabled(window, "move_stage_up") and _enabled(window, "move_stage_down")


def test_output_actions_need_output_on_the_current_box(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    stage = window.boxes[0]
    stage.args.setFocus()
    qtbot.waitUntil(lambda: window.current is stage)
    stage.show_pdf(None, status="not run")
    assert not _enabled(window, "view_stage") and not _enabled(window, "export_stage")
    assert not _enabled(window, "follow_stage")
    _run_args(qtbot, window, stage, "1")
    assert _enabled(window, "view_stage") and _enabled(window, "export_stage")
    assert _enabled(window, "follow_stage")


def test_clicking_a_stage_makes_it_current(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    fire(qtbot, "add_stage")
    first = window.boxes[0]
    qtbot.mouseClick(first.title, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.current is first)
    assert _focus_widget_is(window, first.strip)


def test_applying_a_command_keeps_the_inputs_box_current(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.inputs_box.strip.setFocus()
    qtbot.waitUntil(lambda: window.current is window.inputs_box)
    window.add_stage()
    window.inputs_box.strip.setFocus()
    qtbot.waitUntil(lambda: window.current is window.inputs_box)
    window.apply_command(parse_command(f"{shlex.quote(str(two_pages))} rotate 1east"))
    assert [b.stage() for b in window.boxes] == [Stage("rotate", "1east")]
    assert window.current is window.inputs_box


def test_revealed_stage_scrolls_its_top_to_the_top(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.resize(900, 600)
    for _ in range(6):
        fire(qtbot, "add_stage")
        newest = window.boxes[-1]
        qtbot.waitUntil(lambda: _top_in_viewport(window, newest) == _expected_top(window, newest))
    for _ in range(4):
        fire(qtbot, "prev_stage")
    third = window.boxes[2]
    assert window.current is third
    qtbot.waitUntil(lambda: _top_in_viewport(window, third) == window_stages.REVEAL_MARGIN)
    fire(qtbot, "focus_args")
    qtbot.wait(50)
    assert _top_in_viewport(window, third) == window_stages.REVEAL_MARGIN


def test_stage_buttons_insert_and_delete_next_to_their_stage(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.boxes[0].args.setText("mid")
    window.schedule()
    middle = window.boxes[0]
    qtbot.mouseClick(middle.insert_before, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(middle.insert_after, Qt.MouseButton.LeftButton)
    assert [b.args.text() for b in window.boxes] == ["", "mid", ""]
    assert window.boxes[1] is middle
    assert window.current is window.boxes[2]
    qtbot.mouseClick(middle.delete, Qt.MouseButton.LeftButton)
    assert middle not in window.boxes and len(window.boxes) == 2
    assert "Deleted stage 2; Ctrl+Z undoes" in window.statusBar().currentMessage()
    fire(qtbot, "undo")
    assert [b.args.text() for b in window.boxes] == ["", "mid", ""]


def test_insert_before_needs_a_current_stage(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.inputs_box.strip.setFocus()
    qtbot.waitUntil(lambda: window.current is window.inputs_box)
    assert not window.actions_by_id["insert_stage_before"].isEnabled()
    window.insert_stage_before()
    assert len(window.boxes) == 1
    _focus_stage(qtbot, window, window.boxes[0])
    first = window.boxes[0]
    fire(qtbot, "insert_stage_before")
    assert window.boxes[1] is first and len(window.boxes) == 2


def test_moving_focus_to_another_stage_scrolls_back_to_the_left(qtbot, make_window):
    window = make_window([])
    window.add_stage()
    first, second = window.boxes
    window.stages_widget.setMinimumWidth(window.scroll.viewport().width() * 3)
    first.args.setFocus()
    qtbot.waitUntil(lambda: window.current is first)
    bar = window.scroll.horizontalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0)
    bar.setValue(bar.maximum())
    first.strip.setFocus()
    assert bar.value() == bar.maximum()
    second.args.setFocus()
    qtbot.waitUntil(lambda: window.current is second)
    assert bar.value() == 0


def test_stage_file_names_start_with_the_input_names(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.boxes[0].op.setCurrentText("rotate")
    window.add_stage()
    window.boxes[1].op.setCurrentText("shrink")
    assert [window._box_name(b) for b in window.boxes] == ["a-rotate", "a-shrink"]


def test_set_current_to_the_same_box_is_a_noop(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.boxes[0].strip.setFocus()
    qtbot.waitUntil(lambda: window.current is window.boxes[0])
    window._set_current(window.current)
    assert window.current is window.boxes[0]


def test_tab_order_reaches_the_output_box_after_the_last_stage(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.add_stage()
    assert window.boxes[-1].args.nextInFocusChain() is window.output_box.options


def test_tab_order_falls_back_to_the_inputs_strip_with_no_stages(make_window, monkeypatch):
    calls = []
    monkeypatch.setattr(
        MainWindow, "setTabOrder", lambda self, a, b: calls.append((a, b)), raising=False
    )
    window = make_window([])
    window.delete_stage()
    assert not window.boxes
    assert calls[-1] == (window.inputs_box.strip, window.output_box.options)


def test_double_click_between_stages_inserts_one_there(qtbot, make_window):
    window = make_window([])
    window.resize(900, 1400)
    window.add_stage()
    first, second = window.boxes
    area = window.stages_widget
    qtbot.waitUntil(lambda: second.geometry().top() > first.geometry().bottom())
    x = first.geometry().center().x()
    QTest.mouseDClick(area, Qt.MouseButton.LeftButton, pos=QPoint(x, _gap_between(first, second)))
    assert len(window.boxes) == 3
    assert window.boxes.index(first) == 0 and window.boxes.index(second) == 2
    _settle(qtbot, window)
    QTest.mouseDClick(
        area, Qt.MouseButton.LeftButton, pos=QPoint(x, _gap_between(window.inputs_box, first))
    )
    assert window.boxes.index(first) == 1
    _settle(qtbot, window)
    below = window.output_box.geometry().bottom() + 20
    assert area.childAt(QPoint(x, below)) is None
    QTest.mouseDClick(area, Qt.MouseButton.LeftButton, pos=QPoint(x, below))
    assert len(window.boxes) == 5
    assert window.boxes[-1] not in (first, second)


def test_double_click_inside_a_stage_inserts_nothing(qtbot, make_window):
    window = make_window([])
    box = window.boxes[0]
    QTest.mouseDClick(box, Qt.MouseButton.LeftButton, pos=QPoint(box.width() - 2, 2))
    assert window.boxes == [box]


def test_dropping_a_dragged_stage_moves_it_and_focuses_it(qtbot, make_window):
    window = make_window([])
    window.add_stage()
    window.add_stage()
    for box, args in zip(window.boxes, ("1", "2", "3")):
        box.args.setText(args)
        box.edited.emit()
    first = window.boxes[0]
    window.move_box_to(first, 2, reveal=False)
    assert window.boxes[2] is first
    qtbot.waitUntil(lambda: first.current)
    assert "Stage 3" in first.title.text()
    assert "Moved to stage 3" in window.statusBar().currentMessage()
    assert [b.stage().args_text for b in window.boxes] == ["2", "3", "1"]
    window.undo()
    assert [b.stage().args_text for b in window.boxes] == ["1", "2", "3"]


def test_a_stage_drag_goes_through_the_list_gestures(qtbot, make_window):
    window = make_window([])
    window.add_stage()
    dragged = []
    window.gestures.run_drag = lambda drag: dragged.append(drag.mimeData())
    window.boxes[0].drag_started.emit(window.boxes[0])
    assert len(dragged) == 1 and window.gestures.dragging is None


def test_focus_output_box_focuses_its_options_and_scrolls_to_it(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    for _ in range(4):
        window.add_stage()
    window.resize(600, 300)
    window.scroll.verticalScrollBar().setValue(0)
    window.focus_output_box()
    qtbot.waitUntil(lambda: _focus_widget_is(window, window.output_box.options))
    qtbot.waitUntil(
        lambda: (
            _top_in_viewport(window, window.output_box) == _expected_top(window, window.output_box)
        )
    )
    assert window.scroll.horizontalScrollBar().value() == 0


def test_insert_selection_from_the_last_stage_adds_a_stage_for_it(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    (stage1,) = window.boxes
    stage1.strip.setFocus()
    qtbot.waitUntil(lambda: _focus_widget_is(window, stage1.strip))
    qtbot.waitUntil(lambda: stage1.total == 2, timeout=15000)
    qtbot.keyClick(stage1.strip, Qt.Key.Key_Home)
    window.insert_selection()
    assert len(window.boxes) == 2
    assert window.boxes[1].args.text() == "1"


def _fake_picker(window, token="10,20"):
    calls = []

    def picker(parent, path, pages, passwords):
        calls.append((parent, path, list(pages), passwords))
        return None if token is None else SimpleNamespace(token=token)

    window.run_picker = picker
    return calls


def test_pick_point_in_focused_arguments_picks_on_the_stage_input(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    stage = window.boxes[0]
    qtbot.waitUntil(lambda: window.inputs_box.total == 2, timeout=15000)
    calls = _fake_picker(window)
    stage.args.setText("1(abs")
    stage.args.setFocus()
    qtbot.waitUntil(lambda: _focus_widget_is(window, stage.args))
    fire(qtbot, "pick_point")
    assert calls == [(window, window.inputs_box.path, [1, 2], window._password_for)]
    assert stage.args.text() == "1(abs,10,20"
    assert stage.args.cursorPosition() == len("1(abs,10,20")


def test_pick_point_from_a_strip_uses_its_selection_and_fills_a_new_next_stage(
    qtbot, make_window, two_pages
):
    window = make_window([str(two_pages)])
    stage1 = window.boxes[0]
    stage1.args.setText("1-2")
    qtbot.waitUntil(lambda: stage1.total == 2, timeout=15000)
    stage1.strip.setFocus()
    qtbot.waitUntil(lambda: _focus_widget_is(window, stage1.strip))
    stage1.strip.clearSelection()
    stage1.strip.item(1).setSelected(True)
    calls = _fake_picker(window)
    window.pick_point()
    assert [c[1:3] for c in calls] == [(stage1.path, [2])]
    assert len(window.boxes) == 2
    assert window.boxes[1].args.text() == "10,20"


def test_cancelling_the_picker_changes_nothing(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    qtbot.waitUntil(lambda: window.inputs_box.total == 2, timeout=15000)
    window.inputs_box.strip.setFocus()
    qtbot.waitUntil(lambda: window.current is window.inputs_box)
    calls = _fake_picker(window, token=None)
    window.pick_point()
    assert len(calls) == 1
    assert [b.args.text() for b in window.boxes] == [""]


def test_pick_point_without_pages_says_so(qtbot, make_window):
    window = make_window([])
    calls = _fake_picker(window)
    window.cli.setFocus()
    window.pick_point()
    assert "No pages" in window.statusBar().currentMessage()
    window.boxes[0].args.setFocus()
    qtbot.waitUntil(lambda: _focus_widget_is(window, window.boxes[0].args))
    window.pick_point()
    assert calls == []
    assert "No pages" in window.statusBar().currentMessage()


def test_pick_point_is_disabled_without_a_current_box(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.boxes[0].args.setFocus()
    qtbot.waitUntil(lambda: window.current is window.boxes[0])
    fire(qtbot, "delete_stage")
    assert not _enabled(window, "pick_point")
    calls = _fake_picker(window)
    window.pick_point()
    assert calls == [] and "No pages" in window.statusBar().currentMessage()


def test_the_real_picker_inserts_the_centre_of_the_page(qtbot, make_window, two_pages):
    from PySide6.QtCore import QTimer

    window = make_window([str(two_pages)])
    stage = window.boxes[0]
    qtbot.waitUntil(lambda: window.inputs_box.total == 2, timeout=15000)
    qtbot.keyClick(window.inputs_box.strip, Qt.Key.Key_Home)
    stage.args.setFocus()
    qtbot.waitUntil(lambda: _focus_widget_is(window, stage.args))

    def accept():
        dialog = QApplication.activeModalWidget()
        if dialog is None or dialog is window:
            QTimer.singleShot(20, accept)
            return
        qtbot.keyClick(dialog, Qt.Key.Key_Return)

    QTimer.singleShot(0, accept)
    window.pick_point()
    # Page 1 is 100pt square; the crosshair starts at its centre.
    assert stage.args.text() == "50pt,50pt"


def test_run_picker_opens_the_picker_module_lazily(monkeypatch):
    from pdftl.gui import picker

    seen = []
    monkeypatch.setattr(picker, "pick_point", lambda *a: seen.append(a) or "picked")
    assert window_stages.run_picker("p", "f.pdf", [1], None) == "picked"
    assert seen == [("p", "f.pdf", [1], None)]
