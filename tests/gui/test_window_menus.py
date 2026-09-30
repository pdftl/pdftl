"""Tests for window_menus: menus, box menus and the shortcut help."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QLabel, QMenu

from pdftl.gui import keymap, window_menus
from tests.gui.window_support import _menu_texts, _mnemonic


def test_keys_markdown_skips_a_group_with_no_keyed_actions(monkeypatch):
    monkeypatch.setattr(
        window_menus, "key_groups", lambda: [("Empty", []), ("Full", [keymap.BY_ID["save"]])]
    )
    md = window_menus.keys_markdown()
    assert "Empty" not in md
    assert "Full" in md


def test_every_window_action_is_in_exactly_one_menu(make_window):
    make_window()
    listed = [
        e[0]
        for _title, entries in window_menus.MENUS
        for e in entries
        if e is not None and e[0] not in window_menus.RECENT_MENUS
    ]
    required = {a.id for a in keymap.ACTIONS if a.scope == "window" and not a.native}
    assert sorted(listed) == sorted(required)


def test_menus_are_grouped_with_separators_and_dim_shortcuts(make_window):
    window = make_window()
    menus = [a.menu() for a in window.menuBar().actions()]
    titles = [a.text() for a in window.menuBar().actions()]
    assert titles == ["&File", "&Edit", "&Stage", "Foc&us", "&View", "&Console", "&Help"]
    stage = menus[2]
    assert [a.isSeparator() for a in stage.actions()].count(True) == 4
    assert all(m.style() is window.menu_style for m in menus)
    follow = window.actions_by_id["follow_stage"]
    assert follow.text() == "&Follow in Viewer"
    assert follow.statusTip() == keymap.BY_ID["follow_stage"].label


def test_hide_pages_default_menu_action_starts_unchecked_and_toggles(make_window):
    from pdftl.gui import widgets

    window = make_window()
    action = window.actions_by_id["hide_pages_default"]
    assert action.isCheckable()
    assert action.isChecked() is False
    assert widgets.default_pages_hidden() is False
    action.trigger()
    assert widgets.default_pages_hidden() is True
    action.trigger()
    assert widgets.default_pages_hidden() is False


def test_hide_pages_default_menu_action_reflects_a_persisted_default(make_window):
    from pdftl.gui import widgets

    widgets.set_default_pages_hidden(True)
    window = make_window()
    assert window.actions_by_id["hide_pages_default"].isChecked() is True


def test_shortcut_list_leaves_out_actions_without_keys(make_window):
    window = make_window()
    window.show_shortcuts()
    text = window.help.toPlainText()
    assert "Quit" in text
    assert "About pdftl" not in text


def test_alt_letters_are_unique_across_menus_labels_and_shortcuts(make_window):
    window = make_window([])
    letters = [_mnemonic(a.text()) for a in window.menuBar().actions()]
    letters += [_mnemonic(label.text()) for label in window.findChildren(QLabel)]
    for action in keymap.build_actions(False):
        letters += [k[4:] for k in action.keys if len(k) == 5 and k.startswith("Alt+")]
    letters = [x for x in letters if x]
    assert len(letters) == len(set(letters)), sorted(letters)


def test_mnemonics_are_unique_within_each_menu(make_window):
    window = make_window([])
    for menu in window.menuBar().findChildren(QMenu):
        letters = [_mnemonic(a.text()) for a in menu.actions() if not a.isSeparator()]
        assert len(letters) == len(set(letters)), (menu.title(), letters)


def test_shortcuts_help_is_one_table_per_group(make_window):
    window = make_window([])
    window.show_shortcuts()
    doc = window.help.document()
    headings, tables = [], []
    block = doc.begin()
    while block.isValid():
        if block.blockFormat().headingLevel() == 3:
            headings.append(block.text())
        table = QTextCursor(block).currentTable()
        if table is not None and table not in tables:
            tables.append(table)
        block = block.next()
    assert headings == [
        "File",
        "Edit",
        "Stage",
        "Focus",
        "View",
        "Console",
        "Help",
        "Page strips",
        "Large preview",
        "Input files",
        "General",
    ]
    assert len(tables) == len(headings)
    cells = [
        t.cellAt(r, 1).firstCursorPosition().block().text()
        for t in tables
        for r in range(1, t.rows())
    ]
    keyed = [a.label for a in keymap.ACTIONS if a.keys]
    assert sorted(cells) == sorted(keyed)
    strips = tables[headings.index("Page strips")]
    assert "Toggle page selection" in [
        strips.cellAt(r, 1).firstCursorPosition().block().text() for r in range(1, strips.rows())
    ]


def test_a_stage_menu_offers_the_stage_actions(qtbot, make_window):
    window = make_window([])
    window.add_stage()
    box = window.boxes[1]
    box.args.setFocus()
    qtbot.waitUntil(lambda: box.current)
    texts = {t: enabled for t, enabled, _ in _menu_texts(window, box) if t}
    assert texts["&Delete Stage"] and texts["Move &Up"] and not texts["Move Do&wn"]
    assert "&Hide Page Strip" in texts


def test_the_hide_item_of_a_box_menu_hides_its_strip(make_window):
    window = make_window([])
    box = window.boxes[0]
    window.run_menu = lambda menu, _pos: next(
        a for a in menu.actions() if a.text() == "&Hide Page Strip"
    ).trigger()
    window._show_box_menu(box, QPoint(0, 0))
    assert box.pages_hidden


def test_the_inputs_menu_removes_the_selected_file(qtbot, make_window, two_pages):
    window = make_window([str(two_pages)])
    entries = _menu_texts(window, window.inputs_box)
    assert ("&Remove Input File", True, False) in entries
    window.run_menu = lambda menu, _pos: next(
        a for a in menu.actions() if a.text() == "&Remove Input File"
    ).trigger()
    window._show_box_menu(window.inputs_box, QPoint(0, 0))
    assert window.pipeline.inputs == ()
    assert ("&Remove Input File", False, False) in _menu_texts(window, window.inputs_box)


def test_right_clicking_an_input_asks_for_the_inputs_menu(qtbot, make_window, two_pages):
    from PySide6.QtGui import QContextMenuEvent

    window = make_window([str(two_pages)])
    asked = []
    window.run_menu = lambda menu, pos: asked.append(pos)
    viewport = window.input_list.viewport()
    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5), QPoint(40, 50))
    QApplication.sendEvent(viewport, event)
    assert asked == [QPoint(40, 50)]


def test_the_output_menu_follows_and_saves(make_window):
    window = make_window([])
    toggled = []
    window.toggle_output_follow = lambda: toggled.append(True)
    entries = _menu_texts(window, window.output_box)
    names = [t for t, _e, _c in entries if t]
    assert names == ["&Save Output As…", "&Follow in Viewer", "&Copy Command Line"]
    window.output_box.follow.setChecked(True)
    assert ("&Follow in Viewer", True, True) in _menu_texts(window, window.output_box)
    window.run_menu = lambda menu, _pos: next(
        a for a in menu.actions() if a.text() == "&Follow in Viewer"
    ).trigger()
    window._show_box_menu(window.output_box, QPoint(0, 0))
    assert toggled == [True]


def test_right_clicking_an_input_really_shows_the_inputs_menu(qtbot, make_window, two_pages):
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QContextMenuEvent

    window = make_window([str(two_pages)])
    shown = []

    def close_popup():
        popup = QApplication.activePopupWidget()
        if popup is None:
            QTimer.singleShot(20, close_popup)
            return
        shown.append([a.text() for a in popup.actions()])
        popup.close()

    QTimer.singleShot(0, close_popup)
    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(5, 5), QPoint(40, 50))
    QApplication.sendEvent(window.input_list.viewport(), event)
    assert len(shown) == 1 and "&Remove Input File" in shown[0]
