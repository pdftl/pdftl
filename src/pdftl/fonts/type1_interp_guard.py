# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/fonts/type1_interp_guard.py

"""
Bounds fontTools.misc.psLib.PSInterpreter so that parsing an untrusted
Type 1 program (a PDF /FontFile stream) cannot hang the process or exhaust
memory.

T1Font.parse() executes the program's PostScript via PSInterpreter, which
has no step, loop or allocation limit: `0 0 -1 {pop} for` never returns,
and `50000000 array` allocates hundreds of MB from a few bytes of font.

The budgets are flat rather than scaled to program size, so a larger
crafted program buys no extra work. See the constants for headroom
measured against real fonts.

psLib.suckfont() constructs its interpreter by the module-global name
`psLib.PSInterpreter`, so that name is rebound once to a subclass. The
subclass only enforces budgets on an instance constructed inside
`bounded_type1_interpreter()` on the same thread; any other construction
behaves exactly like stock fontTools. This keeps concurrent callers (the
Python API may be driven from several threads) from racing on a
rebind/restore pair or inheriting each other's budgets.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

# Headroom below is against 7,491 real Type 1 programs (TeX Live and
# system fonts).

# Executed objects (handle_object + call_procedure), roughly 40 per glyph.
# Real peak ~200k (STIX2Math, 5,253 glyphs). Exhausting it takes seconds.
MAX_INTERP_STEPS = 1_000_000

# Cells allocated by `array` / `string` across one parse, on the order of
# 64 MB of pointers worst case. Real use tracks program size (each
# charstring is read into a `string`): peak ~2.5M, for a 2.6 MB program.
MAX_INTERP_CELLS = 8_000_000

# Operand-stack depth; real peak 279. `0 0 -1 {} for` pushes its counter
# every iteration and executes no body objects, so this catches it long
# before the step budget would.
MAX_INTERP_STACK = 65_536

_state = threading.local()
_install_lock = threading.Lock()


class InterpreterBudgetExceeded(Exception):
    """A Type 1 program's PostScript exceeded the step, stack or
    allocation budget. Treat like any other unparseable font. The message
    is fixed and never built from document bytes."""


def _bounded_interpreter_class(base: type) -> type:
    class _Bounded(base):
        _pdftl_bounded = True

        def __init__(self, *args, **kwargs):
            self._budget = [MAX_INTERP_STEPS, MAX_INTERP_CELLS] if _is_active() else None
            super().__init__(*args, **kwargs)

        def _tick(self):
            budget = self._budget
            if budget is None:
                return
            budget[0] -= 1
            if budget[0] < 0:
                raise InterpreterBudgetExceeded(
                    "Type 1 program exceeded the interpreter step budget"
                )
            if len(self.stack) > MAX_INTERP_STACK:
                raise InterpreterBudgetExceeded("Type 1 program overflowed the interpreter stack")

        def _charge(self):
            budget = self._budget
            if budget is None:
                return
            try:
                count = int(self.stack[-1].value)
            except (IndexError, AttributeError, TypeError, ValueError):
                return  # the base operator raises its own error
            if count > budget[1]:
                raise InterpreterBudgetExceeded(
                    "Type 1 program requested more interpreter memory than allowed"
                )
            budget[1] -= max(count, 0)

        def handle_object(self, obj):
            self._tick()
            super().handle_object(obj)

        def call_procedure(self, proc):
            self._tick()
            super().call_procedure(proc)

        def ps_array(self):
            self._charge()
            super().ps_array()

        def ps_string(self):
            self._charge()
            super().ps_string()

    return _Bounded


def _is_active() -> bool:
    return getattr(_state, "depth", 0) > 0


def _install() -> None:
    from fontTools.misc import psLib

    with _install_lock:
        if not getattr(psLib.PSInterpreter, "_pdftl_bounded", False):
            psLib.PSInterpreter = _bounded_interpreter_class(psLib.PSInterpreter)


@contextmanager
def bounded_type1_interpreter():
    """Every PSInterpreter this thread constructs inside the block enforces
    the budgets above, raising InterpreterBudgetExceeded past any of them."""
    _install()
    _state.depth = getattr(_state, "depth", 0) + 1
    try:
        yield
    finally:
        _state.depth -= 1
