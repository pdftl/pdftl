"""Tests for window_dialogs: the default dialog functions."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QInputDialog

from pdftl.gui import window_dialogs


def test_default_ask_pipeline_open_path(monkeypatch):
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("chosen.yaml", ""))
    )
    assert window_dialogs.ask_pipeline_open_path(None) == "chosen.yaml"


def test_default_ask_pipeline_save_path_accepted(qtbot, monkeypatch, tmp_path):
    chosen = str(tmp_path / "chosen.yaml")
    monkeypatch.setattr(
        QFileDialog, "exec", lambda self: window_dialogs.QDialog.DialogCode.Accepted
    )
    monkeypatch.setattr(QFileDialog, "selectedFiles", lambda self: [chosen])
    assert window_dialogs.ask_pipeline_save_path(None, "default.yaml") == chosen


def test_default_ask_pipeline_save_path_cancelled(qtbot, monkeypatch):
    monkeypatch.setattr(
        QFileDialog, "exec", lambda self: window_dialogs.QDialog.DialogCode.Rejected
    )
    assert window_dialogs.ask_pipeline_save_path(None, "default.yaml") == ""


def test_default_ask_pipeline_save_path_accepted_with_no_files_selected(qtbot, monkeypatch):
    monkeypatch.setattr(
        QFileDialog, "exec", lambda self: window_dialogs.QDialog.DialogCode.Accepted
    )
    monkeypatch.setattr(QFileDialog, "selectedFiles", lambda self: [])
    assert window_dialogs.ask_pipeline_save_path(None, "default.yaml") == ""


def test_default_ask_discard_pipeline_maps_clicked_button_to_choice(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    for label, expected in (("Save", "save"), ("Discard", "discard"), ("Cancel", "cancel")):
        monkeypatch.setattr(QMessageBox, "exec", lambda self: None)
        monkeypatch.setattr(
            QMessageBox,
            "clickedButton",
            lambda self, label=label: next(b for b in self.buttons() if b.text() == label),
        )
        assert window_dialogs.ask_discard_pipeline(None) == expected


def test_default_ask_discard_pipeline_without_a_click_cancels(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "exec", lambda self: None)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: None)
    assert window_dialogs.ask_discard_pipeline(None) == "cancel"


def test_default_ask_save_path(monkeypatch):
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("chosen.pdf", ""))
    )
    assert window_dialogs.ask_save_path(None, "title", "default", "filter") == "chosen.pdf"


def test_default_ask_open_paths(monkeypatch):
    monkeypatch.setattr(
        QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: (["a.pdf", "b.pdf"], ""))
    )
    assert window_dialogs.ask_open_paths(None) == ["a.pdf", "b.pdf"]


def test_default_ask_text_accepted(monkeypatch):
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("hi", True)))
    assert window_dialogs.ask_text(None, "title", "label") == "hi"


def test_default_ask_text_cancelled(monkeypatch):
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("hi", False)))
    assert window_dialogs.ask_text(None, "title", "label") == ""


def test_default_open_url(monkeypatch, tmp_path):
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda url: True))
    assert window_dialogs.open_in_system_viewer(tmp_path / "x.pdf") is True


@pytest.mark.parametrize(("ok", "expected"), [(True, "pw"), (False, None)])
def test_default_ask_password_uses_password_echo(monkeypatch, ok, expected):
    seen = []

    def fake_get_text(*args, **kwargs):
        seen.append(args[3])
        return "pw", ok

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(fake_get_text))
    assert window_dialogs.ask_password(None, "title", "label") == expected
    assert seen == [window_dialogs.QLineEdit.EchoMode.Password]
