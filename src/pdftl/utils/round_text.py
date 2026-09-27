# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/round_text.py

"""Shorten text positioning numbers while bounding how far any glyph moves.

TJ kerns and Td operands are rounded to fewer digits, or kerns dropped,
with the rounding error carried forward so that every glyph origin stays
within `tolerance` of where it was, in the space the stream is drawn
into (default user space for a page; every use, for a Form).

Two errors are tracked as text-space vectors: `line_err` (emitted minus
true line matrix translation) and `pos_err` (the same for the text
matrix, which places the next glyph). A kern moves only `pos_err`; a Td
moves `line_err` and resets `pos_err` to it; Tm and BT reset both. TD is
never rounded, as its operand also sets the leading.

Rounding needs the font size, horizontal scaling and matrices; while any
is unknown, numbers are left as they are. A text object holding an
operator outside `_SAFE_IN_TEXT` ends rewriting for the rest of the stream.
"""

from __future__ import annotations

import functools
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

Instruction = tuple[list[Any], Any]
Linear = tuple[float, float, float, float]  # a b c d of a PDF matrix

IDENTITY: Linear = (1.0, 0.0, 0.0, 1.0)
_MAX_DECIMALS = 4
_SLACK = 1e-9  # relative, so float noise never rejects an exact candidate

_TEXT_STATE_OPS = {"Tc", "Tw", "Tz", "TL", "Tf", "Tr", "Ts"}
_LINE_OPS = {"Td", "TD", "T*", "'", '"'}
_SAFE_IN_TEXT = (
    _TEXT_STATE_OPS
    | _LINE_OPS
    | {"Tj", "TJ", "Tm", "ET"}
    | {"g", "G", "rg", "RG", "k", "K", "cs", "CS", "sc", "SC", "scn", "SCN"}
    | {"w", "J", "j", "M", "d", "ri", "i"}
    | {"BMC", "BDC", "EMC", "MP", "DP"}
)


@dataclass
class RoundTextStats:
    text_objects: int = 0
    text_objects_skipped: int = 0  # held an operator this pass does not model
    kerns_before: int = 0
    kerns_after: int = 0
    numbers_shortened: int = 0


@dataclass(frozen=True)
class _State:
    """The part of the graphics state that q saves and Q restores."""

    ctm: Linear | None = IDENTITY  # None if unknown
    size: float | None = None  # font size; None until known
    th: float | None = 1.0  # horizontal scaling as a fraction; None if unknown
    vertical: bool = False


def _mul(m: Linear, n: Linear) -> Linear:
    a, b, c, d = m
    a2, b2, c2, d2 = n
    return (a * a2 + b * c2, a * b2 + b * d2, c * a2 + d * c2, c * b2 + d * d2)


def _number(x: Any) -> float | None:
    if isinstance(x, bool):
        return None
    try:
        value = float(x)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _matrix(operands: list[Any]) -> Linear | None:
    values = [_number(x) for x in operands]
    if len(values) != 6 or None in values:
        return None
    return values[0], values[1], values[2], values[3]  # type: ignore[return-value]


def _format(value: float, decimals: int) -> Decimal | int:
    """`value` rounded to `decimals` places, in its shortest written form."""
    text = f"{value:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    number = Decimal(text)
    if number == number.to_integral_value():
        return int(number)
    return number


def _candidates(true: float, compensated: float, original: Any) -> list[tuple[Any, float]]:
    """(written value, numeric value) pairs to choose from; the original comes first."""
    return [(original, true), *_roundings(true, compensated)]


@functools.lru_cache(maxsize=65536)
def _roundings(true: float, compensated: float) -> tuple[tuple[Any, float], ...]:
    out: dict[Any, float] = {}
    for target in (compensated, true):
        for decimals in range(_MAX_DECIMALS + 1):
            value = _format(target, decimals)
            out.setdefault(value, float(value))
    return tuple(out.items())


def _state_after(state: _State, operands: list[Any], name: str, is_vertical) -> _State:
    """Applies a Tf or Tz (inside or outside a text object)."""
    if name == "Tf":
        size = _number(operands[1]) if len(operands) == 2 else None
        vertical = is_vertical(str(operands[0])) if operands else None
        if vertical is None:
            return replace(state, size=None)  # font not found: its writing mode is unknown
        return replace(state, size=size, vertical=vertical)
    value = _number(operands[0]) if operands else None
    return replace(state, th=None if value is None else value / 100.0)


