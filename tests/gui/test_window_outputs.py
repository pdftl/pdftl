"""Tests for window_outputs: viewing, following and saving outputs."""

import shlex
import shutil
from pathlib import Path

import pikepdf
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from pdftl.gui import window_outputs
from pdftl.gui.interfaces import ResultKind, StageResult
from tests.gui.window_support import (
    _focus_widget_is,
    _make_pdf,
    _pages,
    _run_args,
    fire,
    type_text,
)


def test_view_output_reports_when_no_viewer_can_open_it(make_window, two_pages):
    window = make_window([str(two_pages)])
    stage1 = window.boxes[0]
    stage1.path, stage1.key = two_pages, "k"
    window.open_url = lambda p: False
    window.view_output(stage1)
    assert "No viewer could open" in window.statusBar().currentMessage()


def test_view_output_reports_a_copy_failure(make_window, two_pages, monkeypatch):
    window = make_window([str(two_pages)])
    stage1 = window.boxes[0]
    stage1.path, stage1.key = two_pages, "k"

    def fail(_src, _dst):
        raise PermissionError("in use")

    monkeypatch.setattr(shutil, "copyfile", fail)
    window.view_output(stage1)
    assert "Could not open" in window.statusBar().currentMessage()


def test_save_output_text_writes_plain_text(make_window, two_pages, tmp_path):
    window = make_window([str(two_pages)])
    stage1 = window.boxes[0]
    stage1.text = "\x1b[1mbold\x1b[0m plain"
    target = tmp_path / "out.txt"
    window.ask_save_path = lambda *a, **k: str(target)
    window.save_output_text(stage1)
    assert target.read_text(encoding="utf-8") == "bold plain"


def test_save_output_text_reports_a_write_failure(make_window, two_pages, tmp_path):
    window = make_window([str(two_pages)])
    stage1 = window.boxes[0]
    stage1.text = "some text"
    target = tmp_path / "missing_dir" / "out.txt"
    window.ask_save_path = lambda *a, **k: str(target)
    window.save_output_text(stage1)
    assert "Could not save" in window.statusBar().currentMessage()
    assert not target.exists()


def test_save_with_no_stages_is_a_noop(make_window):
    window = make_window([])
    window.delete_stage()
    assert window.boxes == []
    called = []
    window.ask_save_path = lambda *a, **k: called.append(1) or "x.pdf"
    window.save()
    assert not called


def test_save_failure_reports_in_status_bar(qtbot, make_window, two_pages, tmp_path):
    window = make_window([str(two_pages)])
    window.boxes[0].args.setText("1")
    window.engine.save = lambda *a, **k: StageResult(
        index=0, kind=ResultKind.ERROR, key="k", error="boom"
    )
    window.ask_save_path = lambda *a, **k: str(tmp_path / "out.pdf")
    window.save()
    qtbot.waitUntil(lambda: "Save failed" in window.statusBar().currentMessage())


def test_second_save_while_one_runs_is_refused(qtbot, make_window, two_pages, tmp_path):
    import threading as _threading

    window = make_window([str(two_pages)])
    release = _threading.Event()

    def slow_save(pipeline, output, cancel):
        release.wait(10)
        return StageResult(index=0, kind=ResultKind.PDF, key="k", pdf_path=output)

    window.engine.save = slow_save
    window.ask_save_path = lambda *a, **k: str(tmp_path / "out.pdf")
    window.save()
    window.save()
    assert window.statusBar().currentMessage() == "A save is already running"
    release.set()
    qtbot.waitUntil(lambda: "Saved" in window.statusBar().currentMessage())


def test_preview_page_without_output_is_a_noop(make_window, two_pages):
    window = make_window([str(two_pages)])
    stage1 = window.boxes[0]
    assert stage1.path is None
    window.preview_page(stage1, 1)
    assert window.preview is None


def test_box_name_falls_back_to_input_when_no_path(make_window):
    window = make_window([])
    assert window.inputs_box.path is None
    assert window._box_name(window.inputs_box) == "input"


def test_view_output_uses_input_path_directly(make_window, tmp_path, qtbot):
    window = make_window([])
    a = _make_pdf(tmp_path / "a.pdf", [10])
    window.add_file(str(a))
    window.input_list.setCurrentRow(0)
    qtbot.waitUntil(lambda: window.inputs_box.path is not None, timeout=5000)
    captured = {}
    window.open_url = lambda p: captured.setdefault("path", p) or True
    window.view_output(window.inputs_box)
    assert captured["path"] == window.inputs_box.path


