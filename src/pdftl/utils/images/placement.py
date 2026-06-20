# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/images/placement.py

"""
Pure geometric computations for PDF image placement.

This module calculates physical dimensions, scaling factors, aspect ratio
constraints, anchor offsets, and transformation matrices ('cm' operators)
for placing an image on a PDF page relative to a target boundary (e.g., CropBox).
It contains zero PDF side-effects and operates on pure numeric values, allowing
callers to extract page boxes using page-level geometry utilities and calculate
positioning cleanly.
"""

from __future__ import annotations

from typing import Literal

AnchorType = Literal[
    "center",
    "top-left",
    "top-center",
    "top-right",
    "center-left",
    "center-right",
    "bottom-left",
    "bottom-center",
    "bottom-right",
]

ScaleMode = Literal["fit", "fill", "stretch", "none"]


def calculate_placement_matrix(
    *,
    img_size: tuple[float, float],
    box_bounds: tuple[float, float, float, float],
    requested_size: tuple[float | None, float | None] = (None, None),
    scale_mode: ScaleMode = "none",
    anchor: AnchorType = "bottom-left",
    offset: tuple[float, float] = (0.0, 0.0),
) -> tuple[float, float, float, float, float, float]:
    """
    Calculates the six components of the 2D affine transformation matrix
    (a, b, c, d, e, f) for the PDF 'cm' operator:
        a  0  0
        0  d  0
        e  f  1
    Which expands to: [a, 0, 0, d, e, f] where:
        - a: target width (horizontal scaling)
        - d: target height (vertical scaling)
        - e: translation X (horizontal translation)
        - f: translation Y (vertical translation)

    Args:
        img_size: Absolute intrinsic image dimensions (width, height) in pixels or points.
        box_bounds: Target page boundary (x1, y1, x2, y2) in PDF points (usually CropBox).
        requested_size: Tuple of (width, height) in PDF points. If either is None,
            it is resolved based on scale_mode or preserved aspect ratio.
        scale_mode: Scaling strategy ("fit", "fill", "stretch", "none").
        anchor: Positioning anchor alignment inside the target box.
        offset: Extra translation offset (dx, dy) in PDF points relative to the anchor position.

    Returns:
        tuple[float, float, float, float, float, float]: (a, b, c, d, e, f)
    """
    img_w, img_h = img_size
    bx1, by1, bx2, by2 = box_bounds
    box_w = bx2 - bx1
    box_h = by2 - by1

    if img_w <= 0 or img_h <= 0 or box_w <= 0 or box_h <= 0:
        return (0.0, 0.0, 0.0, 0.0, bx1, by1)

    # 1. Resolve targeted width and height before applying alignment anchors
    req_w, req_h = requested_size
    target_w, target_h = img_w, img_h

    if scale_mode == "stretch":
        target_w = req_w if req_w is not None else box_w
        target_h = req_h if req_h is not None else box_h
    elif scale_mode == "fit":
        # Fit image entirely within box while preserving aspect ratio
        limit_w = req_w if req_w is not None else box_w
        limit_h = req_h if req_h is not None else box_h
        scale = min(limit_w / img_w, limit_h / img_h)
        target_w = img_w * scale
        target_h = img_h * scale
    elif scale_mode == "fill":
        # Fill box completely with image while preserving aspect ratio
        limit_w = req_w if req_w is not None else box_w
        limit_h = req_h if req_h is not None else box_h
        scale = max(limit_w / img_w, limit_h / img_h)
        target_w = img_w * scale
        target_h = img_h * scale
    else:  # scale_mode == "none" or unspecified
        if req_w is not None and req_h is not None:
            # Explicitly sized but preserving aspect ratio was not forced by fit/fill/stretch
            target_w, target_h = req_w, req_h
        elif req_w is not None:
            target_w = req_w
            target_h = img_h * (req_w / img_w)
        elif req_h is not None:
            target_h = req_h
            target_w = img_w * (req_h / img_h)

    # 2. Compute translation relative to target box boundary and alignment anchor
    # Default origin is at (bx1, by1) (bottom-left of crop/media boundary)
    align_x, align_y = 0.0, 0.0

    if anchor == "center":
        align_x = (box_w - target_w) / 2.0
        align_y = (box_h - target_h) / 2.0
    elif anchor == "top-left":
        align_x = 0.0
        align_y = box_h - target_h
    elif anchor == "top-center":
        align_x = (box_w - target_w) / 2.0
        align_y = box_h - target_h
    elif anchor == "top-right":
        align_x = box_w - target_w
        align_y = box_h - target_h
    elif anchor == "center-left":
        align_x = 0.0
        align_y = (box_h - target_h) / 2.0
    elif anchor == "center-right":
        align_x = box_w - target_w
        align_y = (box_h - target_h) / 2.0
    elif anchor == "bottom-left":
        align_x = 0.0
        align_y = 0.0
    elif anchor == "bottom-center":
        align_x = (box_w - target_w) / 2.0
        align_y = 0.0
    elif anchor == "bottom-right":
        align_x = box_w - target_w
        align_y = 0.0

    # 3. Incorporate offsets (dx, dy)
    dx, dy = offset
    final_x = bx1 + align_x + dx
    final_y = by1 + align_y + dy

    # Matrix: a, b, c, d, e, f
    # b and c are 0.0 (no skewing)
    return (target_w, 0.0, 0.0, target_h, final_x, final_y)
