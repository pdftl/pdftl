# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/place.py

"""Apply affine transformations to content on specific pages."""

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Page

import pdftl.core.constants as c
from pdftl.commands.parsers.place_parser import PlacementOp, parse_place_args
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.utils.affix_content import affix_content
from pdftl.utils.dimensions import dim_str_to_pts, get_visible_page_dimensions
from pdftl.utils.page_specs import page_numbers_matching_page_spec

_PLACE_LONG_DESC = """
Applies geometric transformations (direct similarities) to the content of selected pages.

**Syntax:**
  `pdftl <input> place "<pages>(<op>=<val>; ...)" output <file>`

**Operations:**
  * `shift=dx, dy`
    Moves content by the specified x and y distances.
    Supports units (pt, in, cm, mm) and percentages relative to page size.
    Example: `shift=1in, 50%`

  * `scale=factor[:anchor]`
    Scales content by a multiplier (e.g., 0.5 for half size).
    Optional anchor determines the fixed point (default: center).

  * `spin=angle[:anchor]`
    Rotates content by degrees clockwise.
    Optional anchor determines the pivot point (default: center).

**Anchors:**
  Anchors define the center of scaling or rotation.
  * **Named:** `center` (default), `top-left`, `top`, `top-right`,
    `left`, `right`, `bottom-left`, `bottom`, `bottom-right`.
  * **Coordinate:** `x,y` (e.g., `0,0` for bottom-left corner).

**Examples:**
  * Shift all pages up by 1 inch:
    `all(shift=0, 1in)`

  * Shrink odd pages to 90% size, centered:
    `odd(scale=0.9)`

  * Rotate page 1 by 45 degrees around the top-left corner:
    `1(spin=45:top-left)`

  * Chain operations (shift then scale):
    `1-5(shift=10,10; scale=0.8)`
"""


@register_operation(
    "place",
    tags=["content_modification", "geometry"],
    desc="Shift, scale, and spin page content",
    usage="<input> place <spec>... output <file>",
    long_desc=_PLACE_LONG_DESC,
    args=(
        [c.INPUT_PDF, c.OPERATION_ARGS],
        {},
    ),
)
def place_content(target_pdf, place_specs) -> OpResult:
    total_pages = len(target_pdf.pages)

    commands = parse_place_args(place_specs)

    for cmd in commands:
        page_nums = page_numbers_matching_page_spec(cmd.page_spec, total_pages)

        for p_num in page_nums:
            if not (1 <= p_num <= total_pages):
                continue

            page = target_pdf.pages[p_num - 1]
            matrix = _calculate_transformation_matrix(page, cmd.operations)

            if matrix:
                matrix_str = " ".join(f"{x:.4f}" for x in matrix)
                affix_content(page, "Q", "tail")
                affix_content(page, f"q {matrix_str} cm", "head")
                update_annotations(page, matrix)

    return OpResult(success=True, pdf=target_pdf)


def _calculate_transformation_matrix(page: "Page", operations: list[PlacementOp]) -> list[float]:
    ctm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]  # Identity
    dims = get_visible_page_dimensions(page)

    if dims is None:
        return None

    x0, y0, w, h = dims
    rect = (x0, y0, x0 + w, y0 + h)

    for op in operations:
        op_matrix = None

        if op.name == "shift":
            dx = _eval_coordinate(op.params["dx"], w)
            dy = _eval_coordinate(op.params["dy"], h)
            op_matrix = [1, 0, 0, 1, dx, dy]

        elif op.name == "scale":
            s = float(op.params["value"])
            ax, ay = _resolve_anchor(op.params, rect)

            # 1. Translate anchor to origin
            m1 = [1, 0, 0, 1, -ax, -ay]
            # 2. Scale
            m2 = [s, 0, 0, s, 0, 0]
            # 3. Translate origin back to anchor
            m3 = [1, 0, 0, 1, ax, ay]

            # Order: m1 -> m2 -> m3
            # Matrix Math: m1 * m2 * m3
            op_matrix = _multiply_matrices(m1, _multiply_matrices(m2, m3))

        elif op.name == "spin":
            angle_deg = float(op.params["value"])
            ax, ay = _resolve_anchor(op.params, rect)
            rad = math.radians(angle_deg)
            c_val = math.cos(rad)
            s_val = math.sin(rad)

            # 1. Translate anchor to origin
            m1 = [1, 0, 0, 1, -ax, -ay]
            # 2. Rotate
            m2 = [c_val, s_val, -s_val, c_val, 0, 0]
            # 3. Translate origin back to anchor
            m3 = [1, 0, 0, 1, ax, ay]

            # Order: m1 -> m2 -> m3
            op_matrix = _multiply_matrices(m1, _multiply_matrices(m2, m3))

        if op_matrix:
            # Accumulate: current_CTM * new_op
            ctm = _multiply_matrices(ctm, op_matrix)

    return ctm


