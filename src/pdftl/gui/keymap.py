# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/keymap.py

"""Every keyboard shortcut in the GUI, in one table.

Qt-free. Sequences use `QKeySequence` portable text, so `Ctrl` becomes
Cmd on macOS. Widgets bind by action id; the help panel lists this table.

Keys handled by the widgets themselves (Tab, arrows, Shift+arrows, Home,
End, Space, Ctrl+A/Ctrl+Space inside a strip) are listed with `native=True`
for the help panel only and must not be bound as extra shortcuts.

On macOS, Option+letter types a character, so focus keys are Cmd+Option+letter;
Cmd+Space is Spotlight, so page toggling is plain Space only.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class KeyAction:
    id: str
    keys: tuple[str, ...]
    label: str
    scope: str
    """`window`: anywhere; `strip`: a page strip has focus; `inputs`: the input list has
    focus; `preview`: the large page preview is open."""

    native: bool = False


def build_actions(macos: bool) -> tuple[KeyAction, ...]:
    """The key table for macOS or for Linux and Windows."""

    def focus(letter: str) -> tuple[str, ...]:
        return (f"Ctrl+Alt+{letter}",) if macos else (f"Alt+{letter}",)

    toggle = ("Space",) if macos else ("Space", "Ctrl+Space")
    return (
        KeyAction("add_input", ("Ctrl+O",), "Add input file", "window"),
        KeyAction("new_pipeline", ("Ctrl+Alt+N",), "New pipeline", "window"),
        KeyAction("open_pipeline", ("Ctrl+Alt+I",), "Open pipeline file", "window"),
        KeyAction("save_pipeline", ("Ctrl+Alt+S",), "Save pipeline file", "window"),
        KeyAction("save_pipeline_as", (), "Save pipeline file as", "window"),
        KeyAction(
            "set_password", ("Ctrl+Shift+P",), "Set password for selected input file", "window"
        ),
        KeyAction("add_stage", ("Ctrl+N",), "Add stage after the current one", "window"),
        KeyAction(
            "insert_stage_before", ("Ctrl+Shift+N",), "Add stage before the current one", "window"
        ),
        KeyAction("undo", ("Ctrl+Z",), "Undo", "window"),
        KeyAction("redo", ("Ctrl+Shift+Z", "Ctrl+Y"), "Redo", "window"),
        KeyAction("delete_stage", ("Ctrl+Shift+Del",), "Delete current stage", "window"),
        KeyAction("move_stage_up", ("Ctrl+Up",), "Move current stage up", "window"),
        KeyAction("move_stage_down", ("Ctrl+Down",), "Move current stage down", "window"),
        KeyAction("prev_stage", ("Alt+Up",), "Go to previous stage", "window"),
        KeyAction("next_stage", ("Alt+Down",), "Go to next stage", "window"),
        KeyAction("focus_op", focus("O"), "Focus operation of current stage", "window"),
        KeyAction("focus_args", focus("A"), "Focus arguments of current stage", "window"),
        KeyAction("focus_pages", focus("P"), "Focus page strip of current stage", "window"),
        KeyAction("focus_output", focus("T"), "Focus output options", "window"),
        KeyAction(
            "insert_selection",
            ("Ctrl+E",),
            "Insert page selection into next stage's arguments",
            "window",
        ),
        KeyAction(
            "pick_point",
            ("Ctrl+Shift+K",),
            "Pick a point on the pages and insert its coordinates into the arguments",
            "window",
        ),
        KeyAction("save", ("Ctrl+S",), "Save output", "window"),
        KeyAction("focus_console", focus("L"), "Focus the console", "window"),
        KeyAction("save_console", ("Ctrl+Shift+S",), "Save console text", "window"),
        KeyAction("find_text", ("Ctrl+F",), "Find in console", "window"),
        KeyAction("clear_console", ("Ctrl+L",), "Clear the console", "window"),
        KeyAction("focus_command", ("Ctrl+K",), "Focus the command bar", "window"),
        KeyAction("run", ("Ctrl+R",), "Run the pipeline now", "window"),
        KeyAction(
            "view_stage", ("Ctrl+Shift+O",), "Open stage output in the PDF viewer", "window"
        ),
        KeyAction(
            "follow_stage",
            ("Ctrl+Shift+F",),
            "Follow stage output live in the PDF viewer",
            "window",
        ),
        KeyAction("export_stage", ("Ctrl+Shift+E",), "Save stage output as", "window"),
        KeyAction("copy_cli", ("Ctrl+Shift+C",), "Copy command line", "window"),
        KeyAction(
            "zoom_in", ("Ctrl+=", "Ctrl++"), "Zoom in on the focused part of the window", "window"
        ),
        KeyAction("zoom_out", ("Ctrl+-",), "Zoom out of the focused part of the window", "window"),
        KeyAction("zoom_reset", ("Ctrl+0",), "Reset the zoom of the focused part", "window"),
        KeyAction("help", ("F1",), "Help for current operation", "window"),
        KeyAction("shortcuts", ("Ctrl+/",), "Show keyboard shortcuts", "window"),
        KeyAction("quit", ("Ctrl+Q",), "Quit", "window"),
        KeyAction("about", (), "About pdftl", "window"),
        KeyAction("choose_viewer", (), "Choose the PDF viewer", "window"),
        KeyAction("show_console", (), "Show or hide the console panel", "window"),
        KeyAction("show_help_panel", (), "Show or hide the help panel", "window"),
        KeyAction(
            "hide_pages_default",
            (),
            "Hide page strips for new stages by default",
            "window",
        ),
        KeyAction("remove_input", ("Delete",), "Remove selected input file", "inputs"),
        KeyAction("preview_page", ("Return", "Enter"), "Open large preview", "strip"),
        KeyAction("preview_prev_page", ("Left", "PgUp"), "Previous page", "preview"),
        KeyAction("preview_next_page", ("Right", "PgDown"), "Next page", "preview"),
        KeyAction("close_preview", ("Esc",), "Close large preview", "preview"),
        KeyAction("toggle_page", toggle, "Toggle page selection", "strip", native=True),
        KeyAction("select_all", ("Ctrl+A",), "Select all pages", "strip", native=True),
        KeyAction(
            "move_page",
            ("Left", "Right", "Home", "End"),
            "Move between pages",
            "strip",
            native=True,
        ),
        KeyAction(
            "extend_selection", ("Shift+Left", "Shift+Right"), "Extend selection", "strip", True
        ),
        KeyAction(
            "next_field", ("Tab", "Shift+Tab"), "Next / previous field", "window", native=True
        ),
    )


ACTIONS: tuple[KeyAction, ...] = build_actions(sys.platform == "darwin")

BY_ID: dict[str, KeyAction] = {a.id: a for a in ACTIONS}


def keys_for(action_id: str) -> tuple[str, ...]:
    """Raises KeyError for an unknown action id."""
    return BY_ID[action_id].keys
