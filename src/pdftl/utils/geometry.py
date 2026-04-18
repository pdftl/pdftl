# src/pdftl/utils/geometry.py

"""
Geometric utilities for calculating PDF transformation matrices.
Handles anchor resolution, rotation, and coordinate normalization.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Matrix



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