def _eval_coordinate(terms: list[str], total_dim: float) -> float:
    total = 0.0
    for term in terms:
        total += dim_str_to_pts(term, total_dim)
    return total


def _resolve_anchor(params: dict, rect: tuple[float, float, float, float]) -> tuple[float, float]:
    x1, y1, x2, y2 = rect
    w = x2 - x1
    h = y2 - y1

    if params["anchor_type"] == "coord":
        x_offset = _eval_coordinate(params["anchor_x"], w)
        y_offset = _eval_coordinate(params["anchor_y"], h)
        return x1 + x_offset, y1 + y_offset

    else:
        name = params.get("anchor_name", "center").lower().replace("-", "").replace("_", "")
        mid_x = x1 + w / 2
        mid_y = y1 + h / 2

        if "left" in name:
            x = x1
        elif "right" in name:
            x = x2
        else:
            x = mid_x

        if "top" in name:
            y = y2
        elif "bottom" in name:
            y = y1
        else:
            y = mid_y

        return x, y


def _multiply_matrices(m1: list[float], m2: list[float]) -> list[float]:
    """Result = M1 * M2. Corrected affine multiplication."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return [
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,  # FIXED: + e2 (was + e1)
        e1 * b2 + f1 * d2 + f2,  # FIXED: + f2 (was + f1)
    ]


def _transform_point(x: float, y: float, m: list[float]) -> tuple[float, float]:
    """Applies affine transform matrix m to point (x,y)."""
    a, b, c, d, e, f = m
    # PDF Matrix math: [x y 1] * [a b 0; c d 0; e f 1]
    nx = a * x + c * y + e
    ny = b * x + d * y + f
    return nx, ny


def _get_aabb_from_rect(rect: list[float], matrix: list[float]) -> list[float]:
    """
    Calculates the new Axis-Aligned Bounding Box (AABB) for a transformed Rect.
    This handles the rotation limitation by growing the box to fit.
    """
    x1, y1, x2, y2 = rect

    # Get all 4 corners of the original rectangle
    corners = [
        _transform_point(x1, y1, matrix),
        _transform_point(x2, y1, matrix),
        _transform_point(x2, y2, matrix),
        _transform_point(x1, y2, matrix),
    ]

    # Find the new min/max to create the AABB
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]

    # Return [x_ll, y_ll, x_ur, y_ur]
    return [min(xs), min(ys), max(xs), max(ys)]


def _transform_quadpoints(quads: list[float], matrix: list[float]) -> list[float]:
    """
    Transforms QuadPoints directly.
    QuadPoints allow arbitrary rotation (perfect for highlights).
    """
    new_quads = []
    # Quads are sets of 8 numbers (x1,y1 ... x4,y4)
    for i in range(0, len(quads), 2):
        nx, ny = _transform_point(quads[i], quads[i + 1], matrix)
        new_quads.extend([nx, ny])
    return new_quads


def update_annotations(page, matrix: list[float]):
    if "/Annots" not in page:
        return

    def to_floats(x):
        return list(map(float, x))

    for annot in page["/Annots"]:
        # 1. Update QuadPoints FIRST (if present)
        # This preserves the "perfect" rotation data for highlights
        if "/QuadPoints" in annot:
            annot["/QuadPoints"] = _transform_quadpoints(to_floats(annot["/QuadPoints"]), matrix)

        # 2. Update Rect
        # For highlights, this becomes the bounding box of the QuadPoints.
        # For links, this expands to cover the rotated area.
        if "/Rect" in annot:
            annot["/Rect"] = _get_aabb_from_rect(to_floats(annot["/Rect"]), matrix)

        # 3. Reset Appearance
        # Force the viewer to redraw the annotation based on new coords
        if "/AP" in annot:
            del annot["/AP"]
