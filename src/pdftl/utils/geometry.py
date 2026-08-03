# src/pdftl/utils/geometry.py

"""
Geometric utilities for calculating PDF transformation matrices.
Handles anchor resolution, rotation, and coordinate normalization.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Matrix


def _as_matrix_array(m: "Matrix | tuple[float, ...] | list[float]") -> list[float]:
    """
    Normalizes a pikepdf.Matrix OR a plain 6-element (a,b,c,d,e,f) tuple/list
    (e.g. GraphicsState.ctm) into a flat 6-float list.

    This is the bridge referenced in the trim roadmap: GraphicsState.ctm is a
    plain tuple, not a pikepdf.Matrix, so callers computing image bboxes from
    it would otherwise need to build a throwaway pikepdf.Matrix first. Every
    call site that previously did `m.as_array()` should go through this
    instead, so tuple-CTM support is a one-line change per site rather than
    a parallel function.
    """
    if hasattr(m, "as_array"):
        return [float(x) for x in m.as_array()]
    return [float(x) for x in m]


# Rectangles reaching rects_overlap are typically the product of a chain
# of floating-point matrix multiplications (nested CTM concatenation,
# glyph text-render-matrix composition, corner transform + min/max).
# Two geometrically-abutting edges (e.g. adjacent glyphs with zero true
# gap) will therefore rarely be bit-identical after that arithmetic --
# they differ by a few ULPs, occasionally enough to tip a strict `<=`/
# `>=` comparison the wrong way and register as "overlapping" when
# nothing actually overlaps. This tolerance absorbs that noise without
# affecting any real overlap/redaction rect, which is always at least
# several orders of magnitude larger than this in practice (grep's
# smallest default pad alone is 1.0pt).
_OVERLAP_EPSILON = 1e-6


def rects_overlap(rect_a: list[float], rect_b: list[float]) -> bool:
    """
    Strict boolean overlap test between two axis-aligned rectangles
    [x_min, y_min, x_max, y_max]. Any nonzero overlap returns True --
    there is no containment threshold or tolerance here, unlike
    pdf_text/bboxes.py's _is_contained (which is a heuristic for
    merging probably-same-line OCR boxes, not a strict intersection
    test, and must not be reused for redaction/trim decisions where
    any overlap, however small, means the atomic unit is in scope).

    Rectangles that only touch at an edge or corner (zero-width or
    zero-height intersection) are treated as NOT overlapping, since
    a shared boundary contains no actual content to delete. A small
    fixed epsilon (_OVERLAP_EPSILON) absorbs floating-point noise from
    upstream matrix-transform chains so that two rects which are
    mathematically meant to merely touch don't spuriously register as
    overlapping due to rounding -- see module comment above.
    """
    ax1, ay1, ax2, ay2 = rect_a
    bx1, by1, bx2, by2 = rect_b

    return not (
        ax2 <= bx1 + _OVERLAP_EPSILON
        or ax1 >= bx2 - _OVERLAP_EPSILON
        or ay2 <= by1 + _OVERLAP_EPSILON
        or ay1 >= by2 - _OVERLAP_EPSILON
    )


def rect_contains(inner: list[float], outer: list[float]) -> bool:
    """
    True iff `inner` [x_min, y_min, x_max, y_max] is fully contained
    within (or exactly equal to) `outer`. Touching/flush edges count as
    contained (unlike rects_overlap's edge-touching-is-not-overlap rule,
    since containment is naturally inclusive of the boundary).

    Used by trim's match="all" mode: an atomic unit is only a "match"
    for the rect if it lies entirely within it, not merely touching it.
    """
    ix1, iy1, ix2, iy2 = inner
    ox1, oy1, ox2, oy2 = outer
    return ix1 >= ox1 and iy1 >= oy1 and ix2 <= ox2 and iy2 <= oy2


def transform_rect_bbox(rect: list[float], matrix: "Matrix | tuple[float, ...]") -> list[float]:
    """
    Applies a matrix to a rectangle [x1, y1, x2, y2] and returns the
    new Axis-Aligned Bounding Box (AABB) that encloses the result.

    `matrix` accepts either a pikepdf.Matrix or a raw 6-element
    (a, b, c, d, e, f) tuple such as GraphicsState.ctm — see
    _as_matrix_array for the normalization.
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


def _transform_point(x: float, y: float, m: "Matrix | tuple[float, ...]") -> tuple[float, float]:
    """Helper to apply a pikepdf.Matrix OR a raw 6-tuple CTM to an (x,y) pair."""
    # x' = a*x + c*y + e
    # y' = b*x + d*y + f
    m_arr = _as_matrix_array(m)
    return (m_arr[0] * x + m_arr[2] * y + m_arr[4], m_arr[1] * x + m_arr[3] * y + m_arr[5])


def resolve_anchor(anchor: str, x: float, y: float, w: float, h: float) -> tuple[float, float]:
    """Parses 'center', 'top-left' etc into absolute coordinates."""
    anchor = anchor.lower().strip()

    h_pos, v_pos = "center", "center"

    if "-" in anchor:
        parts = anchor.split("-")
        if parts[0] in ["top", "bottom", "center"]:
            v_pos = parts[0]
            h_pos = parts[1]
        elif parts[0] in ["left", "right"]:
            h_pos = parts[0]
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


def wrap_visual_matrix(page, visual_matrix: "Matrix") -> "Matrix | None":
    """Wraps a visual-space matrix so it's safe to apply to an unrotated content stream.

    Returns None if the page's visible dimensions can't be determined
    (caller decides whether that means identity or an error).
    """
    from pdftl.utils.dimensions import get_visible_page_dimensions

    rotation = int(page.get("/Rotate", 0)) % 360

    unrot_dims = get_visible_page_dimensions(page, apply_rotate=False)
    if unrot_dims is None:
        return None
    u_x0, u_y0, u_w, u_h = unrot_dims

    vis_dims = get_visible_page_dimensions(page, apply_rotate=True)
    if vis_dims is None:
        return None

    m_u_to_v, m_v_to_u = get_visual_mapping_matrices(u_x0, u_y0, u_w, u_h, rotation)
    return m_u_to_v @ visual_matrix @ m_v_to_u


def update_annotations_for_matrix(page, matrix: "Matrix") -> None:
    """Transforms click-coordinate bounding boxes of page annotations to match `matrix`."""
    if "/Annots" not in page:
        return

    for annot in page["/Annots"]:
        if "/QuadPoints" in annot:
            annot["/QuadPoints"] = transform_quadpoints(annot["/QuadPoints"], matrix)
        if "/Rect" in annot:
            annot["/Rect"] = transform_rect_bbox(annot["/Rect"], matrix)
        if "/AP" in annot:
            del annot["/AP"]
