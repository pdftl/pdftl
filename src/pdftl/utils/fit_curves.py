# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/fit_curves.py

"""
Numba-accelerated implementation of Philip J. Schneider's
"Algorithm for Automatically Fitting Digitized Curves" (Graphics Gems, 1990).

Architecture
------------
The JIT kernels live in _fit_curves_jit.py, which imports numpy and numba at
module level. That module is intentionally excluded from auto-discovery by
registry_init (its leading underscore causes it to be skipped by
_discover_modules), so numpy and numba are never imported at process startup.

On the first call to fit_cubic() or fit_points(), _get_jit() imports
_fit_curves_jit and caches it in _jit_mod. Subsequent calls pay only a
single global lookup. numba's cache=True means compiled bytecode is written
to __pycache__ on first run, so later process starts load from disk rather
than recompiling.

This module (fit_curves.py) contains only stdlib imports at module level and
is safe to import at any time with zero cost.

Interface contract for JIT kernels (defined in _fit_curves_jit.py)
-------------------------------------------------------------------
  pts     : np.ndarray shape (N, 2), dtype float64, C-contiguous
  u       : np.ndarray shape (N,),   dtype float64, C-contiguous
  ctrl    : np.ndarray shape (4, 2), dtype float64, C-contiguous
  tl0/tl1 : plain Python float (tangent components)
  tr0/tr1 : plain Python float (tangent components)
"""

import math

_jit_mod = None


def _get_jit():
    global _jit_mod
    if _jit_mod is None:
        from pdftl.utils import _fit_curves_jit

        _jit_mod = _fit_curves_jit
    return _jit_mod


# ---------------------------------------------------------------------------
# Public helper API (unchanged — used by callers outside this module)
# ---------------------------------------------------------------------------


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _mul(v, s):
    return (v[0] * s, v[1] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _len(v):
    return math.hypot(v[0], v[1])


def _normalize(v):
    length = math.hypot(v[0], v[1])
    return (v[0] / length, v[1] / length) if length > 1e-9 else (0.0, 0.0)


# ---------------------------------------------------------------------------
# Internal conversion helper
# ---------------------------------------------------------------------------


def _ctrl_to_list(c):
    """Convert (4,2) ndarray ctrl to list of 4 (x, y) tuples."""
    return [
        (float(c[0, 0]), float(c[0, 1])),
        (float(c[1, 0]), float(c[1, 1])),
        (float(c[2, 0]), float(c[2, 1])),
        (float(c[3, 0]), float(c[3, 1])),
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fit_cubic(points, left_tangent, right_tangent, max_error, max_error_scale):
    """Fit one or more cubic Béziers to *points* within *max_error*.

    *points* may be a list-of-tuples or ndarray.
    *left_tangent*, *right_tangent* may be tuples or ndarrays.
    Returns a list of [p0, p1, p2, p3] control-point tuples.
    """
    jit = _get_jit()
    import numpy as np

    tl0, tl1 = float(left_tangent[0]), float(left_tangent[1])
    tr0, tr1 = float(right_tangent[0]), float(right_tangent[1])

    if isinstance(points, np.ndarray):
        pts = np.ascontiguousarray(points, dtype=np.float64)
    else:
        pts = np.array(points, dtype=np.float64)

    n = len(pts)

    if n < 2:
        raise ValueError("fit_cubic requires at least 2 points")

    if n == 2:
        p0x, p0y = float(pts[0, 0]), float(pts[0, 1])
        p1x, p1y = float(pts[1, 0]), float(pts[1, 1])
        dist = math.hypot(p1x - p0x, p1y - p0y) / 3.0
        return [
            [
                (p0x, p0y),
                (p0x + tl0 * dist, p0y + tl1 * dist),
                (p1x + tr0 * dist, p1y + tr1 * dist),
                (p1x, p1y),
            ]
        ]

    u = jit.chord_length_parameterize(pts)
    ctrl = np.zeros((4, 2), dtype=np.float64)
    jit.generate_bezier(pts, u, ctrl, tl0, tl1, tr0, tr1)
    max_e, split = jit.find_max_error(pts, ctrl, u)

    if max_e < max_error:
        return [_ctrl_to_list(ctrl)]

    if max_e < max_error * max_error_scale:
        for _ in range(4):
            u = jit.reparameterize(ctrl, pts, u)
            jit.generate_bezier(pts, u, ctrl, tl0, tl1, tr0, tr1)
            max_e, split = jit.find_max_error(pts, ctrl, u)
            if max_e < max_error:
                return [_ctrl_to_list(ctrl)]

    return _split_and_recurse_fit_cubic(pts, split, tl0, tl1, tr0, tr1, max_error, max_error_scale)


def _split_and_recurse_fit_cubic(pts, split, tl0, tl1, tr0, tr1, max_error, max_error_scale):
    # Split and recurse — ndarray slices are O(1) views (no copy).
    sx, sy = float(pts[split, 0]), float(pts[split, 1])
    ax, ay = float(pts[split - 1, 0]), float(pts[split - 1, 1])
    bx, by = float(pts[split + 1, 0]), float(pts[split + 1, 1])

    tan_lx = ax - sx
    tan_ly = ay - sy
    tan_rx = sx - bx
    tan_ry = sy - by

    len_l = math.hypot(tan_lx, tan_ly)
    len_r = math.hypot(tan_rx, tan_ry)

    if len_l > 1e-9:
        tan_lx /= len_l
        tan_ly /= len_l
    else:
        tan_lx = tan_ly = 0.0
    if len_r > 1e-9:
        tan_rx /= len_r
        tan_ry /= len_r
    else:
        tan_rx = tan_ry = 0.0

    cx = tan_lx + tan_rx
    cy = tan_ly + tan_ry
    len_c = math.hypot(cx, cy)
    if len_c > 1e-9:
        cx /= len_c
        cy /= len_c
    else:
        cx = cy = 0.0

    beziers = []
    beziers.extend(fit_cubic(pts[: split + 1], (tl0, tl1), (cx, cy), max_error, max_error_scale))
    beziers.extend(fit_cubic(pts[split:], (-cx, -cy), (tr0, tr1), max_error, max_error_scale))
    return beziers


def fit_points(points, max_error=0.15, max_error_scale=4.0):
    """Fit cubic Bézier curves to a flat sequence of (x, y) points."""
    import numpy as np

    if isinstance(points, np.ndarray):
        pts = np.ascontiguousarray(points, dtype=np.float64)
    else:
        pts = np.array(points, dtype=np.float64)

    n = len(pts)
    if n < 2:
        raise ValueError(f"fit_points requires at least 2 points, got {n}")

    tl0 = pts[1, 0] - pts[0, 0]
    tl1 = pts[1, 1] - pts[0, 1]
    tr0 = pts[-2, 0] - pts[-1, 0]
    tr1 = pts[-2, 1] - pts[-1, 1]

    len_l = math.hypot(tl0, tl1)
    len_r = math.hypot(tr0, tr1)
    if len_l > 1e-9:
        tl0 /= len_l
        tl1 /= len_l
    else:
        tl0 = tl1 = 0.0
    if len_r > 1e-9:
        tr0 /= len_r
        tr1 /= len_r
    else:
        tr0 = tr1 = 0.0

    return fit_cubic(pts, (tl0, tl1), (tr0, tr1), max_error, max_error_scale)