class _TextObject:
    """Rewrites one BT...ET whose operators are all in _SAFE_IN_TEXT."""

    def __init__(self, state, uses, tolerance, is_vertical, stats) -> None:
        self.state: _State = state
        self._uses: Sequence[Linear] = uses
        self._tolerance: float = tolerance
        self._is_vertical: Callable[[str], bool | None] = is_vertical
        self._stats: RoundTextStats = stats
        self._set_text_matrix(IDENTITY)

    def _set_text_matrix(self, tm: Linear | None) -> None:
        self.line_err = self.pos_err = (0.0, 0.0)
        ctm = self.state.ctm
        self._maps = (
            [] if tm is None or ctm is None else [_mul(_mul(tm, ctm), u) for u in self._uses]
        )

    def _within(self, dx: float, dy: float) -> bool:
        limit = self._tolerance * (1 + _SLACK)
        return all(
            math.hypot(dx * a + dy * c, dx * b + dy * d) <= limit for a, b, c, d in self._maps
        )

    def run(self, instructions: list[Instruction]) -> list[Instruction]:
        # A plain list: pikepdf builds a new object on every index, which would defeat `is`.
        return [self._step(list(operands), op, str(op)) for operands, op in instructions]

    def _step(self, operands: list[Any], op: Any, name: str) -> Instruction:
        if name == "Tm":
            self._set_text_matrix(_matrix(operands))
        elif name in ("Tf", "Tz"):
            self.state = _state_after(self.state, operands, name, self._is_vertical)
        elif name == "Td":
            return self._td(operands, op)
        elif name in _LINE_OPS:
            self.pos_err = self.line_err
        elif name == "TJ":
            return self._tj(operands, op)
        return operands, op

    def _best(self, options, error_of, width):
        """The option with the fewest characters whose error is within the bound;
        the smallest error breaks ties, then the earlier option."""
        best = None
        for size, written, value in sorted(
            ((width(w), w, v) for w, v in options), key=lambda o: o[0]
        ):
            if best is not None and size > best[0][0]:
                break
            err = error_of(value)
            if self._within(*err):
                key = (size, math.hypot(*err))
                if best is None or key < best[0]:
                    best = (key, written, err)
        assert best is not None  # the original numbers always qualify
        return best[1], best[2]

    # -- Td --

    def _td(self, operands: list[Any], op: Any) -> Instruction:
        t = [_number(x) for x in operands]
        if len(t) != 2 or None in t:
            self._maps = []  # a move we cannot follow: round nothing more here
            return operands, op
        if not self._maps:
            return operands, op
        (tx, ty), (ex, ey) = t, self.line_err
        ys = _candidates(ty, ty - ey, operands[1])
        options = [
            ((rx, ry), (vx, vy))
            for rx, vx in _candidates(tx, tx - ex, operands[0])
            for ry, vy in ys
        ]
        written, self.line_err = self._best(
            options,
            lambda v: (ex + v[0] - tx, ey + v[1] - ty),
            lambda w: len(str(w[0])) + len(str(w[1])),
        )
        self.pos_err = self.line_err
        if written[0] is operands[0] and written[1] is operands[1]:
            return operands, op
        self._stats.numbers_shortened += 1
        return list(written), op

    # -- TJ --

    def _kern_unit(self) -> tuple[float, float] | None:
        """Text-space displacement of one kern unit, or None while unknown."""
        size, th = self.state.size, self.state.th
        if not self._maps or not size or th is None:
            return None
        if self.state.vertical:
            return (0.0, -size / 1000.0)
        return (-size * th / 1000.0, 0.0)

    def _tj(self, operands: list[Any], op: Any) -> Instruction:
        import pikepdf

        if len(operands) != 1 or not isinstance(operands[0], pikepdf.Array):
            return operands, op
        items = list(operands[0])
        kerns = [x for x in items if not isinstance(x, pikepdf.String)]
        self._stats.kerns_before += len(kerns)
        unit = self._kern_unit()
        if unit is None or any(_number(x) is None for x in kerns):
            self._stats.kerns_after += len(kerns)
            return operands, op
        out = _rebuild(items, lambda kern: self._kern(kern, unit))
        written = [x for x in out if not isinstance(x, bytes)]
        self._stats.kerns_after += len(written)
        if len(written) == len(kerns) and all(a is b for a, b in zip(written, kerns)):
            return operands, op
        if len(out) == 1 and not written:
            return [pikepdf.String(out[0])], pikepdf.Operator("Tj")
        return [pikepdf.Array([pikepdf.String(x) if isinstance(x, bytes) else x for x in out])], op

    def _kern(self, item: Any, unit: tuple[float, float]) -> Any:
        """What to write in place of kern `item` (None drops it); updates pos_err."""
        n = float(item)
        ux, uy = unit
        ex, ey = self.pos_err
        paid_back = n - (ex * ux + ey * uy) / (ux * ux + uy * uy)  # cancels pos_err
        value, self.pos_err = self._best(
            [(None, 0.0), *_candidates(n, paid_back, item)],
            lambda v: (ex + (v - n) * ux, ey + (v - n) * uy),
            lambda w: 0 if w is None else len(str(w)),
        )
        if value is not item:
            self._stats.numbers_shortened += 1
        return value


