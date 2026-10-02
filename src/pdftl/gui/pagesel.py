# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/pagesel.py

"""Page selections as pdftl page-specification text. Qt-free."""

from __future__ import annotations

import string
from collections.abc import Iterable


def _runs(pages: list[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for page in pages:
        if runs and page == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], page)
        else:
            runs.append((page, page))
    return runs


def _format_run(start: int, stop: int, total: int) -> str:
    last = "end" if stop == total and total > 1 else str(stop)
    return last if start == stop else f"{start}-{last}"


def pages_to_spec(pages: Iterable[int], total: int, handle: str = "") -> str:
    """Comma-separated runs selecting exactly `pages`, e.g. `1-3,7,10-end`.

    A run ending at `total` is written with `end` (except when total is 1).
    With a handle every item carries it (`B1-3,B7`): in a comma list the CLI
    applies a handle only to the item it prefixes. Empty input gives "".

    Raises ValueError for a handle other than "" or A-Z, or a page outside
    1..total.
    """
    if handle and (len(handle) != 1 or handle not in string.ascii_uppercase):
        raise ValueError(f"handle must be empty or a single letter A-Z, got {handle!r}")
    ordered = sorted(set(pages))
    for page in ordered:
        if not 1 <= page <= total:
            raise ValueError(f"page {page} outside range 1..{total}")
    return ",".join(f"{handle}{_format_run(a, b, total)}" for a, b in _runs(ordered))


def spec_to_pages(spec: str, total: int) -> list[int]:
    """Sorted, deduplicated pages a handle-free spec selects, via pdftl's resolver.

    Out-of-range pages are clamped, as on the CLI. Raises pdftl's
    InvalidArgumentError for a malformed spec.
    """
    from pdftl.utils.page_specs import page_numbers_matching_page_spec

    return page_numbers_matching_page_spec(spec, total)


def insert_coordinates(args_text: str, cursor: int, token: str) -> tuple[str, int]:
    """Insert `token` at `cursor`, after a comma if it follows other spec text.

    So `1(abs` then two picks gives `1(abs,x0,y0,x1,y1`. Returns the new text
    and the position just after `token`.
    """
    cursor = max(0, min(cursor, len(args_text)))
    before, after = args_text[:cursor], args_text[cursor:]
    if before and before[-1] not in "(, \t":
        before += ","
    return before + token + after, len(before) + len(token)


def insert_into_args(args_text: str, cursor: int, spec: str) -> tuple[str, int]:
    """Insert `spec` at `cursor` as its own shell token.

    Adds a space on either side only where needed. The cursor is clamped into
    the text. Returns the new text and the position just after `spec`.
    """
    if not spec:
        return args_text, cursor
    cursor = max(0, min(cursor, len(args_text)))
    before, after = args_text[:cursor], args_text[cursor:]
    if before and not before[-1].isspace():
        before += " "
    if after and not after[0].isspace():
        after = " " + after
    return before + spec + after, len(before) + len(spec)
