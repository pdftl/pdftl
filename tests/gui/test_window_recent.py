"""Tests for window_recent: recent pipeline and input files."""

import json
from pathlib import Path, PurePosixPath

import pytest

pytest.importorskip("PySide6")

from pdftl.gui import widgets, window_recent
from pdftl.gui.window_recent import (
    RECENT_MAX,
    clear_recent,
    entry_text,
    recent_paths,
    remember,
)
from tests.gui.window_support import _make_pdf


def _files(tmp_path: Path, count: int) -> list[Path]:
    paths = [tmp_path / f"f{i}.pdf" for i in range(count)]
    for p in paths:
        p.write_bytes(b"x")
    return paths


def _submenu(window, title: str):
    file_menu = window.menuBar().actions()[0].menu()
    (action,) = [a for a in file_menu.actions() if a.text() == title]
    menu = action.menu()
    menu.aboutToShow.emit()
    return menu


def _entries(menu) -> list[str]:
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def _trigger(menu, text: str) -> None:
    (action,) = [a for a in menu.actions() if a.text() == text]
    action.trigger()


def test_remember_puts_newest_first_without_duplicates(tmp_path):
    a, b = _files(tmp_path, 2)
    remember("inputs", a)
    remember("inputs", b)
    remember("inputs", a)
    assert recent_paths("inputs") == [a, b]
    assert recent_paths("pipelines") == []


def test_remember_keeps_only_the_newest_few(tmp_path):
    paths = _files(tmp_path, RECENT_MAX + 2)
    for p in paths:
        remember("inputs", p)
    assert recent_paths("inputs") == paths[::-1][:RECENT_MAX]


def test_relative_paths_are_remembered_absolute(tmp_path, monkeypatch):
    (a,) = _files(tmp_path, 1)
    monkeypatch.chdir(tmp_path)
    remember("inputs", Path(a.name))
    monkeypatch.chdir("/")
    assert recent_paths("inputs") == [a]


def test_files_removed_since_are_left_out(tmp_path):
    a, b = _files(tmp_path, 2)
    remember("inputs", a)
    remember("inputs", b)
    b.unlink()
    assert recent_paths("inputs") == [a]


@pytest.mark.parametrize("raw", ["not json", '{"a": 1}'])
def test_unreadable_settings_mean_no_recent_files(raw):
    widgets.settings_factory().setValue("recent/inputs", raw)
    assert recent_paths("inputs") == []


def test_non_string_entries_are_skipped(tmp_path):
    (a,) = _files(tmp_path, 1)
    widgets.settings_factory().setValue("recent/inputs", json.dumps([3, str(a)]))
    assert recent_paths("inputs") == [a]


def test_clear_recent_forgets_only_that_kind(tmp_path):
    a, b = _files(tmp_path, 2)
    remember("inputs", a)
    remember("pipelines", b)
    clear_recent("inputs")
    assert recent_paths("inputs") == []
    assert recent_paths("pipelines") == [b]


def test_entry_text_numbers_entries_and_escapes_ampersands():
    assert entry_text(1, PurePosixPath("/d/a.pdf")) == "&1 a.pdf  (/d)"
    assert entry_text(10, PurePosixPath("/d/b.pdf")) == "&0 b.pdf  (/d)"
    assert entry_text(2, PurePosixPath("/R&D/x&y.pdf")) == "&2 x&&y.pdf  (/R&&D)"


def test_empty_submenus_say_so_and_cannot_be_cleared(make_window):
    window = make_window([])
    for title in ("Open &Recent Pipeline", "Add Recent &Input File"):
        menu = _submenu(window, title)
        entries = [a for a in menu.actions() if not a.isSeparator()]
        assert [a.text() for a in entries][0] == "(none)"
        assert not any(a.isEnabled() for a in entries)
        assert menu.style() is window.menu_style


def test_added_inputs_are_offered_again(make_window, tmp_path):
    pdf = _make_pdf(tmp_path / "in.pdf", [10])
    window = make_window([])
    window.add_file(str(pdf))
    menu = _submenu(window, "Add Recent &Input File")
    assert _entries(menu) == [entry_text(1, pdf), "&Clear Recent Input Files"]
    assert menu.actions()[0].statusTip() == str(pdf)
    _trigger(menu, entry_text(1, pdf))
    assert [f.path for f in window.pipeline.inputs] == [pdf, pdf]


def test_clearing_recent_inputs_empties_the_submenu(make_window, tmp_path):
    pdf = _make_pdf(tmp_path / "in.pdf", [10])
    window = make_window([])
    window.add_file(str(pdf))
    _trigger(_submenu(window, "Add Recent &Input File"), "&Clear Recent Input Files")
    assert _entries(_submenu(window, "Add Recent &Input File"))[0] == "(none)"


def test_saved_and_opened_pipelines_are_offered_again(make_window, two_pages, tmp_path):
    first = tmp_path / "first.yaml"
    window = make_window([str(two_pages)])
    assert window._write_pipeline(first)
    other = make_window([])
    menu = _submenu(other, "Open &Recent Pipeline")
    assert _entries(menu) == [entry_text(1, first), "&Clear Recent Pipelines"]
    _trigger(menu, entry_text(1, first))
    assert other.pipeline_path == first
    assert [f.path for f in other.pipeline.inputs] == [two_pages]


def test_opening_a_pipeline_file_remembers_it(make_window, two_pages, tmp_path):
    saved = tmp_path / "p.yaml"
    make_window([str(two_pages)])._write_pipeline(saved)
    clear_recent("pipelines")
    make_window([])._load_pipeline_file(saved)
    assert recent_paths("pipelines") == [saved]


def test_declining_to_discard_keeps_the_current_pipeline(make_window, two_pages, tmp_path):
    saved = tmp_path / "p.yaml"
    make_window([str(two_pages)])._write_pipeline(saved)
    window = make_window([str(two_pages)])
    window.ask_discard_pipeline = lambda *a: "cancel"
    _trigger(_submenu(window, "Open &Recent Pipeline"), entry_text(1, saved))
    assert window.pipeline_path is None


def test_clearing_recent_pipelines_leaves_inputs(make_window, two_pages, tmp_path):
    window = make_window([str(two_pages)])
    window._write_pipeline(tmp_path / "p.yaml")
    _trigger(_submenu(window, "Open &Recent Pipeline"), "&Clear Recent Pipelines")
    assert window_recent.recent_paths("pipelines") == []
    assert window_recent.recent_paths("inputs") == [two_pages]
