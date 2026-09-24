# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/fonts/charstring_work_guard.py

"""
Bounds fontTools' charstring interpretation against an untrusted embedded
font program with deeply or exponentially recursive subroutines.

Reading a CFF glyph's advance width means interpreting its charstring
(`decompile()` and `draw()` both follow every callsubr/callgsubr), and
converting a Type 1 glyph to CFF means drawing it. All of these run
through fontTools.misc.psCharStrings.SimpleT2Decompiler.execute, which has
no bound: a subroutine that calls itself dies with RecursionError, and one
that calls the next one twice, thirty levels deep, runs for 2^30 calls.

Work is charged per program byte (or token) executed rather than per
call, so a shallow call graph that still visits a huge amount of
subroutine code is bounded the same as deep recursion. Charstrings have no
jump operators, so executed work never exceeds the sum of executed
program lengths.

SimpleT2Decompiler.execute is replaced once by a wrapper that only charges
a budget while the current thread is inside `bounded_charstring_interpreter()`;
anywhere else it behaves exactly like stock fontTools, so concurrent
callers neither race on a patch/restore pair nor share each other's
budgets.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

# Charstring bytes/tokens executed for one glyph (decompile and draw),
# subroutines included. Real peak ~49k across 1,886 CFF fonts, ~41k
# across 7,491 Type 1 fonts. Kept tight because it is spent per glyph.
MAX_CHARSTRING_WORK = 200_000

# Execution nesting, the glyph itself included. Type 2 caps subroutine
# nesting at 10 (Adobe TN #5177, Appendix B); real peak 11.
MAX_CHARSTRING_DEPTH = 64

_state = threading.local()
_install_lock = threading.Lock()


class CharstringBudgetExceeded(Exception):
    """A glyph's charstring exceeded the work or nesting budget. The message
    is fixed and never built from document bytes."""


def _program_size(charstring) -> int:
    program = getattr(charstring, "bytecode", None)
    if program is None:
        program = getattr(charstring, "program", None)
    return max(1, len(program or ()))


def _install() -> None:
    from fontTools.misc.psCharStrings import SimpleT2Decompiler

    with _install_lock:
        original = SimpleT2Decompiler.execute
        if getattr(original, "_pdftl_bounded", False):
            return

        def execute(self, charString, **kwargs):
            budget = getattr(_state, "budget", None)
            if budget is None:
                return original(self, charString, **kwargs)
            budget[0] -= _program_size(charString)
            if budget[0] < 0 or budget[1] >= MAX_CHARSTRING_DEPTH:
                raise CharstringBudgetExceeded(
                    "embedded font program exceeded the charstring work budget"
                )
            budget[1] += 1
            try:
                return original(self, charString, **kwargs)
            finally:
                budget[1] -= 1

        execute._pdftl_bounded = True
        SimpleT2Decompiler.execute = execute


@contextmanager
def bounded_charstring_interpreter():
    """Charstrings this thread interprets inside the block share one fresh
    budget (remaining work, current depth), raising
    CharstringBudgetExceeded past it. Use one block per glyph."""
    _install()
    previous = getattr(_state, "budget", None)
    _state.budget = [MAX_CHARSTRING_WORK, 0]
    try:
        yield
    finally:
        _state.budget = previous
