# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/path_types.py

"""
Typed intermediate representations for the simplify_vectors pipeline.

These dataclasses are the contract between pipeline stages — each stage
consumes one type and produces the next.  No geometry lives here.

Stage flow:
    content stream bytes
        → [Stage 1: parse]   → list[raw pikepdf instructions]
        → [Stage 2: segment] → list[Path | pass-through instruction]
        → [Stage 3: simplify]→ list[SimplifiedPath | pass-through instruction]
        → [Stage 4: serialize]→ content stream bytes
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pdftl.utils.graphics_state import GraphicsState


# ---------------------------------------------------------------------------
# Per-subpath representation
# ---------------------------------------------------------------------------


@dataclass
class Subpath:
    """A single contiguous stroke within a path.

    Points are in **user space** as sampled at construction time.
    Curves are expanded to point sequences by _sample_cubic_bezier so that
    Stage 3 only needs to handle a uniform point list regardless of the
    original operator mix.

    Attributes:
        points:         Sampled (x, y) coordinates in user space.
        closed:         True if the subpath ended with an 'h' operator.
        has_curves:     True if any 'c', 'v', or 'y' operator contributed
                        points (used to route to Schneider vs. RDP).
        ctm_scale:      Approximate uniform CTM scale at construction time,
                        used to convert device-space tolerance to user space.
        original_op_count: Number of original PDF operators that produced
                        this subpath (used to detect when simplification
                        would make things worse).
    """

    points: list[tuple[float, float]]
    closed: bool = False
    has_curves: bool = False
    ctm_scale: float = 1.0
    original_op_count: int = 0


# ---------------------------------------------------------------------------
# Complete path (one 'm...paint_op' sequence)
# ---------------------------------------------------------------------------


@dataclass
class Path:
    """A complete path: from the first 'm' to the painting operator.

    Attributes:
        subpaths:            All subpaths that make up this path.
        paint_op:            The operator that terminates this path
                             ('S', 'f', 'n', 'W', etc.).  None only for
                             interrupted paths that must fall back.
        original_instructions: Raw pikepdf (operands, operator) pairs for the
                             entire path including the painting op.  Used
                             verbatim when falling back.
        is_clipping:         True if paint_op is 'W' or 'W*'.
        state_snapshot:      Graphics state at the start of this path (for
                             tolerance conversion in Stage 3).
    """

    subpaths: list[Subpath]
    paint_op: str | None
    original_instructions: list[Any]
    is_clipping: bool = False
    state_snapshot: GraphicsState | None = None


# ---------------------------------------------------------------------------
# Simplified path (output of Stage 3)
# ---------------------------------------------------------------------------


@dataclass
class SimplifiedPath:
    """The result of simplifying a single Path.

    Attributes:
        subpath_instructions: New (operands, operator_str) pairs for the path
                              body (everything before the paint op).
        paint_op:             The original paint operator, re-emitted unchanged.
        fell_back:            True if the original_instructions were used
                              instead of the simplified form.
    """

    subpath_instructions: list[tuple[list[float], str]]
    paint_op: str
    fell_back: bool = False


# ---------------------------------------------------------------------------
# Configuration (derived from user parameters at operation start)
# ---------------------------------------------------------------------------


@dataclass
class SimplifyConfig:
    """Immutable configuration for the simplification stage.

    All attributes correspond 1:1 to user-facing parameters defined in the
    spec (§2).  Created once per operation invocation and passed through
    to every call of simplify_path().

    Attributes:
        tolerance:        Maximum allowed deviation in **device space** points.
        curves:           Whether to apply Schneider fitting to curved subpaths.
        lines:            Whether to apply RDP to linear subpaths.
        clip_paths:       Whether to simplify clipping paths (W / W*).
        min_points:       Minimum subpath length before simplification is tried.
        max_error_scale:  Reparameterization abandonment threshold multiplier.
    """

    tolerance: float = 0.15
    curves: bool = True
    lines: bool = True
    clip_paths: bool = False
    min_points: int = 4
    max_error_scale: float = 4.0