def test_view_focused_with_no_output_shows_status(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.boxes[0].strip.setFocus()
    window.view_focused()
    assert "Focus a stage that has output first" in window.statusBar().currentMessage()


def test_export_focused_with_no_output_shows_status(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.boxes[0].strip.setFocus()
    window.export_focused()
    assert "Focus a stage that has output first" in window.statusBar().currentMessage()


def test_export_output_cancelled_dialog_is_a_noop(make_window, two_pages):
    window = make_window([str(two_pages)])
    stage1 = window.boxes[0]
    stage1.path = two_pages
    before = window.statusBar().currentMessage()
    window.ask_save_path = lambda *a, **k: ""
    window.export_output(stage1)
    assert window.statusBar().currentMessage() == before


def test_export_output_reports_a_copy_failure(make_window, two_pages, tmp_path, monkeypatch):
    window = make_window([str(two_pages)])
    stage1 = window.boxes[0]
    stage1.path = two_pages
    target = tmp_path / "out.pdf"
    window.ask_save_path = lambda *a, **k: str(target)

    def fail(_src, _dst):
        raise PermissionError("locked")

    monkeypatch.setattr(shutil, "copyfile", fail)
    window.export_output(stage1)
    assert "Could not save" in window.statusBar().currentMessage()
    assert not target.exists()


def test_save_output_text_cancelled_dialog_is_a_noop(make_window, two_pages):
    window = make_window([str(two_pages)])
    stage1 = window.boxes[0]
    stage1.text = "some text"
    before = window.statusBar().currentMessage()
    window.ask_save_path = lambda *a, **k: ""
    window.save_output_text(stage1)
    assert window.statusBar().currentMessage() == before


def test_save_cancelled_dialog_is_a_noop(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.boxes[0].args.setText("1")
    before = window.statusBar().currentMessage()
    window.ask_save_path = lambda *a, **k: ""
    window.save()
    assert window.statusBar().currentMessage() == before


def test_follow_rewrites_one_file_as_the_stage_changes(qtbot, make_window, tmp_path):
    src = _make_pdf(tmp_path / "three.pdf", [100, 200, 300])
    window = make_window([str(src)])
    stage = window.boxes[0]
    _run_args(qtbot, window, stage, "1-3")
    opened = []
    window.open_url = lambda p: opened.append(p) or True
    fire(qtbot, "focus_args")
    fire(qtbot, "follow_stage")
    assert stage.follow.isChecked()
    (live,) = opened
    assert _pages(live) == [(100, 0), (200, 0), (300, 0)]

    inode = live.stat().st_ino
    _run_args(qtbot, window, stage, "2")
    assert _pages(live) == [(200, 0)]
    assert live.stat().st_ino == inode
    stamp = live.stat().st_mtime_ns
    window.run_pipeline()
    qtbot.waitUntil(lambda: "(cached)" in stage.status.text(), timeout=15000)
    assert live.stat().st_mtime_ns == stamp
    assert opened == [live]

    fire(qtbot, "follow_stage")
    assert not stage.follow.isChecked()
    _run_args(qtbot, window, stage, "3")
    assert _pages(live) == [(200, 0)]


def test_view_snapshot_is_not_changed_by_later_runs(qtbot, make_window, tmp_path):
    src = _make_pdf(tmp_path / "three.pdf", [100, 200, 300])
    window = make_window([str(src)])
    stage = window.boxes[0]
    _run_args(qtbot, window, stage, "1")
    opened = []
    window.open_url = lambda p: opened.append(p) or True
    window.view_output(stage)
    _run_args(qtbot, window, stage, "3")
    window.view_output(stage)
    first, second = opened
    assert first != second
    assert _pages(first) == [(100, 0)]
    assert _pages(second) == [(300, 0)]


def test_follow_button_toggles_following(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    stage = window.boxes[0]
    _run_args(qtbot, window, stage, "1")
    window.open_url = lambda p: True
    qtbot.mouseClick(stage.follow, Qt.MouseButton.LeftButton)
    assert stage.follow.isChecked() and stage in window.followed
    qtbot.mouseClick(stage.follow, Qt.MouseButton.LeftButton)
    assert not stage.follow.isChecked() and stage not in window.followed


def test_output_box_follow_button_follows_the_last_stage(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    stage = window.boxes[0]
    _run_args(qtbot, window, stage, "1")
    window.open_url = lambda p: True
    qtbot.mouseClick(window.output_box.follow, Qt.MouseButton.LeftButton)
    assert stage in window.followed
    assert window.output_box.follow.isChecked()
    qtbot.mouseClick(window.output_box.follow, Qt.MouseButton.LeftButton)
    assert stage not in window.followed
    assert not window.output_box.follow.isChecked()


def test_output_box_follow_syncs_when_the_stage_button_is_used_instead(
    qtbot, make_window, two_pages
):
    window = make_window([str(two_pages)])
    stage = window.boxes[0]
    _run_args(qtbot, window, stage, "1")
    window.open_url = lambda p: True
    qtbot.mouseClick(stage.follow, Qt.MouseButton.LeftButton)
    assert window.output_box.follow.isChecked()


def test_output_box_follow_with_no_stages_reports(make_window):
    window = make_window([])
    window.delete_stage()
    assert not window.boxes
    window.toggle_output_follow()
    assert "No stage output to follow yet" in window.statusBar().currentMessage()


def test_output_box_save_button_triggers_save(qtbot, make_window, tmp_path, two_pages):
    window = make_window([str(two_pages)])
    target = tmp_path / "out.pdf"
    window.ask_save_path = lambda *a, **k: str(target)
    qtbot.mouseClick(window.output_box.save, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: target.exists(), timeout=15000)


def test_output_options_edit_is_undoable_and_reruns(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    can_undo_before = window.history.can_undo()
    window.output_box.options.setFocus()
    qtbot.keyClicks(window.output_box.options, "keep_first_id")
    assert window.current_pipeline().output_options == "keep_first_id"
    assert can_undo_before is False
    assert window.history.can_undo()
    window.undo()
    assert window.current_pipeline().output_options == ""


def test_typing_output_path_sets_the_save_target_not_an_error(qtbot, make_window, two_pages):
    """The reported bug: 'output foo.pdf' must set the target, not fail a stage."""
    window = make_window([str(two_pages)])
    window.output_box.options.setFocus()
    qtbot.keyClicks(window.output_box.options, "output foo.pdf")
    qtbot.keyClick(window.output_box.options, Qt.Key.Key_Return)
    assert window.save_target == Path("foo.pdf")
    assert "foo.pdf" in window.output_box.target.text()
    assert window.output_box.options_text() == ""
    assert window.output_box.error.text() == ""
    assert "Ctrl+S writes it" in window.statusBar().currentMessage()
    assert window.current_pipeline().output_options == ""


def test_bad_output_options_text_is_reported_on_the_box_not_a_stage(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    stage = window.boxes[0]
    stage.status.setText("")
    window.output_box.options.setFocus()
    qtbot.keyClicks(window.output_box.options, "banana")
    assert "not a recognized output option" in window.output_box.error.text()
    assert window.current_pipeline().output_options == ""
    window.run_pipeline()
    qtbot.waitUntil(lambda: "page" in stage.status.text(), timeout=15000)
    assert "banana" not in stage.status.text()


def test_bad_output_options_text_clears_once_fixed(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.output_box.options.setFocus()
    qtbot.keyClicks(window.output_box.options, "banana")
    assert window.output_box.error.text() != ""
    qtbot.keyClick(window.output_box.options, Qt.Key.Key_Backspace)
    qtbot.keyClick(window.output_box.options, Qt.Key.Key_Backspace)
    qtbot.keyClick(window.output_box.options, Qt.Key.Key_Backspace)
    qtbot.keyClick(window.output_box.options, Qt.Key.Key_Backspace)
    qtbot.keyClick(window.output_box.options, Qt.Key.Key_Backspace)
    qtbot.keyClick(window.output_box.options, Qt.Key.Key_Backspace)
    assert window.output_box.options_text() == ""
    qtbot.keyClicks(window.output_box.options, "flatten")
    assert window.output_box.error.text() == ""


def test_restore_clears_a_stale_output_box_error(qtbot, make_window, two_pages):
    """Invalid text never changes current_pipeline(), so it makes no undo step of
    its own; check instead that any _restore (here, undoing an unrelated change)
    also clears a stale error, rather than leaving it stuck on screen."""
    window = make_window([str(two_pages)])
    window.output_box.options.setFocus()
    qtbot.keyClicks(window.output_box.options, "banana")
    assert window.output_box.error.text() != ""
    window.add_stage()
    window.undo()
    assert window.output_box.error.text() == ""


def test_follow_without_output_reports_and_stays_off(make_window, two_pages):
    window = make_window([str(two_pages)])
    stage = window.boxes[0]
    stage.follow.setChecked(True)
    window.toggle_follow(stage)
    assert not stage.follow.isChecked()
    assert "no output to follow" in window.statusBar().currentMessage()


def test_follow_from_the_inputs_strip_reports(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    window.inputs_box.strip.setFocus()
    qtbot.waitUntil(lambda: _focus_widget_is(window, window.inputs_box.strip))
    assert window.current is window.inputs_box
    assert not window.actions_by_id["follow_stage"].isEnabled()
    window.follow_focused()
    assert "Focus a stage" in window.statusBar().currentMessage()
    assert window.inputs_box.follow.isHidden()


def test_follow_when_no_viewer_opens_it_stays_off(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    stage = window.boxes[0]
    _run_args(qtbot, window, stage, "1")
    window.open_url = lambda p: False
    window.toggle_follow(stage)
    assert not stage.follow.isChecked() and stage not in window.followed
    assert "No viewer could open" in window.statusBar().currentMessage()


def test_follow_reports_a_file_the_viewer_holds_locked(qtbot, make_window, tmp_path, monkeypatch):
    src = _make_pdf(tmp_path / "three.pdf", [100, 200, 300])
    window = make_window([str(src)])
    stage = window.boxes[0]
    _run_args(qtbot, window, stage, "1")
    window.open_url = lambda p: True
    window.toggle_follow(stage)
    live = window.followed[stage][0]

    copy = shutil.copyfile

    def locked(src, dst):
        if Path(dst) == live:
            raise PermissionError("in use")
        return copy(src, dst)

    monkeypatch.setattr(shutil, "copyfile", locked)
    _run_args(qtbot, window, stage, "2")
    assert "Could not update" in window.statusBar().currentMessage()
    assert _pages(live) == [(100, 0)]
    monkeypatch.undo()
    _run_args(qtbot, window, stage, "3")
    assert _pages(live) == [(300, 0)]


def test_deleting_a_followed_stage_stops_following(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    stage = window.boxes[0]
    _run_args(qtbot, window, stage, "1")
    window.open_url = lambda p: True
    window.toggle_follow(stage)
    fire(qtbot, "focus_args")
    fire(qtbot, "delete_stage")
    assert window.followed == {}


def test_repeated_view_of_unchanged_output_reuses_the_snapshot(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    stage = window.boxes[0]
    _run_args(qtbot, window, stage, "1")
    opened = []
    window.open_url = lambda p: opened.append(p) or True
    window.view_output(stage)
    stamp = opened[0].stat().st_mtime_ns
    window.view_output(stage)
    assert opened[1] == opened[0]
    assert opened[0].stat().st_mtime_ns == stamp


def test_save_asks_for_a_prompt_output_password(qtbot, make_window, tmp_path):
    window = make_window([])
    window.boxes[0].op.setCurrentText("create")
    window.boxes[0].args.setText("1")
    window.output_box.set_options_text("owner_pw PROMPT")
    target = tmp_path / "locked.pdf"
    window.ask_save_path = lambda *a, **k: str(target)
    asked = []
    window.ask_password = lambda _parent, _title, label: asked.append(label) or "ownerpw"
    window.save()
    qtbot.waitUntil(lambda: "Saved" in window.statusBar().currentMessage(), timeout=30000)
    assert asked == ["owner password for the saved PDF:"]
    with pikepdf.open(target, password="ownerpw") as pdf:
        assert pdf.is_encrypted
        assert pdf.owner_password_matched
    assert window.save_target == target
    assert window.current_pipeline().output_options == "owner_pw PROMPT"


def test_cancelling_the_output_password_cancels_the_save(make_window, tmp_path):
    window = make_window([])
    window.boxes[0].op.setCurrentText("create")
    window.output_box.set_options_text("user_pw PROMPT")
    target = tmp_path / "never.pdf"
    window.ask_save_path = lambda *a, **k: str(target)
    window.ask_password = lambda *_args: None
    window.save()
    assert window.saver is None or not window.saver.isRunning()
    assert window.save_target is None
    assert not target.exists()


def test_save_only_asks_for_the_prompted_output_password(qtbot, make_window, tmp_path):
    window = make_window([])
    window.boxes[0].op.setCurrentText("create")
    window.output_box.set_options_text("owner_pw known user_pw PROMPT")
    target = tmp_path / "both.pdf"
    window.ask_save_path = lambda *a, **k: str(target)
    asked = []
    window.ask_password = lambda _parent, _title, label: asked.append(label) or "userpw"
    window.save()
    qtbot.waitUntil(lambda: "Saved" in window.statusBar().currentMessage(), timeout=30000)
    assert asked == ["user password for the saved PDF:"]
    with pikepdf.open(target, password="known") as pdf:
        assert pdf.owner_password_matched
    with pikepdf.open(target, password="userpw") as pdf:
        assert pdf.user_password_matched and not pdf.owner_password_matched


def test_open_url_uses_the_system_viewer_until_another_is_chosen(
    make_window, monkeypatch, tmp_path
):
    from pdftl.gui import viewers, widgets
    from pdftl.gui.viewer_dialog import save_viewer

    window = make_window([])
    calls = []
    monkeypatch.setattr(
        window_outputs, "open_in_system_viewer", lambda p: calls.append(("system", p)) or True
    )
    monkeypatch.setattr(viewers, "launch", lambda v, p: calls.append((v.name, p)) or False)
    assert window.open_url(tmp_path / "a.pdf") is True
    save_viewer(widgets.settings_factory(), viewers.Viewer("zathura", ("zathura", viewers.FILE)))
    assert window.open_url(tmp_path / "b.pdf") is False
    assert calls == [("system", tmp_path / "a.pdf"), ("zathura", tmp_path / "b.pdf")]


def test_choose_viewer_saves_an_accepted_choice_only(make_window):
    from pdftl.gui import viewers, widgets
    from pdftl.gui.viewer_dialog import load_viewer

    window = make_window([])
    okular = viewers.Viewer("Okular", ("okular", viewers.FILE))
    window.discover_viewers = lambda: [okular]
    shown = []

    def pick(accept):
        def run(dialog):
            shown.append([dialog.choice.itemText(i) for i in range(dialog.choice.count())])
            dialog.choice.setCurrentIndex(1)
            return accept

        return run

    window.run_dialog = pick(0)
    window.actions_by_id["choose_viewer"].trigger()
    assert load_viewer(widgets.settings_factory()) is viewers.SYSTEM
    window.run_dialog = pick(1)
    window.choose_viewer()
    assert load_viewer(widgets.settings_factory()) == okular
    assert window.statusBar().currentMessage() == "PDF viewer: Okular"
    assert shown[0] == ["System viewer", "Okular", "Other program…"]


def test_ctrl_shift_o_view_stage(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    stage1 = window.boxes[0]
    fire(qtbot, "focus_args")
    type_text(qtbot, "1")
    qtbot.waitUntil(lambda: "page" in stage1.status.text(), timeout=15000)
    captured = {}
    window.open_url = lambda p: captured.setdefault("path", p) or True
    fire(qtbot, "view_stage")
    assert _pages(captured["path"]) == _pages(stage1.path)


def test_ctrl_shift_e_export_stage(qtbot, make_window, two_pages, tmp_path):
    window = make_window([str(two_pages)])
    stage1 = window.boxes[0]
    fire(qtbot, "focus_args")
    type_text(qtbot, "1")
    qtbot.waitUntil(lambda: "page" in stage1.status.text(), timeout=15000)
    target = tmp_path / "exported.pdf"
    window.ask_save_path = lambda *a, **k: str(target)
    fire(qtbot, "export_stage")
    assert _pages(target) == _pages(stage1.path)


def test_enter_on_strip_opens_preview_esc_closes(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    stage1 = window.boxes[0]
    fire(qtbot, "focus_args")
    type_text(qtbot, "1-2")
    qtbot.waitUntil(lambda: "page" in stage1.status.text(), timeout=15000)
    fire(qtbot, "focus_pages")
    qtbot.keyClick(stage1.strip, Qt.Key.Key_Home)
    assert window.preview is None
    fire(qtbot, "preview_page")
    assert window.preview is not None
    qtbot.waitExposed(window.preview)
    qtbot.keyClick(window.preview, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: window.preview is None, timeout=5000)


def test_ctrl_shift_c_copy_cli(qtbot, make_window, two_pages, tmp_path, run_pdftl):
    window = make_window([str(two_pages)])
    stage1 = window.boxes[0]
    fire(qtbot, "focus_args")
    type_text(qtbot, "1")
    qtbot.waitUntil(lambda: "page" in stage1.status.text(), timeout=15000)
    fire(qtbot, "copy_cli")
    clipboard_text = QGuiApplication.clipboard().text()
    tokens = shlex.split(clipboard_text)[1:]
    out = tmp_path / "copied.pdf"
    run_pdftl([*tokens, "output", str(out)])
    assert _pages(out) == _pages(stage1.path)
