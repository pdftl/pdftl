# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/gui/test_output_box.py

"""Tests for output_box: the Output pseudo-stage widget."""

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt

from pdftl.gui.output_box import NO_TARGET, OutputBox


@pytest.fixture
def box(qtbot):
    b = OutputBox(["flatten", "uncompress", "owner_pw"])
    qtbot.addWidget(b)
    return b


def test_starts_with_no_target_and_empty_options(box):
    assert box.target.text() == NO_TARGET
    assert box.options_text() == ""
    assert box.error.text() == ""


def test_set_target_shows_the_path(box):
    box.set_target(Path("out.pdf"))
    assert "out.pdf" in box.target.text()
    box.set_target(None)
    assert box.target.text() == NO_TARGET


def test_set_and_get_options_text(box):
    box.set_options_text("uncompress flatten")
    assert box.options_text() == "uncompress flatten"
    assert box.options.text() == "uncompress flatten"


def test_set_error_shows_and_clears(box):
    box.set_error("not a recognized output option: 'x'")
    assert "not a recognized" in box.error.text()
    box.set_error("")
    assert box.error.text() == ""


def test_set_stale_shows_and_hides_the_note(box):
    assert not box.stale.isVisibleTo(box)
    box.set_stale("Stage 1 (cat) failed")
    assert box.stale.isVisibleTo(box)
    assert box.stale.text() == "Stage 1 (cat) failed"
    assert "color: #d0312d" in box.stale.styleSheet()
    box.set_stale("")
    assert not box.stale.isVisibleTo(box)


def test_set_following_checks_the_button(box):
    box.set_following(True)
    assert box.follow.isChecked()
    box.set_following(False)
    assert not box.follow.isChecked()


def test_completer_offers_the_given_option_names(box):
    model = box.options.completer().model()
    names = [model.index(i, 0).data() for i in range(model.rowCount())]
    assert set(names) == {"flatten", "uncompress", "owner_pw"}


def test_typing_emits_options_edited(qtbot, box):
    box.show()
    box.options.setFocus()
    with qtbot.waitSignal(box.options_edited, timeout=1000):
        qtbot.keyClicks(box.options, "f")
    assert box.options_text() == "f"


def test_save_button_emits_save_requested(qtbot, box):
    with qtbot.waitSignal(box.save_requested, timeout=1000):
        qtbot.mouseClick(box.save, Qt.MouseButton.LeftButton)


def test_follow_button_emits_follow_requested(qtbot, box):
    with qtbot.waitSignal(box.follow_requested, timeout=1000):
        qtbot.mouseClick(box.follow, Qt.MouseButton.LeftButton)


def test_tab_order_within_the_box(box):
    assert box.options.nextInFocusChain() is box.save
    assert box.save.nextInFocusChain() is box.follow


def test_a_click_on_the_title_focuses_the_options_and_tints_nothing_itself(qtbot, box):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QLineEdit, QVBoxLayout, QWidget

    holder = QWidget()
    layout = QVBoxLayout(holder)
    other = QLineEdit()
    layout.addWidget(other)
    layout.addWidget(box)
    qtbot.addWidget(holder)
    box.holder = holder
    holder.show()
    qtbot.waitExposed(holder)
    holder.activateWindow()
    other.setFocus()
    qtbot.waitUntil(other.hasFocus)
    qtbot.mouseClick(box.title, Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
    assert box.options.hasFocus()
    assert not box.current


def test_save_and_follow_show_icon_and_text_at_the_right(qtbot, box):
    box.resize(600, 200)
    box.show()
    qtbot.waitExposed(box)
    for button, text in ((box.save, "Save"), (box.follow, "Follow")):
        assert button.text() == text
        assert not button.icon().isNull()
    assert box.follow.isCheckable()
    assert box.save.geometry().left() > box.width() // 2
    assert box.follow.geometry().right() > box.save.geometry().right()
