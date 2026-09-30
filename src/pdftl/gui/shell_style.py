# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/shell_style.py

"""Shell quoting style: splitting, quoting and joining argv text. Qt-free.

`default_style()` picks "windows" on Windows, else "posix"; every GUI
module that turns typed text into argv, or argv back into text, resolves
its own default through it so the two stay consistent on one platform.
"""

from __future__ import annotations

import os
import shlex
import subprocess  # nosec B404

_WINDOWS = os.name == "nt"


def default_style() -> str:
    return "windows" if _WINDOWS else "posix"


def split_posix(text: str) -> list[str]:
    return shlex.split(text)


def _consume_backslash_run(text: str, i: int, cur: list[str], in_quotes: bool) -> tuple[int, bool]:
    """Consume the run of backslashes at `i`, appending literal characters to `cur`.

    A run of 2n backslashes before a quote collapses to n backslashes and
    the quote toggles `in_quotes` (consumed, not appended); a run of 2n+1
    collapses to n backslashes plus one literal quote (`in_quotes` unchanged).
    A run not followed by a quote is copied through as-is.
    """
    n = len(text)
    j = i
    while j < n and text[j] == "\\":
        j += 1
    num_bs = j - i
    if j >= n or text[j] != '"':
        cur.append("\\" * num_bs)
        return j, in_quotes
    cur.append("\\" * (num_bs // 2))
    if num_bs % 2:
        cur.append('"')
        return j + 1, in_quotes
    return j + 1, not in_quotes


def split_windows(text: str) -> list[str]:
    """Split `text` by the CommandLineToArgvW / MSVCRT backslash-quote rules.

    Unquoted whitespace (space or tab) separates arguments; see
    `_consume_backslash_run` for how backslashes and quotes combine.
    """
    args: list[str] = []
    cur: list[str] = []
    started = False
    in_quotes = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if not in_quotes and ch in (" ", "\t"):
            if started:
                args.append("".join(cur))
                cur, started = [], False
            i += 1
        elif ch == "\\":
            i, in_quotes = _consume_backslash_run(text, i, cur, in_quotes)
            started = True
        elif ch == '"':
            in_quotes = not in_quotes
            started = True
            i += 1
        else:
            cur.append(ch)
            started = True
            i += 1
    if started:
        args.append("".join(cur))
    return args


_SPLITTERS = {"posix": split_posix, "windows": split_windows}
# Quoting builds display text; nothing here runs a command.
_QUOTERS = {"posix": shlex.quote, "windows": lambda arg: subprocess.list2cmdline([arg])}  # nosec # nosemgrep


def split(text: str, style: str) -> list[str]:
    """Raises ValueError for an unknown style."""
    if style not in _SPLITTERS:
        raise ValueError(f"unknown style {style!r}")
    return _SPLITTERS[style](text)


def quote(arg: str, style: str) -> str:
    """Raises ValueError for an unknown style."""
    if style not in _QUOTERS:
        raise ValueError(f"unknown style {style!r}")
    return _QUOTERS[style](arg)


def join(args, style: str) -> str:
    """Join already-split `args` into one shell-quoted string.

    Raises ValueError for an unknown style.
    """
    return " ".join(quote(arg, style) for arg in args)
