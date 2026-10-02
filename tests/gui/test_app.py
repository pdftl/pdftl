# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/gui/test_app.py

"""Tests for app module: starting the GUI (`pdftl gui` / `python -m pdftl.gui`)."""

import pytest

pytest.importorskip("PySide6")

import pdftl.gui.app as app_module
from PySide6.QtWidgets import QApplication


class _FakeWindow:
    """Stands in for MainWindow: records what `run` did to it, without
    starting any real engine or thumbnail thread."""

    instances: list["_FakeWindow"] = []

    def __init__(self, files):
        self.files_arg = files
        self.resized = None
        self.shown = False
        self.closed = False
        self.loaded_pipeline_path = None
        _FakeWindow.instances.append(self)

    def resize(self, w, h):
        self.resized = (w, h)

    def show(self):
        self.shown = True

    def close(self):
        self.closed = True

    def _load_pipeline_file(self, path):
        self.loaded_pipeline_path = path


@pytest.fixture(autouse=True)
def _fake_window(monkeypatch):
    _FakeWindow.instances = []
    monkeypatch.setattr(app_module, "MainWindow", _FakeWindow)
    yield
    _FakeWindow.instances = []


def test_run_without_exec_creates_window_shows_and_closes_it():
    code = app_module.run(["a.pdf", "b.pdf"], exec_app=False)

    assert code == 0
    assert len(_FakeWindow.instances) == 1
    window = _FakeWindow.instances[0]
    assert window.files_arg == ["a.pdf", "b.pdf"]
    assert window.resized == (1100, 900)
    assert window.shown is True
    assert window.closed is True


def test_run_defaults_to_no_files():
    app_module.run(exec_app=False)

    window = _FakeWindow.instances[0]
    assert window.files_arg == []


def test_run_with_exec_app_runs_event_loop_and_returns_its_code(monkeypatch):
    monkeypatch.setattr(QApplication, "exec", lambda self: 3)
    watched = []
    monkeypatch.setattr(app_module, "close_on_sigint", watched.append)

    code = app_module.run(["a.pdf"], exec_app=True)

    assert code == 3
    window = _FakeWindow.instances[0]
    assert watched == [window]
    assert window.shown is True
    assert window.closed is False  # only the exec_app=False path closes it itself


def test_run_reuses_an_existing_qapplication_instance(qtbot):
    before = QApplication.instance()
    assert before is not None

    app_module.run(["a.pdf"], exec_app=False)

    assert QApplication.instance() is before


def test_main_exits_with_runs_return_code(monkeypatch):
    monkeypatch.setattr(app_module.sys, "argv", ["pdftl", "one.pdf", "two.pdf"])
    seen_args = {}

    def fake_run(files, exec_app=True):
        seen_args["files"] = files
        return 5

    monkeypatch.setattr(app_module, "run", fake_run)

    with pytest.raises(SystemExit) as exc_info:
        app_module.main()

    assert exc_info.value.code == 5
    assert seen_args["files"] == ["one.pdf", "two.pdf"]


def test_python_dash_m_runs_main(monkeypatch):
    import runpy

    import pdftl.gui.app as app_module

    calls = []
    monkeypatch.setattr(app_module, "main", lambda: calls.append(True))
    runpy.run_module("pdftl.gui", run_name="__main__")
    assert calls == [True]


def test_run_sets_the_application_icon():
    app_module.run([], exec_app=False)
    icon = QApplication.instance().windowIcon()
    assert not icon.isNull()
    assert icon.pixmap(32, 32).toImage() == app_module.app_icon().pixmap(32, 32).toImage()


@pytest.mark.parametrize("name", ["pipe.yaml", "pipe.yml", "PIPE.YAML"])
def test_a_single_yaml_argument_opens_as_a_pipeline_not_an_input(name):
    from pathlib import Path

    app_module.run([name], exec_app=False)

    window = _FakeWindow.instances[0]
    assert window.files_arg == []
    assert window.loaded_pipeline_path == Path(name)


def test_multiple_files_are_never_treated_as_a_pipeline_even_if_one_is_yaml():
    app_module.run(["a.pdf", "pipe.yaml"], exec_app=False)

    window = _FakeWindow.instances[0]
    assert window.files_arg == ["a.pdf", "pipe.yaml"]
    assert window.loaded_pipeline_path is None


def test_a_single_pdf_argument_is_not_treated_as_a_pipeline():
    app_module.run(["a.pdf"], exec_app=False)

    window = _FakeWindow.instances[0]
    assert window.files_arg == ["a.pdf"]
    assert window.loaded_pipeline_path is None


def test_ctrl_c_closes_the_window_and_a_second_one_is_fatal(qtbot, tmp_path):
    import signal

    from pdftl.gui.engine import SubprocessEngine
    from pdftl.gui.main_window import MainWindow

    previous = signal.getsignal(signal.SIGINT)
    window = MainWindow([], engine=SubprocessEngine(cache_dir=tmp_path))
    qtbot.addWidget(window)
    window.show()
    try:
        app_module.close_on_sigint(window)
        handler = signal.getsignal(signal.SIGINT)
        assert callable(handler)
        assert any(t.isActive() for t in window.findChildren(app_module.QTimer))
        handler(signal.SIGINT, None)
        assert not window.isVisible()
        assert signal.getsignal(signal.SIGINT) is signal.SIG_DFL
    finally:
        signal.signal(signal.SIGINT, previous)
