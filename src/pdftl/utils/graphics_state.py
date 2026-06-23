# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/graphics_state.py

"""
Lightweight PDF graphics state tracker for the subset of state that affects
geometric simplification: the current transformation matrix (CTM), current
line width, and whether the next path is a clipping path.

Only the operators that change these properties need to be tracked:
    q / Q   — save / restore
    cm      — concatenate matrix
    w       — line width
    W / W*  — next path is a clipping path (reset after painting op)

All other graphics state (colour, dash, blend mode, etc.) is ignored here;
it passes through the content stream unchanged.

Public API
----------
GraphicsState           dataclass — carries CTM, line_width, is_clipping
GraphicsStateStack      thin wrapper around a list[GraphicsState]
ctm_scale(ctm)          approximate uniform scale of a 6-element CTM
"""

from __future__ import annotations

import math
from copy import copy
from dataclasses import dataclass, field

# PDF spec §8.3.4 — maximum graphics state stack depth
_MAX_STACK_DEPTH = 32

# Identity CTM
_IDENTITY_CTM: tuple[float, ...] = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _multiply_ctm(m: tuple[float, ...], n: tuple[float, ...]) -> tuple[float, ...]:
    """Concatenate two 6-element affine matrices.

    PDF column-vector convention:
        [a b 0]   [a2 b2 0]
        [c d 0] × [c2 d2 0]  →  result
        [e f 1]   [e2 f2 1]
    """
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = n
    return (
        a * a2 + b * c2,
        a * b2 + b * d2,
        c * a2 + d * c2,
        c * b2 + d * d2,
        e * a2 + f * c2 + e2,
        e * b2 + f * d2 + f2,
    )


def ctm_scale(ctm: tuple[float, ...]) -> float:
    """Approximate the uniform scale factor of a CTM.

    Uses the RMS of the four linear components (a, b, c, d):
        scale ≈ sqrt((a² + b² + c² + d²) / 2)

    This is exact for uniform scales and rotations, and a reasonable
    approximation for mild non-uniform transforms.  For the purpose of
    mapping a device-space tolerance back to user space it is sufficient.

    Returns 1.0 if the matrix is degenerate (all linear components ≈ 0)
    to avoid division by zero at call sites.
    """
    a, b, c, d = ctm[0], ctm[1], ctm[2], ctm[3]
    rms = math.sqrt((a * a + b * b + c * c + d * d) / 2.0)
    return rms if rms > 1e-9 else 1.0


@dataclass
class GraphicsState:
    """The subset of PDF graphics state relevant to path simplification."""

    # 6-element row-major affine CTM: [a, b, c, d, e, f]
    ctm: tuple[float, ...] = field(default_factory=lambda: _IDENTITY_CTM)

    # Current line width in user space (set by the 'w' operator)
    line_width: float = 1.0

    # True when W or W* has been seen and the next painting op is a clip
    is_clipping: bool = False

    # ------------------------------------------------------------------
    # Mutation helpers (return self for convenience)
    # ------------------------------------------------------------------

    def apply_cm(self, operands: list) -> None:
        """Concatenate a new matrix from the 'cm' operator's 6 operands."""
        new_m: tuple[float, ...] = tuple(float(x) for x in operands)  # type: ignore[assignment]
        self.ctm = _multiply_ctm(new_m, self.ctm)

    def set_line_width(self, operands: list) -> None:
        """Update line width from the 'w' operator's operand."""
        try:
            self.line_width = float(operands[0])
        except (IndexError, TypeError, ValueError):
            pass  # malformed operator — keep current width

    def mark_clipping(self) -> None:
        """Called when W or W* is encountered."""
        self.is_clipping = True

    def consume_clipping(self) -> bool:
        """Called after a painting operator. Returns and clears the flag."""
        was = self.is_clipping
        self.is_clipping = False
        return was

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def scale(self) -> float:
        """Approximate uniform scale factor of the current CTM."""
        return ctm_scale(self.ctm)

    def user_space_tolerance(self, device_tol: float) -> float:
        """Convert a device-space tolerance to user space.

        Args:
            device_tol: Tolerance in device (output) space points.

        Returns:
            Tolerance in the current user space, clamped to [0.01, 100.0].
        """
        s = self.scale
        result = device_tol / s if s > 1e-9 else device_tol
        return max(0.01, min(result, 100.0))

    # ------------------------------------------------------------------
    # Stack helpers
    # ------------------------------------------------------------------

    def clone(self) -> GraphicsState:
        """Return a shallow copy suitable for pushing onto the save stack."""
        return copy(self)


class GraphicsStateStack:
    """A bounded stack of GraphicsState objects (mirrors q/Q operators).

    The *current* state is always ``stack.current``.  ``push()`` saves it;
    ``pop()`` restores the previous state.  Stack depth is capped at
    ``_MAX_STACK_DEPTH`` per the PDF specification.
    """

    def __init__(self) -> None:
        self._stack: list[GraphicsState] = []
        self.current: GraphicsState = GraphicsState()

    def push(self) -> None:
        """Save the current state (q operator)."""
        if len(self._stack) >= _MAX_STACK_DEPTH:
            # Spec says this is an error; we log and silently ignore.
            import logging

            logging.getLogger(__name__).warning(
                "Graphics state stack depth exceeded %d; ignoring 'q'.", _MAX_STACK_DEPTH
            )
            return
        self._stack.append(self.current.clone())

    def pop(self) -> None:
        """Restore the previous state (Q operator)."""
        if not self._stack:
            import logging

            logging.getLogger(__name__).warning("Graphics state stack underflow; ignoring 'Q'.")
            return
        self.current = self._stack.pop()

    def __len__(self) -> int:
        return len(self._stack)