def _rebuild(items: list[Any], kern_for: Callable[[Any], Any]) -> list[Any]:
    """TJ items with each kern replaced by kern_for(kern); strings a dropped kern
    separated are joined. Strings come back as bytes."""
    import pikepdf

    out: list[Any] = []
    for item in items:
        if not isinstance(item, pikepdf.String):
            kern = kern_for(item)
            if kern is not None:
                out.append(kern)
        elif out and isinstance(out[-1], bytes):
            out[-1] += bytes(item)
        else:
            out.append(bytes(item))
    return out


def round_text_positions(
    instructions: list[Instruction],
    *,
    tolerance: float,
    is_vertical: Callable[[str], bool | None],
    uses: Sequence[Linear] = (IDENTITY,),
    horizontal_scale: float | None = 1.0,
    stats: RoundTextStats | None = None,
) -> list[Instruction]:
    """Rewrites a content stream's text positioning; see the module docstring.

    `uses` are the linear parts of every matrix the stream is drawn with
    (the identity, for a page). `horizontal_scale` is Tz/100 at the start,
    or None if unknown. The font size is unknown at the start.
    """
    stats = stats if stats is not None else RoundTextStats()
    state = _State(th=horizontal_scale)
    stack: list[_State] = []
    out: list[Instruction] = []
    i = 0
    while i < len(instructions):
        operands, op = instructions[i]
        name = str(op)
        if name != "BT":
            state = _outside_text(state, stack, operands, name, is_vertical)
            out.append((operands, op))
            i += 1
            continue
        end = _text_object_end(instructions, i)
        if end is None:
            stats.text_objects_skipped += 1
            return out + list(instructions[i:])
        stats.text_objects += 1
        obj = _TextObject(state, uses, tolerance, is_vertical, stats)
        out.extend(obj.run(list(instructions[i : end + 1])))
        state = obj.state
        i = end + 1
    return out


def _text_object_end(instructions: Sequence[Instruction], start: int) -> int | None:
    """Index of the ET closing the BT at `start`, if everything between is modelled."""
    for j in range(start + 1, len(instructions)):
        name = str(instructions[j][1])
        if name == "ET":
            return j
        if name not in _SAFE_IN_TEXT:
            return None
    return None


def _outside_text(state, stack, operands, name, is_vertical) -> _State:
    if name == "q":
        stack.append(state)
    elif name == "Q":
        state = stack.pop() if stack else _State(ctm=None, th=None)
    elif name == "cm":
        m = _matrix(operands)
        state = replace(state, ctm=None if m is None or state.ctm is None else _mul(m, state.ctm))
    elif name in ("Tf", "Tz"):
        state = _state_after(state, operands, name, is_vertical)
    elif name == "gs":
        state = replace(state, size=None)  # an ExtGState may set /Font
    return state
