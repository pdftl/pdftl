import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _fake_gui_settings(monkeypatch, tmp_path):
    """Keep widgets.settings_factory off the user's real QSettings during tests."""
    try:
        from PySide6.QtCore import QSettings
    except ImportError:
        return
    from pdftl.gui import widgets

    ini_path = str(tmp_path / "test_settings.ini")
    monkeypatch.setattr(
        widgets, "settings_factory", lambda: QSettings(ini_path, QSettings.Format.IniFormat)
    )


@pytest.fixture(autouse=True)
def _posix_shell_style(monkeypatch):
    """Typed text in these tests uses POSIX quoting; tests of Windows quoting set it."""
    from pdftl.gui import shell_style

    monkeypatch.setattr(shell_style, "_WINDOWS", False)


@pytest.fixture
def make_window(qtbot, tmp_path):
    """Build a shown, activated MainWindow; each is really closed at teardown.

    A test may make close() refuse (a "cancel" discard answer); the window
    would then be destroyed later with its runner still going, which aborts.
    """
    from pdftl.gui.engine import SubprocessEngine
    from pdftl.gui.main_window import MainWindow

    counter = {"n": 0}
    windows = []

    def _make(files=(), engine=None):
        counter["n"] += 1
        eng = engine or SubprocessEngine(cache_dir=tmp_path / f"cache{counter['n']}")
        window = MainWindow(list(files), engine=eng)
        window.ask_password = lambda *_args: ""
        window.ask_discard_pipeline = lambda *_args: "discard"
        qtbot.addWidget(window)
        windows.append(window)
        window.show()
        qtbot.waitExposed(window)
        with qtbot.waitActive(window, timeout=2000):
            window.activateWindow()
        return window

    yield _make
    for window in windows:
        window.ask_discard_pipeline = lambda *_args: "discard"
        window.close()
        window.stop_runner()


@pytest.fixture
def two_pages(tmp_path):
    from tests.gui.window_support import _make_pdf

    return _make_pdf(tmp_path / "a.pdf", [100, 200])
