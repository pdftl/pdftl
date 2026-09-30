# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/console.py

"""Read-only console showing a pipeline stage's stdout/stderr, ANSI-coloured.

`parse_ansi` is a pure function: text in, styled runs and an end state out.
It never touches Qt. `Style.fg`/`bg` are either `None` (default colour), an
`int` 0-15 (a palette slot: 0-7 the 8 standard ANSI colours in order black,
red, green, yellow, blue, magenta, cyan, white; 8-15 their bright variants),
or an `(r, g, b)` tuple for a 256-colour or truecolor SGR code. A palette
slot is resolved to concrete RGB only in `AnsiConsole`, once the widget's
light/dark palette is known, so the same parsed style renders legibly in
either theme.

OSC 8 hyperlinks set `Style.link`; other CSI and OSC sequences are
recognised and dropped rather than shown. An unterminated escape at the end of a chunk is
dropped to its end rather than raising or leaking control bytes. A `\\r` not
at the end of a line discards the line built so far, matching a redrawn
progress bar; the survivor is what a terminal would still show.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import (
    QAction,
    QColor,
    QContextMenuEvent,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import QFileDialog, QMenu, QPlainTextEdit, QWidget


@dataclass(frozen=True)
class Style:
    fg: int | tuple[int, int, int] | None = None
    bg: int | tuple[int, int, int] | None = None
    bold: bool = False
    italic: bool = False
    underline: bool = False
    link: str | None = None


_CSI_RE = re.compile(r"\x1b\[([0-?]*)([ -/]*)([@-~])")
_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", re.DOTALL)
_OSC8_RE = re.compile(r"\x1b\]8;[^;\x07\x1b]*;([^\x07\x1b]*)")
_LINE_SPLIT_RE = re.compile(r"([\r\n])")


def _tokenize(text: str) -> Iterator[tuple[str, str | list[int]]]:
    """Yield `("text", str)`, `("sgr", params)` or `("link", url)` runs, in order.

    Every other CSI/OSC sequence, and a lone `ESC`, is consumed silently.
    An unterminated CSI or OSC at the end of `text` is dropped to the end.
    """
    i = 0
    while i < len(text):
        esc = text.find("\x1b", i)
        if esc == -1:
            yield ("text", text[i:])
            return
        if esc > i:
            yield ("text", text[i:esc])
        token, i = _escape(text, esc)
        if token is not None:
            yield token
        if i is None:
            return


def _escape(text: str, esc: int) -> tuple[tuple[str, str | list[int]] | None, int | None]:
    """The token for the escape at `esc` (None if ignored) and where scanning
    resumes (None if the sequence is unterminated)."""
    nxt = text[esc + 1 : esc + 2]
    if nxt == "[":
        m = _CSI_RE.match(text, esc)
        if m is None:
            return None, None
        sgr = ("sgr", _parse_sgr_params(m.group(1))) if m.group(3) == "m" else None
        return sgr, m.end()
    if nxt == "]":
        m = _OSC_RE.match(text, esc)
        if m is None:
            return None, None
        link = _OSC8_RE.match(m.group())
        return (("link", link.group(1)) if link else None), m.end()
    return None, esc + 1


def _parse_sgr_params(params_str: str) -> list[int]:
    """Empty means a bare reset (`ESC[m`); malformed params mean no-op."""
    if params_str == "":
        return [0]
    try:
        return [int(p) if p else 0 for p in params_str.split(";")]
    except ValueError:
        return []


def _color_256(n: int) -> int | tuple[int, int, int]:
    n = max(0, min(255, n))
    if n < 16:
        return n
    if n < 232:
        idx = n - 16
        r, g, b = idx // 36, (idx // 6) % 6, idx % 6
        return tuple(0 if v == 0 else 55 + 40 * v for v in (r, g, b))
    level = 8 + (n - 232) * 10
    return (level, level, level)


def _parse_extended_color(
    params: list[int], i: int
) -> tuple[int, int | tuple[int, int, int]] | None:
    """Parse a `38;5;n`/`48;5;n` or `38;2;r;g;b`/`48;2;r;g;b` run starting at `i`.

    Returns the index just past what was consumed, and the colour; `None`
    if truncated or the mode byte is neither `5` nor `2`.
    """
    if i + 1 >= len(params):
        return None
    mode = params[i + 1]
    if mode == 5:
        if i + 2 >= len(params):
            return None
        return i + 3, _color_256(params[i + 2])
    if mode == 2:
        if i + 4 >= len(params):
            return None
        rgb = tuple(max(0, min(255, v)) for v in params[i + 2 : i + 5])
        return i + 5, rgb
    return None


# code -> (Style field, value); the "reset to default" and toggle codes.
_ATTR_CODES: dict[int, tuple[str, bool | None]] = {
    1: ("bold", True),
    22: ("bold", False),
    3: ("italic", True),
    23: ("italic", False),
    4: ("underline", True),
    24: ("underline", False),
    39: ("fg", None),
    49: ("bg", None),
}


def _standard_color(code: int, base: int, bright_base: int) -> int | None:
    """Palette index for a `base..base+7` or `bright_base..bright_base+7` code."""
    if base <= code <= base + 7:
        return code - base
    if bright_base <= code <= bright_base + 7:
        return code - bright_base + 8
    return None


def _apply_simple_sgr(style: Style, code: int) -> Style:
    if code == 0:
        return Style()
    if code in _ATTR_CODES:
        field, value = _ATTR_CODES[code]
        return replace(style, **{field: value})
    fg = _standard_color(code, 30, 90)
    if fg is not None:
        return replace(style, fg=fg)
    bg = _standard_color(code, 40, 100)
    if bg is not None:
        return replace(style, bg=bg)
    return style


def _apply_sgr(style: Style, params: list[int]) -> Style:
    i, n = 0, len(params)
    while i < n:
        code = params[i]
        if code in (38, 48):
            parsed = _parse_extended_color(params, i)
            if parsed is None:
                return style
            i, color = parsed
            style = replace(style, fg=color) if code == 38 else replace(style, bg=color)
            continue
        style = _apply_simple_sgr(style, code)
        i += 1
    return style


def _append_merge(dest: list[list], chunk: str, style: Style) -> None:
    if not chunk:
        return
    if dest and dest[-1][1] == style:
        dest[-1][0] += chunk
    else:
        dest.append([chunk, style])


def _split_lines(text: str) -> Iterator[tuple[str, str | None]]:
    """Split on `\\r`/`\\n`, keeping the separator with each preceding piece."""
    parts = _LINE_SPLIT_RE.split(text)
    for idx in range(0, len(parts), 2):
        sep = parts[idx + 1] if idx + 1 < len(parts) else None
        yield parts[idx], sep


def parse_ansi(text: str, state: Style = Style()) -> tuple[list[tuple[str, Style]], Style]:
    """Split `text` into `(run_text, Style)` pairs, and the style to continue with.

    `state` is the style in effect at the start of `text` (from a previous
    chunk); pass the returned style back in for the next chunk.
    """
    style = state
    runs: list[list] = []
    line: list[list] = []

    def flush() -> None:
        for chunk, s in line:
            _append_merge(runs, chunk, s)
        line.clear()

    for kind, payload in _tokenize(text):
        if kind == "sgr":
            style = replace(_apply_sgr(style, payload), link=style.link)
            continue
        if kind == "link":
            style = replace(style, link=payload or None)
            continue
        for piece, sep in _split_lines(payload):
            if sep == "\r":
                line.clear()
            elif sep == "\n":
                _append_merge(line, piece, style)
                _append_merge(line, "\n", style)
                flush()
            else:
                _append_merge(line, piece, style)
    flush()
    return [(t, s) for t, s in runs], style


# 0-7 standard, 8-15 bright, in ANSI order (black red green yellow blue
# magenta cyan white). Tuned for legibility on a near-black background.
_DARK_PALETTE: tuple[tuple[int, int, int], ...] = (
    (85, 85, 85),
    (205, 49, 49),
    (13, 188, 121),
    (229, 229, 16),
    (36, 114, 200),
    (188, 63, 188),
    (17, 168, 205),
    (229, 229, 229),
    (102, 102, 102),
    (241, 76, 76),
    (35, 209, 139),
    (245, 245, 67),
    (59, 142, 234),
    (214, 112, 214),
    (41, 184, 219),
    (255, 255, 255),
)

# Same order, tuned for legibility on a near-white background: black/white
# (0, 7, 8, 15) are swapped away from the unreadable extreme.
_LIGHT_PALETTE: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0),
    (170, 0, 0),
    (0, 128, 0),
    (170, 170, 0),
    (0, 0, 170),
    (170, 0, 170),
    (0, 170, 170),
    (85, 85, 85),
    (102, 102, 102),
    (213, 0, 0),
    (0, 128, 0),
    (150, 130, 0),
    (0, 0, 215),
    (170, 0, 170),
    (0, 130, 130),
    (30, 30, 30),
)


def _resolve_color(value: int | tuple[int, int, int], dark: bool) -> tuple[int, int, int]:
    if isinstance(value, tuple):
        return value
    return (_DARK_PALETTE if dark else _LIGHT_PALETTE)[value]


class AnsiConsole(QPlainTextEdit):
    """Read-only, fixed-width console for a pipeline stage's captured output."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self._style = Style()
        self._file_dialog: Callable[..., tuple[str, str]] = QFileDialog.getSaveFileName
        self.open_link: Callable[[str], object] = lambda url: QDesktopServices.openUrl(QUrl(url))
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.viewport().setMouseTracking(True)

    def append_ansi(self, text: str) -> None:
        """Append `text`, interpreting ANSI escapes; continues the running style."""
        runs, self._style = parse_ansi(text, self._style)
        self._insert_runs(runs)

    def append_plain(self, text: str) -> None:
        """Append `text` verbatim, in the console's default style."""
        self._insert_runs([(text, Style())])

    def clear(self) -> None:
        super().clear()
        self._style = Style()

    def save_to(self, path: Path) -> None:
        Path(path).write_text(self.toPlainText(), encoding="utf-8")

    def _insert_runs(self, runs: list[tuple[str, Style]]) -> None:
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        dark = self._is_dark()
        for text, style in runs:
            if not text:
                continue
            cursor.setCharFormat(self._char_format(style, dark))
            cursor.insertText(text)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def _is_dark(self) -> bool:
        return self.palette().base().color().lightness() < 128

    def _char_format(self, style: Style, dark: bool) -> QTextCharFormat:
        fmt = QTextCharFormat()
        if style.fg is not None:
            fmt.setForeground(QColor(*_resolve_color(style.fg, dark)))
        if style.bg is not None:
            fmt.setBackground(QColor(*_resolve_color(style.bg, dark)))
        if style.bold:
            fmt.setFontWeight(QFont.Weight.Bold)
        if style.italic:
            fmt.setFontItalic(True)
        if style.underline:
            fmt.setFontUnderline(True)
        if style.link:
            fmt.setAnchor(True)
            fmt.setAnchorHref(style.link)
            fmt.setToolTip(style.link)
        return fmt

    def link_at_position(self, position: int) -> str | None:
        """The link on the character after `position`, else the one before it."""
        last = self.document().characterCount() - 1
        cursor = QTextCursor(self.document())
        for end in (position + 1, position):
            if 0 < end <= last:
                cursor.setPosition(end)
                if href := cursor.charFormat().anchorHref():
                    return href
        return None

    def link_at(self, point: QPoint) -> str | None:
        """The link under viewport point `point`."""
        return self.link_at_position(self.cursorForPosition(point).position())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        super().mouseMoveEvent(event)
        over = self.link_at(event.position().toPoint())
        shape = Qt.CursorShape.PointingHandCursor if over else Qt.CursorShape.IBeamCursor
        self.viewport().setCursor(shape)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        clicked = event.button() == Qt.MouseButton.LeftButton
        if clicked and not self.textCursor().hasSelection():
            if url := self.link_at(event.position().toPoint()):
                self.open_link(url)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        url = self.link_at_position(self.textCursor().position()) if enter else None
        if url:
            self.open_link(url)
        else:
            super().keyPressEvent(event)

    def _build_context_menu(self, url: str | None = None) -> QMenu:
        menu = self.createStandardContextMenu()
        if url:
            menu.insertSeparator(menu.actions()[0])
            menu.insertAction(menu.actions()[0], self._action(menu, "Copy Link Address", url))
            open_link = QAction("Open Link", menu)
            open_link.triggered.connect(lambda: self.open_link(url))
            menu.insertAction(menu.actions()[0], open_link)
        menu.addSeparator()
        menu.addAction("Clear", self.clear)
        menu.addAction("Save As...", self._save_as)
        return menu

    @staticmethod
    def _action(menu: QMenu, text: str, url: str) -> QAction:
        action = QAction(text, menu)
        action.triggered.connect(lambda: QGuiApplication.clipboard().setText(url))
        return action

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        if event.reason() == QContextMenuEvent.Reason.Keyboard:
            url = self.link_at_position(self.textCursor().position())
        else:
            url = self.link_at(event.pos())
        self._build_context_menu(url).exec(event.globalPos())

    def _save_as(self) -> None:
        path_str, _filter = self._file_dialog(self, "Save Console Output")
        if path_str:
            self.save_to(Path(path_str))
