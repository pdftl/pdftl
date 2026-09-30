"""Tests for window_inputs: input files and their passwords."""

import pikepdf
import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt

from pdftl.gui.interfaces import InputFile, Pipeline
from tests.gui.window_support import _make_pdf


def test_remove_current_input_with_none_selected_is_a_noop(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.input_list.setCurrentRow(-1)
    window.remove_current_input()
    assert len(window.pipeline.inputs) == 1


def test_preview_input_error_shows_cross_status(make_window, tmp_path):
    window = make_window([])
    bad = _make_pdf(tmp_path / "enc.pdf", [10], password="secret")
    window.add_file(str(bad))
    window._preview_input(0)
    assert "✗" in window.inputs_box.status.text()


def test_encrypted_input_prompts_for_password_and_uses_it(make_window, tmp_path):
    window = make_window([])
    answers = iter(["wrong", "secret"])
    prompts = []
    window.ask_password = lambda *args: prompts.append(args[2]) or next(answers)
    enc = _make_pdf(tmp_path / "enc.pdf", [10, 20], password="secret")
    window.add_file(str(enc))
    assert prompts == ["Password for enc.pdf:", "Wrong password. Password for enc.pdf:"]
    assert window.pipeline.inputs[0].password == "secret"
    assert window.inputs_box.status.text() == "A: enc.pdf, 2 pages (owner password)"


def test_declined_password_is_not_asked_again(make_window, tmp_path):
    window = make_window([])
    prompts = []
    window.ask_password = lambda *args: prompts.append(args[2]) or ""
    enc = _make_pdf(tmp_path / "enc.pdf", [10], password="secret")
    window.add_file(str(enc))
    window._preview_input(0)
    assert len(prompts) == 1
    assert "✗" in window.inputs_box.status.text()


def test_non_password_error_does_not_prompt(make_window, tmp_path):
    window = make_window([])
    window.ask_password = lambda *args: pytest.fail("should not prompt")
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf")
    window.add_file(str(broken))
    assert "✗" in window.inputs_box.status.text()


def test_password_for_returns_stored_password(make_window, tmp_path):
    window = make_window([])
    enc = _make_pdf(tmp_path / "enc.pdf", [10], password="secret")
    window.pipeline = Pipeline(inputs=(InputFile("A", enc, "secret"),))
    assert window._password_for(enc) == "secret"
    assert window._password_for(tmp_path / "missing.pdf") is None


def test_set_password_with_no_selection_shows_status(make_window):
    window = make_window([])
    window.set_password()
    assert "Select an input file" in window.statusBar().currentMessage()


def test_set_password_replaces_a_working_password_with_the_owner_one(make_window, tmp_path):
    path = tmp_path / "dual.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(10, 10))
    pdf.save(path, encryption=pikepdf.Encryption(owner="ownerpw", user="userpw"))
    window = make_window([])
    window.ask_password = lambda *_a: "userpw"
    window.add_file(str(path))
    assert window.pipeline.inputs[0].password == "userpw"
    answers = iter(["wrong", "ownerpw"])
    window.ask_password = lambda *_a: next(answers)
    window.set_password()
    assert window.pipeline.inputs[0].password == "ownerpw"
    assert "(owner password)" in window.inputs_box.status.text()


def test_set_password_blank_clears_it_as_an_undo_step(make_window, tmp_path):
    enc = _make_pdf(tmp_path / "enc.pdf", [10], password="secret")
    window = make_window([])
    window.ask_password = lambda *_a: "secret"
    window.add_file(str(enc))
    assert window.pipeline.inputs[0].password == "secret"
    window.ask_password = lambda *_a: ""
    window.set_password()
    assert window.pipeline.inputs[0].password is None
    assert window.history.undo_label() == "change inputs"
    window.undo()
    assert window.pipeline.inputs[0].password == "secret"


def test_set_password_cancel_keeps_the_working_password(make_window, tmp_path):
    enc = _make_pdf(tmp_path / "enc.pdf", [10], password="secret")
    window = make_window([])
    window.ask_password = lambda *_a: "secret"
    window.add_file(str(enc))
    undo_label = window.history.undo_label()
    window.ask_password = lambda *_a: None
    window.set_password()
    assert window.pipeline.inputs[0].password == "secret"
    assert window.history.undo_label() == undo_label


def test_cancelled_password_prompt_is_not_asked_again(make_window, tmp_path):
    window = make_window([])
    prompts = []
    window.ask_password = lambda *args: prompts.append(args[2])
    enc = _make_pdf(tmp_path / "enc.pdf", [10], password="secret")
    window.add_file(str(enc))
    window._preview_input(0)
    assert len(prompts) == 1
    assert window.pipeline.inputs[0].password is None


def test_remove_last_input_clears_strip(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.input_list.setCurrentRow(0)
    window.remove_current_input()
    assert window.pipeline.inputs == ()
    assert window.inputs_box.status.text() == "No input files"


def test_remove_one_of_several_inputs_leaves_the_rest(make_window, tmp_path):
    window = make_window([])
    a = _make_pdf(tmp_path / "a.pdf", [10])
    b = _make_pdf(tmp_path / "b.pdf", [20])
    window.add_file(str(a))
    window.add_file(str(b))
    window.input_list.setCurrentRow(0)
    window.remove_current_input()
    assert [f.handle for f in window.pipeline.inputs] == ["B"]
    assert window.inputs_box.status.text() == "B: b.pdf, 1 page"
    assert window.inputs_box.path == b


def test_the_inputs_box_buttons_add_and_remove_files(qtbot, make_window, two_pages):
    window = make_window([])
    window.ask_open_paths = lambda _parent: [str(two_pages)]
    qtbot.mouseClick(window.add_input_button, Qt.MouseButton.LeftButton)
    assert [f.path for f in window.pipeline.inputs] == [two_pages]
    assert "Delete" in window.remove_input_button.toolTip()
    qtbot.mouseClick(window.remove_input_button, Qt.MouseButton.LeftButton)
    assert window.pipeline.inputs == ()


def test_row_for_path_finds_an_input_by_its_path(make_window, tmp_path):
    a = _make_pdf(tmp_path / "a.pdf", [100])
    b = _make_pdf(tmp_path / "b.pdf", [100])
    window = make_window([str(a), str(b)])
    assert window._row_for_path(b) == 1
    assert window._row_for_path(tmp_path / "c.pdf") is None
