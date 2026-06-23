# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/path_geometry.py

"""
Pure geometry utilities for the simplify_vectors pipeline.

All functions here are stateless and free of I/O.  They operate on plain
Python floats and tuples so they can be unit-tested independently of pikepdf.

Public API
----------
sample_cubic_bezier(p0, p1, p2, p3, tolerance) -> list[(x, y)]
    Sample a cubic Bézier to an adaptive point sequence.

rdp_simplify(points, tolerance) -> list[(x, y)]
    Ramer-Douglas-Peucker polyline simplification.

simplify_path(path, config) -> SimplifiedPath
    Stage 3 of the pipeline: pure-function path simplification.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from pdftl.utils.path_types import Path, SimplifiedPath, SimplifyConfig, Subpath

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bézier sampling
# ---------------------------------------------------------------------------


def sample_cubic_bezier(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    tolerance: float,
) -> list[tuple[float, float]]:
    """Sample a cubic Bézier to an adaptive sequence of (x, y) points.

    The first output point is the endpoint p3 (not p0, which is already the
    last point of the preceding subpath segment).  Sample density adapts to
    estimated curve length and the current tolerance.

    Formula:
        chord      = |p3 − p0|
        control    = |p1 − p0| + |p2 − p1| + |p3 − p2|
        est_length = (chord + control) / 2
        step       = max(tolerance / 2, 0.25)
        n_samples  = clamp(ceil(est_length / step), 4, 64)

    Args:
        p0, p1, p2, p3: Control points (p0 is the start, already in caller's list).
        tolerance:      Current user-space tolerance — used to scale sample density.

    Returns:
        List of sampled points from t=1/n to t=1 inclusive (i.e. p3 is last).
    """
    p0x, p0y = p0
    p1x, p1y = p1
    p2x, p2y = p2
    p3x, p3y = p3

    # Fast math.hypot inline equivalence where possible, or direct local cache
    _hypot = math.hypot

    chord = _hypot(p3x - p0x, p3y - p0y)
    control = (
        _hypot(p1x - p0x, p1y - p0y) + _hypot(p2x - p1x, p2y - p1y) + _hypot(p3x - p2x, p3y - p2y)
    )
    est_length = (chord + control) * 0.5

    step = max(tolerance * 0.5, 0.25)

    # Inline clamping instead of nested min/max function call overhead
    n_samples = math.ceil(est_length / step)
    if n_samples < 4:
        n_samples = 4
    elif n_samples > 64:
        n_samples = 64

    pts = []
    # Local variable caching for tight loop speed
    append_pt = pts.append
    inv_n = 1.0 / n_samples

    for i in range(1, n_samples + 1):
        t = i * inv_n
        s = 1.0 - t

        s2 = s * s
        t2 = t * t

        b0 = s2 * s
        b1 = 3.0 * s2 * t
        b2 = 3.0 * s * t2
        b3 = t2 * t

        append_pt(
            (p0x * b0 + p1x * b1 + p2x * b2 + p3x * b3, p0y * b0 + p1y * b1 + p2y * b2 + p3y * b3)
        )

    return pts


# ---------------------------------------------------------------------------
# RDP simplification
# ---------------------------------------------------------------------------


def _perp_distance(
    p: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    """Perpendicular distance from point p to line segment a→b."""
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    seg_len = math.hypot(dx, dy)
    if seg_len < 1e-9:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    return abs(dy * p[0] - dx * p[1] + b[0] * a[1] - b[1] * a[0]) / seg_len


def rdp_simplify(
    points: list[tuple[float, float]],
    tolerance: float,
) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker polyline simplification.

    Args:
        points:    Input polyline as (x, y) pairs.  At least 2 points.
        tolerance: Maximum allowed perpendicular deviation from the chord.

    Returns:
        Simplified point list (always includes first and last point).
    """
    if len(points) < 3:
        return list(points)

    # Find the point with maximum perpendicular distance from the chord
    dmax = 0.0
    index = 0
    end = len(points) - 1
    for i in range(1, end):
        d = _perp_distance(points[i], points[0], points[end])
        if d > dmax:
            index = i
            dmax = d

    if dmax > tolerance:
        left = rdp_simplify(points[: index + 1], tolerance)
        right = rdp_simplify(points[index:], tolerance)
        return left[:-1] + right

    return [points[0], points[end]]


# ---------------------------------------------------------------------------
# Coordinate serialisation helpers
# ---------------------------------------------------------------------------


def _prec(effective_tol: float) -> int:
    """Decimal precision for output coordinates based on effective tolerance."""
    if effective_tol < 0.1:
        return 4
    if effective_tol < 1.0:
        return 3
    return 2


def _r(v: float, prec: int) -> float:
    return round(v, prec)


# ---------------------------------------------------------------------------
# Stage 3: simplify_path (pure function)
# ---------------------------------------------------------------------------


def simplify_path(path: Path, config: SimplifyConfig) -> SimplifiedPath:
    """Simplify a single path according to config.

    This is a **pure function** — it reads path and config, writes nothing,
    mutates nothing, raises no exceptions (errors produce fell_back=True).

    Fallback rules (conservative):
    1. Path is a clipping path and config.clip_paths is False → fall back.
    2. paint_op is None (interrupted path) → fall back.
    3. Any subpath has fewer than config.min_points points → fall back entire path.
    4. Any subpath's simplification raises an exception → fall back entire path.
    5. Simplified result has >= original operator count → skip that subpath.
       If any subpath is skipped entirely, fall back entire path for safety
       (avoids seams at subpath joins).

    Args:
        path:   A Path object produced by Stage 2.
        config: Immutable SimplifyConfig for this operation.

    Returns:
        A SimplifiedPath (fell_back=True means use path.original_instructions).
    """
    # --- Early exits ---
    if path.paint_op is None:
        return _fallback(path)

    if path.is_clipping and not config.clip_paths:
        return _fallback(path)

    for subpath in path.subpaths:
        if len(subpath.points) < config.min_points:
            return _fallback(path)

    # --- Per-subpath simplification ---
    all_ops: list[tuple[list[float], str]] = []

    for subpath in path.subpaths:
        result = _simplify_subpath(subpath, config)
        if result is None:
            # Simplification made no improvement or raised — fall back entire path
            return _fallback(path)
        all_ops.extend(result)

    return SimplifiedPath(
        subpath_instructions=all_ops,
        paint_op=path.paint_op,
        fell_back=False,
    )


def _fallback(path: Path) -> SimplifiedPath:
    """Produce a fell_back SimplifiedPath signalling use of original instructions."""
    return SimplifiedPath(
        subpath_instructions=[],
        paint_op=path.paint_op or "",
        fell_back=True,
    )


def _simplify_subpath(
    subpath: Subpath,
    config: SimplifyConfig,
) -> list[tuple[list[float], str]] | None:
    """Simplify one subpath.  Returns None to signal 'fall back entire path'."""
    # Compute effective tolerance in user space
    effective_tol = max(0.01, min(config.tolerance / subpath.ctm_scale, 100.0))
    prec = _prec(effective_tol)
    pts = subpath.points

    if subpath.has_curves and config.curves:
        return _simplify_curved(
            pts,
            effective_tol,
            prec,
            subpath.closed,
            subpath.original_op_count,
            config.max_error_scale,
        )

    if not subpath.has_curves and config.lines:
        return _simplify_linear(
            pts, effective_tol, prec, subpath.closed, subpath.original_op_count
        )

    # Config disables this class of simplification — emit as-is as 'l' ops
    return _emit_linear(pts, prec, subpath.closed)


def _simplify_curved(
    pts: list[tuple[float, float]],
    tol: float,
    prec: int,
    closed: bool,
    original_op_count: int,
    max_error_scale: float,
) -> list[tuple[list[float], str]] | None:
    """Attempt Schneider curve fitting.  Returns None on failure or no gain."""
    from pdftl.utils.fit_curves import fit_points

    try:
        fitted = fit_points(pts, max_error=tol, max_error_scale=max_error_scale)
    except ValueError as exc:
        logger.warning("Schneider fit_points raised %s: %s", type(exc).__name__, exc)
        return None

    if not fitted:
        return None

    # 1 'm' + N 'c' operators
    fitted_op_count = 1 + len(fitted)
    if fitted_op_count >= original_op_count and original_op_count > 0:
        # Fitting made it worse or equal — signal whole-path fallback
        return None

    ops: list[tuple[list[float], str]] = []
    start = fitted[0][0]
    ops.append(([_r(start[0], prec), _r(start[1], prec)], "m"))

    for curve in fitted:
        _, cp1, cp2, ep = curve
        ops.append(
            (
                [
                    _r(cp1[0], prec),
                    _r(cp1[1], prec),
                    _r(cp2[0], prec),
                    _r(cp2[1], prec),
                    _r(ep[0], prec),
                    _r(ep[1], prec),
                ],
                "c",
            )
        )

    if closed:
        ops.append(([], "h"))

    return ops


def _simplify_linear(
    pts: list[tuple[float, float]],
    tol: float,
    prec: int,
    closed: bool,
    original_op_count: int,
) -> list[tuple[list[float], str]] | None:
    """Apply RDP to a linear subpath.  Returns None on failure or no gain."""
    try:
        simplified = rdp_simplify(pts, tol)
    except ZeroDivisionError as exc:
        logger.warning("RDP simplification raised %s: %s", type(exc).__name__, exc)
        return None

    # 1 'm' + (N-1) 'l' operators
    simplified_op_count = len(simplified)
    if simplified_op_count >= original_op_count and original_op_count > 0:
        return None

    return _emit_linear(simplified, prec, closed)


def _emit_linear(
    pts: list[tuple[float, float]],
    prec: int,
    closed: bool,
) -> list[tuple[list[float], str]]:
    """Emit 'm' + 'l'... (+ 'h') for a linear point sequence."""
    ops: list[tuple[list[float], str]] = []
    ops.append(([_r(pts[0][0], prec), _r(pts[0][1], prec)], "m"))
    for pt in pts[1:]:
        ops.append(([_r(pt[0], prec), _r(pt[1], prec)], "l"))
    if closed:
        ops.append(([], "h"))
    return ops
