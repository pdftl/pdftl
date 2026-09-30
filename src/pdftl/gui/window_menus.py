# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/window_menus.py

"""The main window's menus, context menus and keyboard-shortcut help."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMenu

from pdftl.gui import keymap
from pdftl.gui.menu_style import DimShortcutStyle
from pdftl.gui.widgets import default_pages_hidden, set_default_pages_hidden
from pdftl.gui.window_recent import RECENT_MENUS

MenuEntry = tuple[str, str] | None

CHECKABLE_ACTIONS = {"hide_pages_default"}
"""Menu actions that toggle rather than trigger; `_slots()` for these takes the new
checked state instead of being called with no arguments."""

DOCK_ACTIONS = {"show_console": "console_dock", "show_help_panel": "help_dock"}
"""Menu actions that are a dock's own show/hide toggle, by the dock's attribute."""


STAGE_MENU = (
    "insert_stage_before",
    "add_stage",
    "delete_stage",
    None,
    "move_stage_up",
    "move_stage_down",
    None,
    "insert_selection",
    None,
    "view_stage",
    "follow_stage",
    "export_stage",
)
INPUTS_MENU = (
    "add_input",
    "remove_input",
    "set_password",
    None,
    "insert_selection",
    None,
    "view_stage",
    "export_stage",
)
OUTPUT_MENU = ("save", "follow_output", None, "copy_cli")

STAGE_ACTIONS = ("delete_stage", "insert_stage_before", "focus_op", "focus_args", "focus_pages")
OUTPUT_ACTIONS = ("view_stage", "export_stage")

MENUS: tuple[tuple[str, tuple[MenuEntry, ...]], ...] = (
    (
        "&File",
        (
            ("new_pipeline", "&New Pipeline"),
            ("open_pipeline", "&Open Pipeline…"),
            ("recent_pipelines", "Open &Recent Pipeline"),
            ("save_pipeline", "Save &Pipeline…"),
            ("save_pipeline_as", "Save Pipeline &As…"),
            None,
            ("add_input", "A&dd Input Files…"),
            ("recent_inputs", "Add Recent &Input File"),
            ("set_password", "Set Pass&word…"),
            None,
            ("save", "&Save Output As…"),
            ("copy_cli", "&Copy Command Line"),
            None,
            ("quit", "&Quit"),
        ),
    ),
    ("&Edit", (("undo", "&Undo"), ("redo", "&Redo"))),
    (
        "&Stage",
        (
            ("add_stage", "&New Stage After"),
            ("insert_stage_before", "New Stage &Before"),
            ("delete_stage", "&Delete Stage"),
            None,
            ("move_stage_up", "Move &Up"),
            ("move_stage_down", "Move Do&wn"),
            None,
            ("insert_selection", "&Insert Selected Pages"),
            ("run", "&Run Now"),
            None,
            ("view_stage", "&Open Snapshot in Viewer"),
            ("follow_stage", "&Follow in Viewer"),
            ("export_stage", "Save Stage Output &As…"),
            None,
            ("hide_pages_default", "Hide Page &Strips for New Stages"),
        ),
    ),
    (
        "Foc&us",
        (
            ("prev_stage", "&Previous Stage"),
            ("next_stage", "&Next Stage"),
            None,
            ("focus_op", "&Operation"),
            ("focus_args", "&Arguments"),
            ("focus_pages", "Page &Strip"),
            ("focus_output", "Ou&tput Box"),
            None,
            ("focus_command", "&Command Bar"),
            ("focus_console", "Conso&le"),
        ),
    ),
    (
        "&View",
        (
            ("zoom_in", "Zoom &In"),
            ("zoom_out", "Zoom &Out"),
            ("zoom_reset", "&Reset Zoom"),
            None,
            ("choose_viewer", "Choose PDF &Viewer…"),
        ),
    ),
    (
        "&Console",
        (
            ("show_console", "Show Console &Panel"),
            None,
            ("find_text", "&Find…"),
            ("clear_console", "C&lear"),
            ("save_console", "&Save As…"),
        ),
    ),
    (
        "&Help",
        (
            ("show_help_panel", "Show Help &Panel"),
            ("help", "&Operation Help"),
            ("shortcuts", "&Keyboard Shortcuts"),
            None,
            ("about", "&About pdftl"),
        ),
    ),
)


SCOPE_GROUPS = {
    "strip": "Page strips",
    "preview": "Large preview",
    "inputs": "Input files",
    "window": "General",
}


def exec_menu(menu: QMenu, pos: QPoint) -> QAction | None:
    """Unbound `QMenu.exec(menu, pos)` matches the wrong overload."""
    return menu.exec(pos)


def key_groups() -> list[tuple[str, list[keymap.KeyAction]]]:
    """Actions with keys, grouped like the menus; the rest by where they work."""
    groups = []
    seen = set()
    for title, entries in MENUS:
        ids = [e[0] for e in entries if e is not None and e[0] not in RECENT_MENUS]
        groups.append((title.replace("&", ""), [keymap.BY_ID[i] for i in ids]))
        seen.update(ids)
    for scope, name in SCOPE_GROUPS.items():
        rest = [a for a in keymap.ACTIONS if a.scope == scope and a.id not in seen]
        groups.append((name, rest))
    return [(name, [a for a in acts if a.keys]) for name, acts in groups]


def keys_markdown() -> str:
    def keys(action: keymap.KeyAction) -> str:
        native = QKeySequence.SequenceFormat.NativeText
        return " / ".join(f"`{QKeySequence(k).toString(native)}`" for k in action.keys)

    parts = []
    for name, actions in key_groups():
        if actions:
            rows = "\n".join(f"| {keys(a)} | {a.label} |" for a in actions)
            parts.append(f"### {name}\n\n| Keys | Action |\n|---|---|\n{rows}")
    return "\n\n".join(parts)


class MenusMixin:
    """Menu bar, box context menus, and which actions are enabled."""

    def _slots(self) -> dict[str, Callable[[], object]]:
        return {
            "open_pipeline": self.open_pipeline,
            "save_pipeline": self.save_pipeline,
            "save_pipeline_as": self.save_pipeline_as,
            "add_input": self.open_input,
            "set_password": self.set_password,
            "save": self.save,
            "view_stage": self.view_focused,
            "follow_stage": self.follow_focused,
            "export_stage": self.export_focused,
            "copy_cli": self.copy_cli,
            "quit": self.close,
            "add_stage": self.add_stage,
            "insert_stage_before": self.insert_stage_before,
            "undo": self.undo,
            "redo": self.redo,
            "delete_stage": self.delete_stage,
            "move_stage_up": lambda: self.move_stage(-1),
            "move_stage_down": lambda: self.move_stage(1),
            "prev_stage": lambda: self.step(-1),
            "next_stage": lambda: self.step(1),
            "focus_op": lambda: self._focus_part("op"),
            "focus_args": lambda: self._focus_part("args"),
            "focus_pages": lambda: self._focus_part("strip"),
            "focus_output": self.focus_output_box,
            "insert_selection": self.insert_selection,
            "run": self.run_pipeline,
            "focus_command": self.focus_command,
            "focus_console": self.focus_console,
            "find_text": self.find_text,
            "clear_console": self.console.clear,
            "save_console": self.save_console,
            "help": self.toggle_help,
            "shortcuts": self.show_shortcuts,
            "about": self.show_about,
            "new_pipeline": self.new_pipeline,
            "hide_pages_default": set_default_pages_hidden,
            "choose_viewer": self.choose_viewer,
            "zoom_in": lambda: self.zoom_focused(1),
            "zoom_out": lambda: self.zoom_focused(-1),
            "zoom_reset": lambda: self.zoom_focused(0),
        }

    # Context menus

    def _box_menu_entries(self, box) -> tuple[str | None, ...]:
        if box is self.output_box:
            return OUTPUT_MENU
        return INPUTS_MENU if box is self.inputs_box else STAGE_MENU

    def _menu_action(self, menu: QMenu, action_id: str) -> QAction:
        if action_id in self.actions_by_id:
            return self.actions_by_id[action_id]
        if action_id == "remove_input":
            action = QAction("&Remove Input File", menu)
            action.setShortcut(QKeySequence(keymap.keys_for("remove_input")[0]))
            action.setEnabled(self.input_list.currentRow() >= 0)
            action.triggered.connect(self.remove_current_input)
            return action
        action = QAction("&Follow in Viewer", menu)
        action.setCheckable(True)
        action.setChecked(self.output_box.follow.isChecked())
        action.triggered.connect(self.toggle_output_follow)
        return action

    def _show_box_menu(self, box, pos: QPoint) -> None:
        """The box's own actions; the click already made it current."""
        menu = QMenu(self)
        menu.setStyle(self.menu_style)
        for action_id in self._box_menu_entries(box):
            if action_id is None:
                menu.addSeparator()
            else:
                menu.addAction(self._menu_action(menu, action_id))
        if box is not self.output_box:
            menu.addSeparator()
            hide = menu.addAction("&Hide Page Strip")
            hide.setCheckable(True)
            hide.setChecked(box.pages_hidden)
            hide.toggled.connect(box.hide_pages.setChecked)
        self.run_menu(menu, pos)
        menu.deleteLater()

    def _build_menu_action(self, action_id: str, text: str, slot) -> QAction:
        spec = keymap.BY_ID[action_id]
        if action_id in DOCK_ACTIONS:
            action = getattr(self, DOCK_ACTIONS[action_id]).toggleViewAction()
            action.setText(text)
            action.setStatusTip(spec.label)
            return action
        action = QAction(text, self)
        action.setStatusTip(spec.label)
        action.setShortcuts([QKeySequence(k) for k in spec.keys])
        if action_id in CHECKABLE_ACTIONS:
            action.setCheckable(True)
            action.setChecked(default_pages_hidden())
            action.toggled.connect(slot)
        else:
            action.triggered.connect(slot)
        return action

    def _build_menus(self) -> None:
        slots = self._slots()
        self.menu_style = DimShortcutStyle()
        for title, entries in MENUS:
            menu = self.menuBar().addMenu(title)
            menu.setStyle(self.menu_style)
            for entry in entries:
                if entry is None:
                    menu.addSeparator()
                    continue
                action_id, text = entry
                if action_id in RECENT_MENUS:
                    menu.addMenu(self._recent_menu(action_id, text))
                    continue
                action = self._build_menu_action(action_id, text, slots.get(action_id))
                menu.addAction(action)
                self.actions_by_id[action_id] = action

    def _update_actions(self) -> None:
        """Disable actions that need a current stage, or its output, when there is none."""
        stage = self.current in self.boxes
        output = self.current is not None and self.current.path is not None
        i = self.current_index()
        enabled = {
            **dict.fromkeys(STAGE_ACTIONS, stage),
            **dict.fromkeys(OUTPUT_ACTIONS, output),
            "move_stage_up": stage and i > 0,
            "move_stage_down": stage and i < len(self.boxes) - 1,
            "follow_stage": stage and (output or self.current in self.followed),
            "insert_selection": self.current is not None,
            "prev_stage": bool(self.boxes),
            "next_stage": bool(self.boxes),
        }
        for action_id, on in enabled.items():
            self.actions_by_id[action_id].setEnabled(on)
