# src/pdftl/utils/geometry.py

"""
Geometric utilities for calculating PDF transformation matrices.
Handles anchor resolution, rotation, and coordinate normalization.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Matrix, Page


def calculate_placement_matrix(
    source_page: "Page",
    dest_x: float,
    dest_y: float,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    rotate: float = 0.0,
    anchor_source: str = "center",
    anchor_target: str = "bottom-left",
) -> "Matrix":
    """
    Calculates the affine transformation matrix to place a source page onto
    a destination canvas.
    """
    from pikepdf import Matrix

    # 1. Get Source Geometry
    box = source_page.trimbox if source_page.trimbox else source_page.mediabox
    src_x, src_y = float(box[0]), float(box[1])
    src_w = float(box[2]) - src_x
    src_h = float(box[3]) - src_y

    # 2. Resolve Source Anchor
    handle_x, handle_y = _resolve_anchor(anchor_source, src_x, src_y, src_w, src_h)

    # 3. Build the Matrix Chain
    # PDF uses Row Vectors: v_new = v @ Matrix.
    # We want: v -> [Shift to Origin] -> [Rotate/Scale] -> [Shift to Dest]
    # Therefore: Matrix = M_origin @ M_transform @ M_dest

    m_to_origin = Matrix().translated(-handle_x, -handle_y)
    m_transform = Matrix().rotated(rotate).scaled(scale_x, scale_y)
    m_to_dest = Matrix().translated(dest_x, dest_y)

    return m_to_origin @ m_transform @ m_to_dest


def transform_rect_bbox(rect: list[float], matrix: "Matrix") -> list[float]:
    """
    Applies a matrix to a rectangle [x1, y1, x2, y2] and returns the
    new Axis-Aligned Bounding Box (AABB) that encloses the result.
    """
    x1, y1, x2, y2 = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])

    corners = [
        _transform_point(x1, y1, matrix),
        _transform_point(x2, y1, matrix),
        _transform_point(x2, y2, matrix),
        _transform_point(x1, y2, matrix),
    ]

    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]

    return [min(xs), min(ys), max(xs), max(ys)]


def transform_quadpoints(quads: list[float], matrix: "Matrix") -> list[float]:
    """
    Transforms a list of QuadPoints (x1, y1, x2, y2, ...).
    """
    new_quads = []
    for i in range(0, len(quads), 2):
        nx, ny = _transform_point(float(quads[i]), float(quads[i + 1]), matrix)
        new_quads.extend([nx, ny])
    return new_quads


def _transform_point(x: float, y: float, m: "Matrix") -> tuple[float, float]:
    """Helper to apply pikepdf.Matrix to a raw (x,y) pair."""
    # x' = a*x + c*y + e
    # y' = b*x + d*y + f
    m_arr = list(map(float, m.as_array()))
    return (m_arr[0] * x + m_arr[2] * y + m_arr[4], m_arr[1] * x + m_arr[3] * y + m_arr[5])


def _resolve_anchor(anchor: str, x: float, y: float, w: float, h: float) -> tuple[float, float]:
    """Parses 'center', 'top-left' etc into absolute coordinates."""
    anchor = anchor.lower().strip()

    if anchor == "center":
        return x + w / 2.0, y + h / 2.0

    h_pos, v_pos = "center", "center"

    if "-" in anchor:
        parts = anchor.split("-")
        if parts[0] in ["top", "bottom", "center"]:
            v_pos = parts[0]
            if len(parts) > 1:
                h_pos = parts[1]
        elif parts[0] in ["left", "right"]:
            h_pos = parts[0]
            if len(parts) > 1:
                v_pos = parts[1]
    else:
        if anchor in ["top", "bottom"]:
            v_pos = anchor
        elif anchor in ["left", "right"]:
            h_pos = anchor

    if h_pos == "left":
        rx = x
    elif h_pos == "right":
        rx = x + w
    else:
        rx = x + w / 2.0

    if v_pos == "bottom":
        ry = y
    elif v_pos == "top":
        ry = y + h
    else:
        ry = y + h / 2.0

    return rx, ry


def calculate_fit_metrics(
    src_w: float,
    src_h: float,
    target_w: float,
    target_h: float,
    preserve_aspect_ratio: bool = True,
) -> tuple[float, float, float, float]:
    """
    Calculates scale factors and centering offsets to fit a source rectangle
    into a target rectangle.

    Args:
        src_w, src_h: Dimensions of the content to fit.
        target_w, target_h: Dimensions of the container slot.
        preserve_aspect_ratio:
            True: Scale uniformly to fit inside (letterboxing may occur).
            False: Stretch to fill the target exactly (distortion may occur).

    Returns:
        (scale_x, scale_y, offset_x, offset_y)
        Offsets are relative to the target's bottom-left corner to achieve centering.
    """
    if src_w <= 0 or src_h <= 0:
        return 1.0, 1.0, 0.0, 0.0

    # Calculate raw scaling ratios for both axes
    ratio_w = target_w / src_w
    ratio_h = target_h / src_h

    if preserve_aspect_ratio:
        # Scale uniformly using the smaller ratio to ensure it fits entirely
        s = min(ratio_w, ratio_h)
        sx, sy = s, s
    else:
        # Scale axes independently to fill the slot exactly
        sx, sy = ratio_w, ratio_h

    # Calculate the final dimensions of the fitted content
    final_w = src_w * sx
    final_h = src_h * sy

    # Calculate centering offsets
    dx = (target_w - final_w) / 2.0
    dy = (target_h - final_h) / 2.0

    return sx, sy, dx, dy
