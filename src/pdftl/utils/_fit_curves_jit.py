# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/_fit_curves.py

"""
JIT helpers for fit_curves.py

Note: this file should not be auto-imported by registry_init.
So we can freely import at module level.
"""

import math
import numpy as np
from numba import njit


@njit(cache=True)
def chord_length_parameterize(pts):
    n = len(pts)
    u = np.empty(n, np.float64)
    u[0] = 0.0
    total = 0.0
    for i in range(1, n):
        dx = pts[i, 0] - pts[i - 1, 0]
        dy = pts[i, 1] - pts[i - 1, 1]
        total += math.sqrt(dx * dx + dy * dy)
        u[i] = total
    if total > 1e-9:
        inv = 1.0 / total
        for i in range(1, n):
            u[i] *= inv
    else:
        inv = 1.0 / (n - 1)
        for i in range(1, n):
            u[i] = i * inv
    return u


@njit(cache=True)
def generate_bezier(pts, u, ctrl_out, tl0, tl1, tr0, tr1):
    n = len(pts)
    p0x = pts[0, 0]
    p0y = pts[0, 1]
    p1x = pts[n - 1, 0]
    p1y = pts[n - 1, 1]

    tl_sq = tl0 * tl0 + tl1 * tl1
    tr_sq = tr0 * tr0 + tr1 * tr1
    tl_tr = tl0 * tr0 + tl1 * tr1

    c00 = 0.0
    c01 = 0.0
    c11 = 0.0
    x0 = 0.0
    x1 = 0.0

    for i in range(n):
        px = pts[i, 0]
        py = pts[i, 1]
        t = u[i]
        s = 1.0 - t
        s2 = s * s
        t2 = t * t
        bl = 3.0 * s2 * t
        br = 3.0 * s * t2
        w_s = s2 * (1.0 + 2.0 * t)
        w_e = t2 * (3.0 - 2.0 * t)
        dx = px - (p0x * w_s + p1x * w_e)
        dy = py - (p0y * w_s + p1y * w_e)
        c00 += bl * bl * tl_sq
        c11 += br * br * tr_sq
        c01 += bl * br * tl_tr
        x0 += bl * (tl0 * dx + tl1 * dy)
        x1 += br * (tr0 * dx + tr1 * dy)

    det = c00 * c11 - c01 * c01
    if abs(det) > 1e-9:
        alpha_l = (x0 * c11 - x1 * c01) / det
        alpha_r = (c00 * x1 - c01 * x0) / det
    else:
        alpha_l = 0.0
        alpha_r = 0.0

    seg_dx = p1x - p0x
    seg_dy = p1y - p0y
    seg = math.sqrt(seg_dx * seg_dx + seg_dy * seg_dy)
    eps = 1e-6 * seg
    if alpha_l < eps or alpha_r < eps:
        d = seg / 3.0
        ctrl_out[0, 0] = p0x
        ctrl_out[0, 1] = p0y
        ctrl_out[1, 0] = p0x + tl0 * d
        ctrl_out[1, 1] = p0y + tl1 * d
        ctrl_out[2, 0] = p1x + tr0 * d
        ctrl_out[2, 1] = p1y + tr1 * d
        ctrl_out[3, 0] = p1x
        ctrl_out[3, 1] = p1y
    else:
        ctrl_out[0, 0] = p0x
        ctrl_out[0, 1] = p0y
        ctrl_out[1, 0] = p0x + tl0 * alpha_l
        ctrl_out[1, 1] = p0y + tl1 * alpha_l
        ctrl_out[2, 0] = p1x + tr0 * alpha_r
        ctrl_out[2, 1] = p1y + tr1 * alpha_r
        ctrl_out[3, 0] = p1x
        ctrl_out[3, 1] = p1y
    return ctrl_out


@njit(cache=True)
def reparameterize(ctrl, pts, u):
    n = len(pts)
    c0x = ctrl[0, 0]
    c0y = ctrl[0, 1]
    c1x = ctrl[1, 0]
    c1y = ctrl[1, 1]
    c2x = ctrl[2, 0]
    c2y = ctrl[2, 1]
    c3x = ctrl[3, 0]
    c3y = ctrl[3, 1]
    new_u = np.empty(n, np.float64)
    for i in range(n):
        px = pts[i, 0]
        py = pts[i, 1]
        t = u[i]
        s = 1.0 - t
        s2 = s * s
        t2 = t * t
        b0 = s2 * s
        b1 = 3.0 * s2 * t
        b2 = 3.0 * s * t2
        b3 = t2 * t
        Qx = c0x * b0 + c1x * b1 + c2x * b2 + c3x * b3
        Qy = c0y * b0 + c1y * b1 + c2y * b2 + c3y * b3
        bp0 = -3.0 * s2
        bp1 = 3.0 * s2 - 6.0 * s * t
        bp2 = 6.0 * s * t - 3.0 * t2
        bp3 = 3.0 * t2
        Qpx = c0x * bp0 + c1x * bp1 + c2x * bp2 + c3x * bp3
        Qpy = c0y * bp0 + c1y * bp1 + c2y * bp2 + c3y * bp3
        bpp0 = 6.0 * s
        bpp1 = -12.0 * s + 6.0 * t
        bpp2 = 6.0 * s - 12.0 * t
        bpp3 = 6.0 * t
        Qppx = c0x * bpp0 + c1x * bpp1 + c2x * bpp2 + c3x * bpp3
        Qppy = c0y * bpp0 + c1y * bpp1 + c2y * bpp2 + c3y * bpp3
        dx = Qx - px
        dy = Qy - py
        num = dx * Qpx + dy * Qpy
        den = Qpx * Qpx + Qpy * Qpy + dx * Qppx + dy * Qppy
        if abs(den) > 1e-9:
            v = t - num / den
        else:
            v = t
        if v < 0.0:
            v = 0.0
        elif v > 1.0:
            v = 1.0
        new_u[i] = v
    return new_u


@njit(cache=True)
def find_max_error(pts, ctrl, u):
    n = len(pts)
    c0x = ctrl[0, 0]
    c0y = ctrl[0, 1]
    c1x = ctrl[1, 0]
    c1y = ctrl[1, 1]
    c2x = ctrl[2, 0]
    c2y = ctrl[2, 1]
    c3x = ctrl[3, 0]
    c3y = ctrl[3, 1]
    max_e = 0.0
    split = n // 2
    for i in range(n):
        px = pts[i, 0]
        py = pts[i, 1]
        t = u[i]
        s = 1.0 - t
        s2 = s * s
        t2 = t * t
        b0 = s2 * s
        b1 = 3.0 * s2 * t
        b2 = 3.0 * s * t2
        b3 = t2 * t
        bx = c0x * b0 + c1x * b1 + c2x * b2 + c3x * b3
        by = c0y * b0 + c1y * b1 + c2y * b2 + c3y * b3
        e = math.sqrt((bx - px) ** 2 + (by - py) ** 2)
        if e > max_e:
            max_e = e
            split = i
    lo = 1
    hi = n - 2
    if split < lo:
        split = lo
    if split > hi:
        split = hi
    return max_e, split
