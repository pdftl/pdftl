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


def _resolve_stretch_size(
    box_w: float, box_h: float, req_w: float | None, req_h: float | None
) -> tuple[float, float]:
    """Resolves target size for stretch mode, defaulting to box bounds."""
    target_w = req_w if req_w is not None else box_w
    target_h = req_h if req_h is not None else box_h
    return target_w, target_h


def _resolve_aspect_ratio_size(
    img_w: float,
    img_h: float,
    box_w: float,
    box_h: float,
    req_w: float | None,
    req_h: float | None,
    mode: Literal["fit", "fill"],
) -> tuple[float, float]:
    """Resolves aspect ratio preserving sizes for fit and fill modes."""
    limit_w = req_w if req_w is not None else box_w
    limit_h = req_h if req_h is not None else box_h

    if mode == "fit":
        scale = min(limit_w / img_w, limit_h / img_h)
    else:  # mode == "fill"
        scale = max(limit_w / img_w, limit_h / img_h)

    return img_w * scale, img_h * scale


def _resolve_none_size(
    img_w: float, img_h: float, req_w: float | None, req_h: float | None
) -> tuple[float, float]:
    """Resolves target size when no scaling strategy is active."""
    if req_w is not None and req_h is not None:
        return req_w, req_h
    if req_w is not None:
        return req_w, img_h * (req_w / img_w)
    if req_h is not None:
        return img_w * (req_h / img_h), req_h
    return img_w, img_h


def _resolve_target_size(
    img_size: tuple[float, float],
    box_w: float,
    box_h: float,
    requested_size: tuple[float | None, float | None],
    scale_mode: ScaleMode,
) -> tuple[float, float]:
    """Resolves target width and height based on the image size and requested scale_mode."""
    img_w, img_h = img_size
    req_w, req_h = requested_size

    if scale_mode == "stretch":
        return _resolve_stretch_size(box_w, box_h, req_w, req_h)
    if scale_mode in ("fit", "fill"):
        return _resolve_aspect_ratio_size(img_w, img_h, box_w, box_h, req_w, req_h, scale_mode)
    return _resolve_none_size(img_w, img_h, req_w, req_h)


def _get_anchor_offsets(
    box_w: float, box_h: float, target_w: float, target_h: float, anchor: AnchorType
) -> tuple[float, float]:
    """Maps anchor string representation to visual translation offsets."""
    parts = anchor.split("-")
    if len(parts) == 1:
        v, h = "center", "center"
    else:
        v, h = parts[0], parts[1]

    # Horizontal alignment offset
    if h == "left":
        align_x = 0.0
    elif h == "center":
        align_x = (box_w - target_w) / 2.0
    else:  # right
        align_x = box_w - target_w

    # Vertical alignment offset
    if v == "top":
        align_y = box_h - target_h
    elif v == "center" or v == "mid":
        align_y = (box_h - target_h) / 2.0
    else:  # bottom
        align_y = 0.0

    return align_x, align_y


def _resolve_alignment(
    box_bounds: tuple[float, float, float, float],
    target_size: tuple[float, float],
    anchor: AnchorType,
    offset: tuple[float, float],
) -> tuple[float, float]:
    """Maps final absolute positioning by combining coordinates, alignments, and custom offsets."""
    bx1, by1, bx2, by2 = box_bounds
    box_w = bx2 - bx1
    box_h = by2 - by1
    target_w, target_h = target_size

    align_x, align_y = _get_anchor_offsets(box_w, box_h, target_w, target_h, anchor)

    dx, dy = offset
    final_x = bx1 + align_x + dx
    final_y = by1 + align_y + dy
    return final_x, final_y


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

    target_size = _resolve_target_size(img_size, box_w, box_h, requested_size, scale_mode)
    final_x, final_y = _resolve_alignment(box_bounds, target_size, anchor, offset)

    return (target_size[0], 0.0, 0.0, target_size[1], final_x, final_y)
