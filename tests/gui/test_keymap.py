import sys

import pytest

from pdftl.gui import keymap


def _keys(macos, action_id):
    return {a.id: a.keys for a in keymap.build_actions(macos)}[action_id]


def test_ids_unique():
    ids = [a.id for a in keymap.ACTIONS]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("macos", [False, True])
def test_bound_keys_unique_within_scope(macos):
    seen = set()
    for a in keymap.build_actions(macos):
        for k in a.keys:
            assert (a.scope, k) not in seen, k
            seen.add((a.scope, k))


def test_keys_for():
    assert keymap.keys_for("save") == ("Ctrl+S",)
    with pytest.raises(KeyError):
        keymap.keys_for("nope")


def test_by_id_matches_actions():
    assert keymap.BY_ID["save"].keys == ("Ctrl+S",)
    assert set(keymap.BY_ID) == {a.id for a in keymap.ACTIONS}


def test_focus_keys_are_alt_on_linux_and_windows():
    assert _keys(False, "focus_op") == ("Alt+O",)
    assert _keys(False, "focus_args") == ("Alt+A",)
    assert _keys(False, "focus_pages") == ("Alt+P",)
    assert _keys(False, "focus_console") == ("Alt+L",)


def test_focus_keys_are_ctrl_alt_on_macos():
    assert _keys(True, "focus_op") == ("Ctrl+Alt+O",)
    assert _keys(True, "focus_args") == ("Ctrl+Alt+A",)
    assert _keys(True, "focus_pages") == ("Ctrl+Alt+P",)
    assert _keys(True, "focus_console") == ("Ctrl+Alt+L",)


def test_toggle_page_is_space_only_on_macos():
    assert _keys(True, "toggle_page") == ("Space",)
    assert _keys(False, "toggle_page") == ("Space", "Ctrl+Space")


def test_prev_next_stage_keys_are_the_same_on_macos():
    assert _keys(True, "prev_stage") == ("Alt+Up",)
    assert _keys(True, "next_stage") == ("Alt+Down",)


def test_this_platform_table_is_the_one_built_for_it():
    assert keymap.ACTIONS == keymap.build_actions(sys.platform == "darwin")


@pytest.mark.parametrize("macos", [False, True])
def test_pipeline_shortcuts_are_the_same_key_on_every_platform(macos):
    assert _keys(macos, "open_pipeline") == ("Ctrl+Alt+I",)
    assert _keys(macos, "save_pipeline") == ("Ctrl+Alt+S",)


def test_save_pipeline_as_has_no_shortcut():
    assert _keys(False, "save_pipeline_as") == ()


def test_new_pipeline_shortcut_is_the_same_key_on_every_platform():
    assert _keys(False, "new_pipeline") == ("Ctrl+Alt+N",)
    assert _keys(True, "new_pipeline") == ("Ctrl+Alt+N",)


def test_hide_pages_default_has_no_shortcut():
    assert _keys(False, "hide_pages_default") == ()


def test_focus_output_is_alt_t_on_linux_and_ctrl_alt_t_on_macos():
    assert _keys(False, "focus_output") == ("Alt+T",)
    assert _keys(True, "focus_output") == ("Ctrl+Alt+T",)


@pytest.mark.parametrize("macos", [False, True])
def test_every_key_is_a_name_qt_knows(macos):
    qtgui = pytest.importorskip("PySide6.QtGui")
    for a in keymap.build_actions(macos):
        for k in a.keys:
            assert qtgui.QKeySequence(k).toString(), (a.id, k)
