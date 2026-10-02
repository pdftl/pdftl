# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/gui/test_viewer_dialog.py

"""Tests for viewer_dialog: picking a viewer and keeping it in the settings."""

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings

from pdftl.gui.viewer_dialog import (
    COMMAND_KEY,
    NAME_KEY,
    OTHER,
    ViewerDialog,
    load_viewer,
    save_viewer,
)
from pdftl.gui.viewers import FILE, SYSTEM, Viewer

EVINCE = Viewer("Document Viewer", ("evince", FILE))
OKULAR = Viewer("Okular", ("okular", FILE))


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)


def _reread(settings):
    settings.sync()
    return QSettings(settings.fileName(), QSettings.Format.IniFormat)


def test_no_saved_viewer_means_the_system_viewer(settings):
    assert load_viewer(settings) is SYSTEM


@pytest.mark.parametrize(
    "viewer", [EVINCE, Viewer("x", ("/opt/My Viewer/v", FILE)), Viewer("one", ("solo",))]
)
def test_a_saved_viewer_reads_back_the_same(settings, viewer):
    save_viewer(settings, viewer)
    assert load_viewer(_reread(settings)) == viewer


def test_the_ini_holds_a_readable_name_and_command(settings):
    save_viewer(settings, EVINCE)
    settings.sync()
    text = Path(settings.fileName()).read_text(encoding="utf-8")
    assert "name=Document Viewer" in text
    assert "command=evince, {file}" in text


def test_saving_the_system_viewer_forgets_the_program(settings):
    save_viewer(settings, EVINCE)
    save_viewer(settings, SYSTEM)
    again = _reread(settings)
    assert not again.contains(NAME_KEY) and not again.contains(COMMAND_KEY)
    assert load_viewer(again) is SYSTEM


def test_a_command_without_a_name_is_named_by_its_program(settings):
    settings.setValue(COMMAND_KEY, ["zathura", FILE])
    assert load_viewer(_reread(settings)) == Viewer("zathura", ("zathura", FILE))


def _dialog(qtbot, current=SYSTEM, found=(EVINCE, OKULAR)):
    dialog = ViewerDialog(current, list(found))
    qtbot.addWidget(dialog)
    return dialog


def test_the_list_is_system_then_found_then_other(qtbot):
    dialog = _dialog(qtbot)
    items = [dialog.choice.itemText(i) for i in range(dialog.choice.count())]
    assert items == ["System viewer", "Document Viewer", "Okular", OTHER]
    assert dialog.viewer() is SYSTEM
    assert not dialog.program.isEnabled() and not dialog.browse.isEnabled()
    assert dialog.ok.isEnabled()


def test_a_found_viewer_is_preselected_and_chosen(qtbot):
    dialog = _dialog(qtbot, current=OKULAR)
    assert dialog.choice.currentText() == "Okular"
    dialog.choice.setCurrentIndex(1)
    assert dialog.viewer() == EVINCE


def test_other_needs_a_program_path(qtbot):
    dialog = _dialog(qtbot)
    dialog.choice.setCurrentIndex(3)
    assert dialog.program.isEnabled() and dialog.browse.isEnabled()
    assert not dialog.ok.isEnabled()
    dialog.program.setText("  /usr/bin/zathura ")
    assert dialog.ok.isEnabled()
    assert dialog.viewer().command == ("/usr/bin/zathura", FILE)


def test_an_unlisted_current_viewer_shows_as_other_with_its_program(qtbot):
    dialog = _dialog(qtbot, current=Viewer("zathura", ("/usr/bin/zathura", FILE)))
    assert dialog.other()
    assert dialog.program.text() == "/usr/bin/zathura"
    bundle = Viewer("Skim", ("open", "-a", "/Applications/Skim.app", FILE))
    assert _dialog(qtbot, current=bundle).program.text() == "/Applications/Skim.app"


def test_browse_fills_in_the_program_and_cancel_keeps_it(qtbot):
    dialog = _dialog(qtbot)
    dialog.choice.setCurrentIndex(3)
    answers = iter([("/opt/viewer", ""), ("", "")])
    dialog.ask_program = lambda *a, **k: next(answers)
    dialog.browse.click()
    assert dialog.program.text() == "/opt/viewer"
    dialog.browse.click()
    assert dialog.program.text() == "/opt/viewer"
