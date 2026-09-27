# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/images/quality_search.py

"""The lowest of an ordered list of encoder settings whose result passes a check."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

S = TypeVar("S")
R = TypeVar("R")


def lowest_passing(settings: Sequence[S], attempt: Callable[[S], R | None]) -> R | None:
    """`attempt`'s result at the first of `settings` it does not return None for.

    Settings are ordered so that one passing implies every later one passes.
    The first is tried alone, then the last, then the range between is
    bisected: at most 2 + log2(len(settings)) attempts.
    """
    if not settings:
        return None
    first = attempt(settings[0])
    if first is not None or len(settings) == 1:
        return first
    best = attempt(settings[-1])
    if best is None:
        return None
    failing, passing = 0, len(settings) - 1
    while passing - failing > 1:
        middle = (failing + passing) // 2
        result = attempt(settings[middle])
        if result is None:
            failing = middle
        else:
            passing, best = middle, result
    return best
