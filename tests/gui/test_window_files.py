"""Tests for window_files: new, open and save pipeline files."""

import pytest

pytest.importorskip("PySide6")


from pdftl.gui import pipeline_file
from pdftl.gui.interfaces import InputFile, Pipeline, Stage
from pdftl.gui.output_box import NO_TARGET
from tests.gui.window_support import _make_pdf


def test_open_pipeline_is_one_undo_step(make_window, tmp_path, two_pages):
    window = make_window([])
    saved = tmp_path / "pipe.yaml"
    pipeline_file.save(
        Pipeline(inputs=(InputFile("A", two_pages),), stages=(Stage("cat", "1"),)),
        saved,
        None,
        cwd=tmp_path,
    )
    window.ask_pipeline_open_path = lambda *a, **k: str(saved)
    before = window.history.can_undo()
    window.open_pipeline()
    assert before is False
    assert window.pipeline.inputs[0].path == two_pages
    assert [b.stage().op for b in window.boxes] == ["cat"]
    assert window.pipeline_path == saved
    assert window.history.can_undo()
    window.undo()
    assert window.pipeline.inputs == ()


def test_open_pipeline_cancelled_dialog_is_a_noop(make_window):
    window = make_window([])
    window.ask_pipeline_open_path = lambda *a, **k: ""
    window.open_pipeline()
    assert window.pipeline_path is None


def test_open_pipeline_malformed_file_warns_and_leaves_pipeline_unchanged(make_window, tmp_path):
    window = make_window([])
    bad = tmp_path / "bad.yaml"
    bad.write_text("key: value\n")
    window.ask_pipeline_open_path = lambda *a, **k: str(bad)
    before = window.current_pipeline()
    window.open_pipeline()
    assert window.pipeline_path is None
    assert window.current_pipeline() == before
    assert "Could not open" in window.statusBar().currentMessage()


def test_open_pipeline_prompts_for_an_unknown_password(make_window, tmp_path):
    enc = _make_pdf(tmp_path / "enc.pdf", [10], password="secret")
    window = make_window([])
    saved = tmp_path / "pipe.yaml"
    pipeline_file.save(
        Pipeline(inputs=(InputFile("A", enc, "secret"),), stages=(Stage("cat", "1"),)),
        saved,
        None,
        cwd=tmp_path,
    )
    window.ask_pipeline_open_path = lambda *a, **k: str(saved)
    window.ask_password = lambda *a, **k: "secret"
    window.open_pipeline()
    assert window.pipeline.inputs[0].password == "secret"


def test_open_pipeline_asks_to_discard_first_when_dirty(make_window, tmp_path, two_pages):
    window = make_window([str(two_pages)])
    asked = []
    window.ask_discard_pipeline = lambda *a: asked.append(1) or "cancel"
    window.ask_pipeline_open_path = lambda *a, **k: (_ for _ in ()).throw(AssertionError)
    window.open_pipeline()
    assert asked == [1]
    assert window.pipeline_path is None


def test_new_pipeline_resets_to_one_empty_stage(make_window, tmp_path, two_pages):
    window = make_window([str(two_pages)])
    window.boxes[0].args.setText("1-2")
    window.schedule()
    window.pipeline_path = tmp_path / "pipe.yaml"
    can_undo_before = window.history.can_undo()
    window.new_pipeline()
    assert can_undo_before is True
    assert window.pipeline.inputs == ()
    assert [b.stage() for b in window.boxes] == [Stage("cat")]
    assert window.save_target is None
    assert window.pipeline_path is None
    assert "New pipeline" in window.statusBar().currentMessage()
    assert window.output_box.target.text() == NO_TARGET


