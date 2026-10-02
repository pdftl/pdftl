# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/window_recent.py

"""Recently used pipeline and input files, and their File submenus."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QMenu

from pdftl.gui import widgets

RECENT_MAX = 10
RECENT_MENUS = {
    "recent_pipelines": ("pipelines", "&Clear Recent Pipelines"),
    "recent_inputs": ("inputs", "&Clear Recent Input Files"),
}
"""File submenus, by menu id: the kind of file listed, and the clear entry."""


def _key(kind: str) -> str:
    return f"recent/{kind}"


def _stored(kind: str) -> list[Path]:
    raw = widgets.settings_factory().value(_key(kind), "[]", type=str)
    try:
        names = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(names, list):
        return []
    return [Path(n) for n in names if isinstance(n, str)]


def recent_paths(kind: str) -> list[Path]:
    """Remembered files of `kind`, newest first, leaving out ones since removed."""
    return [p for p in _stored(kind) if p.is_file()]


def remember(kind: str, path: Path) -> None:
    path = Path(path).absolute()
    names = [str(path)] + [str(p) for p in recent_paths(kind) if p != path]
    widgets.settings_factory().setValue(_key(kind), json.dumps(names[:RECENT_MAX]))


def clear_recent(kind: str) -> None:
    widgets.settings_factory().remove(_key(kind))


def entry_text(n: int, path: Path) -> str:
    """`n` counts from 1; the tenth entry's mnemonic is 0."""
    label = f"{path.name}  ({path.parent})".replace("&", "&&")
    return f"&{n % 10} {label}"


class RecentMixin:
    """Open Recent Pipeline and Add Recent Input File."""

    def _recent_menu(self, menu_id: str, title: str) -> QMenu:
        menu = QMenu(title, self)
        menu.setStyle(self.menu_style)
        menu.aboutToShow.connect(lambda: self._fill_recent(menu, menu_id))
        self._fill_recent(menu, menu_id)
        return menu

    def _fill_recent(self, menu: QMenu, menu_id: str) -> None:
        kind, clear_text = RECENT_MENUS[menu_id]
        menu.clear()
        paths = recent_paths(kind)
        for n, path in enumerate(paths, 1):
            action = menu.addAction(entry_text(n, path))
            action.setStatusTip(str(path))
            action.triggered.connect(lambda _=False, p=path: self._open_recent(kind, p))
        if not paths:
            menu.addAction("(none)").setEnabled(False)
        menu.addSeparator()
        clear = menu.addAction(clear_text)
        clear.setEnabled(bool(paths))
        clear.triggered.connect(lambda: clear_recent(kind))

    def _open_recent(self, kind: str, path: Path) -> None:
        if kind == "inputs":
            self.add_file(path)
        elif self._confirm_discard_pipeline():
            self._load_pipeline_file(path)
