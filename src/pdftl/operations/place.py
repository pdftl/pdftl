# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/place.py

"""Apply affine transformations to content on specific pages."""

from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.types import HelpExample, OpResult
from pdftl.operations.parsers.place_parser import parse_place_args
from pdftl.utils.affix_content import affix_content
from pdftl.utils.dimensions import dim_str_to_pts, get_visible_page_dimensions
from pdftl.utils.geometry import (
    transform_quadpoints,
    transform_rect_bbox,
)
from pdftl.utils.page_specs import page_numbers_matching_page_spec

if TYPE_CHECKING:
    pass

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
"""

_PLACE_EXAMPLES = [
    HelpExample(
        desc="Shift all pages up by 1 inch", cmd="in.pdf place '(shift=0, 1in)' output out.pdf"
    ),
    HelpExample(
        desc="Shrink odd pages to 90% size, centered",
        cmd="in.pdf place 'odd(scale=0.9)' output out.pdf",
    ),
    HelpExample(
        desc="Rotate page 1 by 45 degrees around the top-left corner",
        cmd="in.pdf place '1(spin=45:top-left)' output out.pdf",
    ),
    HelpExample(
        desc="Chain operations (shift then scale)",
        cmd="in.pdf place '1-5(shift=10,10; scale=0.8)' output out.pdf",
    ),
]


@register_operation(
    "place",
    tags=["content_modification", "geometry"],
    desc="Shift, scale, and spin page content",
    usage="<input> place <spec>... output <file>",
    examples=_PLACE_EXAMPLES,
    long_desc=_PLACE_LONG_DESC,
    args=(
        [c.INPUT_PDF, c.OPERATION_ARGS],
        {},
    ),
)
def place_content(target_pdf, place_specs) -> OpResult:
    import pikepdf

    total_pages = len(target_pdf.pages)
    commands = parse_place_args(place_specs)

    for cmd in commands:
        page_nums = page_numbers_matching_page_spec(cmd.page_spec, total_pages)

        for p_num in page_nums:
            if not (1 <= p_num <= total_pages):
                continue

            page = target_pdf.pages[p_num - 1]

            # 1. Calculate the Matrix using the unified Geometry Engine
            # We map the high-level commands (shift/scale) into the parameters
            # expected by calculate_placement_matrix
            matrix = _calculate_transformation_matrix(page, cmd.operations)

            if matrix != pikepdf.Matrix():
                # 2. Apply the matrix to the content stream
                matrix_str = matrix.encode().decode("utf-8")
                affix_content(page, "Q", "tail")
                affix_content(page, f"q {matrix_str} cm ", "head")

                # 3. Update annotations using the shared helpers
                _update_annotations(page, matrix)

    return OpResult(success=True, pdf=target_pdf)


def _calculate_transformation_matrix(page, operations):
    """
    Adapts the specific 'shift/scale/spin' logic of the Place command.
    """
    from pikepdf import Matrix

    # We need these imports available locally or at module level
    from pdftl.utils.geometry import _resolve_anchor

    # Start with Identity
    current_matrix = Matrix()

    # Dimensions for relative math (50%)
    dims = get_visible_page_dimensions(page)
    if dims is None:
        return Matrix()
    x0, y0, w, h = dims

    for op in operations:
        step_matrix = Matrix()

        if op.name == "shift":
            dx = _eval_dim(op.params["dx"], w)
            dy = _eval_dim(op.params["dy"], h)
            step_matrix = Matrix().translated(dx, dy)

        elif op.name == "scale" or op.name == "spin":
            # --- RESTORED ANCHOR LOGIC ---
            # 1. Determine the anchor point (ax, ay) in absolute page coordinates

            if op.params.get("anchor_type") == "coord":
                # Case A: Explicit coordinates (e.g. "50% 10pt")
                # We calculate offset from the page origin (x0, y0)
                offset_x = _eval_dim(op.params["anchor_x"], w)
                offset_y = _eval_dim(op.params["anchor_y"], h)
                ax, ay = x0 + offset_x, y0 + offset_y
            else:
                # Case B: Named anchor (e.g. "center", "top-left")
                # Delegate to the geometry helper
                anchor_name = op.params.get("anchor_name", "center")
                ax, ay = _resolve_anchor(anchor_name, x0, y0, w, h)

            # 2. Build the Matrix
            # Move Anchor->Origin, Transform, Move Back
            m1 = Matrix().translated(-ax, -ay)
            m3 = Matrix().translated(ax, ay)

            if op.name == "scale":
                s = float(op.params["value"])
                m2 = Matrix().scaled(s, s)
            else:  # spin
                deg = float(op.params["value"])
                m2 = Matrix().rotated(deg)

            step_matrix = m1 @ m2 @ m3

        # Accumulate
        current_matrix = current_matrix @ step_matrix

    return current_matrix


def _update_annotations(page, matrix):
    """Updates clickable areas to match the new visual location."""
    if "/Annots" not in page:
        return

    for annot in page["/Annots"]:
        # Use shared geometry helpers for the heavy lifting
        if "/QuadPoints" in annot:
            # transform_quadpoints is a new export from geometry.py
            annot["/QuadPoints"] = transform_quadpoints(annot["/QuadPoints"], matrix)

        if "/Rect" in annot:
            # transform_rect_bbox is a new export from geometry.py
            annot["/Rect"] = transform_rect_bbox(annot["/Rect"], matrix)

        if "/AP" in annot:
            del annot["/AP"]


def _eval_dim(terms, reference_size: float) -> float:
    """
    Converts dimension strings (e.g. '1in' or ['1in', '5pt'])
    into a float value in points.
    """
    if isinstance(terms, str):
        terms = [terms]  # Normalize single string to list

    total = 0.0
    for term in terms:
        total += dim_str_to_pts(term, reference_size)
    return total
