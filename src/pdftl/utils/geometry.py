# src/pdftl/utils/geometry.py

"""
Geometric utilities for calculating PDF transformation matrices.
Handles anchor resolution, rotation, and coordinate normalization.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Matrix

import math


def calculate_placement_matrix(
    source_page,
    dest_x: float,
    dest_y: float,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    rotate: float = 0.0,
    anchor_source: str = "center",
    anchor_target: str = "bottom-left",
):
    """
    Calculates the affine transformation matrix to place a source page onto
    a destination canvas, accounting for bounding box shifts during rotation.
    """
    from pikepdf import Matrix

    # 1. Get Source Geometry
    box = source_page.trimbox if source_page.trimbox else source_page.mediabox
    src_x, src_y = float(box[0]), float(box[1])
    src_w = float(box[2]) - src_x
    src_h = float(box[3]) - src_y

    # 2. Shift the raw bottom-left to the origin
    m_to_origin = Matrix().translated(-src_x, -src_y)

    # 3. Apply Rotation
    m_rotate = Matrix().rotated(rotate)

    # 4. Calculate visual bounding box shift
    # Track the 4 corners of the page relative to the origin
    corners = [(0, 0), (src_w, 0), (src_w, src_h), (0, src_h)]

    rad = math.radians(rotate)
    cos_r = math.cos(rad)
    sin_r = math.sin(rad)

    # Calculate rotated coordinates: x' = x*cos(θ) - y*sin(θ), y' = x*sin(θ) + y*cos(θ)
    rotated_corners = [(x * cos_r - y * sin_r, x * sin_r + y * cos_r) for x, y in corners]

    # Find the bounds of the newly rotated shape
    min_x = min(x for x, y in rotated_corners)
    min_y = min(y for x, y in rotated_corners)
    max_x = max(x for x, y in rotated_corners)
    max_y = max(y for x, y in rotated_corners)

    vis_w = max_x - min_x
    vis_h = max_y - min_y

    # Shift so the new VISUAL bottom-left sits exactly at (0, 0)
    m_align = Matrix().translated(-min_x, -min_y)

    # 5. Resolve anchors based on the VISUAL dimensions
    handle_x, handle_y = resolve_anchor(anchor_source, 0, 0, vis_w, vis_h)

    # Move the chosen visual anchor to (0,0)
    m_anchor = Matrix().translated(-handle_x, -handle_y)

    # 6. Apply Scaling (scaling must happen around the anchor point)
    m_scale = Matrix().scaled(scale_x, scale_y)

    # 7. Move to destination coordinates
    m_to_dest = Matrix().translated(dest_x, dest_y)

    return m_to_origin @ m_rotate @ m_align @ m_anchor @ m_scale @ m_to_dest


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


def resolve_anchor(anchor: str, x: float, y: float, w: float, h: float) -> tuple[float, float]:
    """Parses 'center', 'top-left' etc into absolute coordinates."""
    anchor = anchor.lower().strip()

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
        if anchor in ["top", "bottom", "center"]:
            v_pos = anchor
        elif anchor in ["left", "right", "center"]:
            h_pos = anchor

    return _text_positions_to_coords(h_pos, v_pos, x, y, w, h)


def _text_positions_to_coords(h_pos, v_pos, x, y, w, h):
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


def get_visual_mapping_matrices(x0: float, y0: float, w: float, h: float, rotation: int):
    """
    Returns two matrices: (m_u_to_v, m_v_to_u).
    These map coordinates between the unrotated PDF space and the visually rotated space.
    """
    from pikepdf import Matrix

    m_to_orig = Matrix().translated(-x0, -y0)
    m_from_orig = Matrix().translated(x0, y0)

    if rotation == 90:
        m_u_to_v = m_to_orig @ Matrix().rotated(-90) @ Matrix().translated(0, w) @ m_from_orig
        m_v_to_u = m_to_orig @ Matrix().translated(0, -w) @ Matrix().rotated(90) @ m_from_orig
    elif rotation == 180:
        m_u_to_v = m_to_orig @ Matrix().rotated(-180) @ Matrix().translated(w, h) @ m_from_orig
        m_v_to_u = m_to_orig @ Matrix().translated(-w, -h) @ Matrix().rotated(180) @ m_from_orig
    elif rotation == 270:
        m_u_to_v = m_to_orig @ Matrix().rotated(-270) @ Matrix().translated(h, 0) @ m_from_orig
        m_v_to_u = m_to_orig @ Matrix().translated(-h, 0) @ Matrix().rotated(270) @ m_from_orig
    else:
        m_u_to_v = Matrix()
        m_v_to_u = Matrix()

    return m_u_to_v, m_v_to_u