def test_new_pipeline_is_one_undo_step(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.boxes[0].args.setText("1-2")
    window.schedule()
    window.new_pipeline()
    window.undo()
    assert window.pipeline.inputs[0].path == two_pages
    assert window.boxes[0].args.text() == "1-2"


def test_new_pipeline_asks_to_discard_first_when_dirty(make_window, two_pages):
    window = make_window([str(two_pages)])
    asked = []
    window.ask_discard_pipeline = lambda *a: asked.append(1) or "cancel"
    window.new_pipeline()
    assert asked == [1]
    assert window.pipeline.inputs  # untouched


def test_save_pipeline_with_no_path_yet_prompts_like_save_as(make_window, tmp_path, two_pages):
    window = make_window([str(two_pages)])
    target = tmp_path / "chosen.yaml"
    window.ask_pipeline_save_path = lambda *a, **k: str(target)
    assert window.save_pipeline() is True
    assert window.pipeline_path == target
    assert target.exists()
    assert "cat" in target.read_text()


def test_save_pipeline_reuses_its_path_without_prompting(make_window, tmp_path, two_pages):
    window = make_window([str(two_pages)])
    window.pipeline_path = tmp_path / "existing.yaml"
    window.ask_pipeline_save_path = lambda *a, **k: (_ for _ in ()).throw(AssertionError)
    assert window.save_pipeline() is True
    assert window.pipeline_path.exists()


def test_save_pipeline_as_cancelled_dialog_is_a_noop(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.ask_pipeline_save_path = lambda *a, **k: ""
    assert window.save_pipeline_as() is False
    assert window.pipeline_path is None


def test_save_pipeline_as_default_name_is_the_current_pipeline_path(make_window, tmp_path):
    window = make_window([])
    window.pipeline_path = tmp_path / "current.yaml"
    seen = {}
    window.ask_pipeline_save_path = lambda parent, default: seen.setdefault("default", default)
    window.save_pipeline_as()
    assert seen["default"] == str(tmp_path / "current.yaml")


def test_save_pipeline_reports_an_oserror(make_window, tmp_path, two_pages, monkeypatch):
    window = make_window([str(two_pages)])
    window.ask_pipeline_save_path = lambda *a, **k: str(tmp_path / "out.yaml")

    def fail(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(pipeline_file, "save", fail)
    assert window.save_pipeline() is False
    assert "Could not save" in window.statusBar().currentMessage()
    assert window.pipeline_path is None


def test_title_shows_pdftl_and_no_asterisk_for_a_fresh_empty_window(make_window):
    window = make_window([])
    assert window.windowTitle() == "pdftl[*]"
    assert not window.isWindowModified()


def test_title_marks_modified_once_a_pipeline_has_content_and_is_unsaved(make_window, two_pages):
    window = make_window([str(two_pages)])
    assert window.isWindowModified()


def test_title_clears_modified_after_saving_and_shows_the_file_name(
    make_window, tmp_path, two_pages
):
    window = make_window([str(two_pages)])
    target = tmp_path / "mypipe.yaml"
    window.ask_pipeline_save_path = lambda *a, **k: str(target)
    window.save_pipeline()
    assert not window.isWindowModified()
    assert window.windowTitle() == "mypipe.yaml[*]"


def test_title_marks_modified_again_after_a_further_edit(make_window, tmp_path, two_pages):
    window = make_window([str(two_pages)])
    window.ask_pipeline_save_path = lambda *a, **k: str(tmp_path / "p.yaml")
    window.save_pipeline()
    assert not window.isWindowModified()
    window.add_stage()
    assert window.isWindowModified()


def test_title_clears_modified_after_opening_a_pipeline(make_window, tmp_path, two_pages):
    window = make_window([])
    saved = tmp_path / "pipe.yaml"
    pipeline_file.save(
        Pipeline(inputs=(InputFile("A", two_pages),), stages=(Stage("cat", "1"),)),
        saved,
        None,
        cwd=tmp_path,
    )
    window.ask_pipeline_open_path = lambda *a, **k: str(saved)
    window.open_pipeline()
    assert not window.isWindowModified()


def test_confirm_discard_does_not_ask_for_an_empty_never_saved_pipeline(make_window):
    window = make_window([])
    window.ask_discard_pipeline = lambda *a: (_ for _ in ()).throw(AssertionError)
    assert window._confirm_discard_pipeline() is True


def test_confirm_discard_save_choice_saves_then_proceeds(make_window, tmp_path, two_pages):
    window = make_window([str(two_pages)])
    window.ask_discard_pipeline = lambda *a: "save"
    target = tmp_path / "p.yaml"
    window.ask_pipeline_save_path = lambda *a, **k: str(target)
    assert window._confirm_discard_pipeline() is True
    assert target.exists()


def test_confirm_discard_save_choice_cancelled_dialog_blocks(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.ask_discard_pipeline = lambda *a: "save"
    window.ask_pipeline_save_path = lambda *a, **k: ""
    assert window._confirm_discard_pipeline() is False


def test_confirm_discard_discard_choice_proceeds_without_saving(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.ask_discard_pipeline = lambda *a: "discard"
    assert window._confirm_discard_pipeline() is True
    assert window.pipeline_path is None


def test_confirm_discard_cancel_choice_blocks(make_window, two_pages):
    window = make_window([str(two_pages)])
    window.ask_discard_pipeline = lambda *a: "cancel"
    assert window._confirm_discard_pipeline() is False
