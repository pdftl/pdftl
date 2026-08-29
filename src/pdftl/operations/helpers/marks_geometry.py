# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/helpers/marks_geometry.py

# Portions derived from printer_marks.py (Spectra-PDF) by Jason Ulbright.
# Used under the MIT License (see NOTICES.md)

# MIT License
#
# Copyright (c) 2026 Jason Ulbright
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""Pure geometry helpers for printer marks (crop marks, registration
targets). No pikepdf writes here beyond reading page box arrays -- kept
testable in isolation from the parse/render layers.
"""

from __future__ import annotations

import math

from pdftl.utils.pikepdf_helpers import get_inheritable

# Stroke weights a press expects, in points.
WEIGHTS = (0.125, 0.25, 0.5)
MARK_STYLES = ("western", "japanese")

# PDF's own page-extent implementation limit. A growth past it is refused
# rather than written, because a viewer's behaviour past it is undefined.
MAX_PAGE_EXTENT = 14400.0

# Bézier circle constant -- four arcs approximate a circle to ~0.02%.
_KAPPA = 0.5522847498

# Diameter count for the star/slur target: pi/18 rad (10 deg) spacing.
STAR_TARGET_DIAMETERS = 18

# Bleed assumed for a Japanese double crop mark when no /BleedBox is
# declared: 3mm, the printing trade's own default.
DEFAULT_BLEED_PT = 8.5


def n(value: float) -> str:
    """Format a float for PDF content-stream operands: trailing zeros trimmed."""
    rounded = round(float(value), 4)
    if rounded == 0.0:
        return "0"
    text = f"{rounded:.4f}".rstrip("0").rstrip(".")
    return text if text else "0"


def read_box(page, key: str) -> tuple[float, float, float, float] | None:
    """The page's box (own or inherited), normalized to (x0,y0,x1,y1) with
    x0<=x1, y0<=y1. None if absent or malformed."""
    value = get_inheritable(page, key)
    if value is None:
        return None
    try:
        x0, y0, x1, y1 = (float(value[i]) for i in range(4))
    except (TypeError, ValueError, IndexError):
        return None
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def resolve_trim(page) -> tuple[tuple[float, float, float, float], str]:
    """The trim rectangle to mark, and which box it came from: explicit
    /TrimBox, else /CropBox, else /MediaBox. The source is reported because
    a document with no trim box is being guessed at."""
    for key, label in (("/TrimBox", "trim"), ("/CropBox", "crop"), ("/MediaBox", "media")):
        box = read_box(page, key)
        if box is not None:
            return box, label
    return (0.0, 0.0, 612.0, 792.0), "default"


def resolve_bleed(page, trim) -> float:
    """Bleed distance for a Japanese double crop mark: the page's own
    /BleedBox margin over trim if declared and positive, else the 3mm
    print-trade default."""
    bleed_box = read_box(page, "/BleedBox")
    if bleed_box is not None:
        declared = min(
            trim[0] - bleed_box[0],
            trim[1] - bleed_box[1],
            bleed_box[2] - trim[2],
            bleed_box[3] - trim[3],
        )
        if declared > 0:
            return declared
    return DEFAULT_BLEED_PT


def grow_box(box, margin: float) -> tuple[float, float, float, float]:
    return (box[0] - margin, box[1] - margin, box[2] + margin, box[3] + margin)


def crop_mark_segments(
    trim, offset: float, length: float, style: str, bleed: float = 0.0
) -> list[tuple[float, float, float, float]]:
    """Crop-mark line segments for one page, in page user space.

    Western style is one L-pair per corner: each arm starts `offset` outside
    the trim and runs `length` further out, so no arm ever crosses the trim.
    Japanese style adds a second, parallel pair at the bleed distance (the
    "double" crop mark, whose gap between the two lines IS the bleed
    indicator) plus a centre mark on each edge.
    """
    x0, y0, x1, y1 = trim
    inner = offset
    outer = offset + length
    segments: list[tuple[float, float, float, float]] = []

    def corner(cx: float, cy: float, sx: int, sy: int, shift: float) -> None:
        hy = cy + sy * shift
        segments.append((cx - sx * outer, hy, cx - sx * inner, hy))
        vx = cx + sx * shift
        segments.append((vx, cy - sy * outer, vx, cy - sy * inner))

    shifts = [0.0] if style != "japanese" else [0.0, bleed]
    for shift in shifts:
        corner(x0, y0, 1, 1, shift)
        corner(x1, y0, -1, 1, shift)
        corner(x0, y1, 1, -1, shift)
        corner(x1, y1, -1, -1, shift)

    if style == "japanese":
        mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        half = length / 2.0
        for edge_y, sy in ((y0, 1), (y1, -1)):
            for dx in (-half, half):
                segments.append((mx + dx, edge_y - sy * outer, mx + dx, edge_y - sy * inner))
        for edge_x, sx in ((x0, 1), (x1, -1)):
            for dy in (-half, half):
                segments.append((edge_x - sx * outer, my + dy, edge_x - sx * inner, my + dy))
    return segments


def registration_centres(trim, offset: float, length: float) -> list[tuple[float, float, float]]:
    """(x, y, radius) for the four edge-midpoint registration targets."""
    x0, y0, x1, y1 = trim
    mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    radius = max(1.0, length * 0.35)
    out = offset + length / 2.0
    return [
        (mx, y1 + out, radius),
        (mx, y0 - out, radius),
        (x0 - out, my, radius),
        (x1 + out, my, radius),
    ]


def circle_ops(cx: float, cy: float, r: float) -> bytes:
    """Bézier-approximated circle path operators (no paint operator appended)."""
    k = _KAPPA * r
    return (
        f"{n(cx + r)} {n(cy)} m "
        f"{n(cx + r)} {n(cy + k)} {n(cx + k)} {n(cy + r)} {n(cx)} {n(cy + r)} c "
        f"{n(cx - k)} {n(cy + r)} {n(cx - r)} {n(cy + k)} {n(cx - r)} {n(cy)} c "
        f"{n(cx - r)} {n(cy - k)} {n(cx - k)} {n(cy - r)} {n(cx)} {n(cy - r)} c "
        f"{n(cx + k)} {n(cy - r)} {n(cx + r)} {n(cy - k)} {n(cx + r)} {n(cy)} c h"
    ).encode("ascii")


def star_target_segments(
    cx: float, cy: float, radius: float, count: int = STAR_TARGET_DIAMETERS
) -> list[tuple[float, float, float, float]]:
    """Line segments for a slur/doubling target: `count` diameters through
    a shared centre, evenly spaced over a half-turn (each diameter already
    covers both opposite rays, so `count` diameters give `2 * count` visible
    spokes). Fine angular spacing is the point of the target -- with too
    few diameters the gaps between spokes are wide enough that ordinary
    slur can't visibly bridge them, defeating the diagnostic."""
    segments = []
    for i in range(count):
        angle = math.pi * i / count
        dx = radius * math.cos(angle)
        dy = radius * math.sin(angle)
        segments.append((cx - dx, cy - dy, cx + dx, cy + dy))
    return segments
