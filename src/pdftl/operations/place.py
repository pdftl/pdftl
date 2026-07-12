# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/place.py

"""Apply affine transformations to content on specific pages."""

from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.core_types import HelpExample, OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import OperationError
from pdftl.operations.parsers.place_parser import parse_place_args
from pdftl.utils.affix_content import apply_content_matrix
from pdftl.utils.dimensions import dim_str_to_pts, get_visible_page_dimensions
from pdftl.utils.geometry import resolve_anchor, update_annotations_for_matrix, wrap_visual_matrix
from pdftl.utils.page_specs import page_numbers_matching_page_spec

if TYPE_CHECKING:
    import pikepdf

_PLACE_LONG_DESC = """
Applies geometric transformations (direct similarities) to the content
of selected pages.

**`<spec>` syntax:**
  `[<pages>](<operation>...)`

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

  More than one operation can be given. They should be separated by
  semicolons, '`;`'. Operations are applied in the order they appear,
  from left to right.


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
def place_content(target_pdf: "pikepdf.Pdf", place_specs) -> OpResult:
    import pikepdf

    total_pages = len(target_pdf.pages)
    commands = parse_place_args(place_specs)

    for cmd in commands:
        page_nums = page_numbers_matching_page_spec(cmd.page_spec, total_pages)

        for p_num in page_nums:
            if not 1 <= p_num <= total_pages:
                continue

            page = target_pdf.pages[p_num - 1]

            # 1. Calculate the Matrix using the unified Geometry Engine
            # We map the high-level commands (shift/scale) into the parameters
            matrix = _calculate_transformation_matrix(page, cmd.operations)

            if matrix != pikepdf.Matrix():
                # 2. Apply the matrix to the content stream
                apply_content_matrix(page, matrix)

                # 3. Update annotations using the shared helpers
                update_annotations_for_matrix(page, matrix)

    return OpResult(success=True, pdf=target_pdf)


# pikepdf Matrix explainer:
# =========================
#
# @ is ordinary matrix multiplication of matrix
#
#    M = (a,b,c,d,e,f) = [a b 0 ; c d 0 ; e f 1] = [L 0; e f 1]
#
#    where L is the linear part, L = [a b; c d]
#
#    (matlab matrix conventions: [row; row])
#
# matrices transform ROW vectors v = (x,y) = (x,y,1) by RIGHT multiplication
#
# so M.transform(v) means v M
#
# viewing v=(x,y) rather than (x,y,1)
# this means that Matrix.transform is a general affine transformation of the plane
#
# this way: M maps
#
# (0,0) to (0,0) M = (e,f)
#
# (1,0) to (1,0) M = (1,0) L + (e,f) = (a,b) + (e,f)
#
# (0,1) to (0,1) M = (0,1) L + (e,f) = (c,d) + (e,f)
#
# and, of course, composition goes from left to right.
# so M1 @ M2 maps v to v M1 M2 = (v M1) M2 = M2.transform(M1.transform(v))
#
# other notes while we're at it:
#
# M.translated(s,t) = T_{s,t} @ M,
# T_{s,t} = Matrix().translated(s,t) = [I_2 0; s t 0]
#
# M.rotated(a) = R_a @ M,
# R_a = rotation by a about (0,0) = [cos(a) sin(a);-sin(a) cos(a)] (padded out)
#
# M.scaled(sx,sy) similar (pre-multiplication)
#
# M.inverse exists, and inverts the whole affine transformation
#
# Matrix() or M.identity() both give the identity matrix
#
# it is strange that there is no automatic 'coordinate change' method
# which would take C, M and spit out inverse(C) @ M @ C


def _calculate_transformation_matrix(page, operations):
    """
    Adapts the specific 'shift/scale/spin' logic of the Place command,
    safely handling rotated pages.
    """
    from pikepdf import Matrix

    unrot_dims = get_visible_page_dimensions(page, apply_rotate=False)
    if unrot_dims is None:
        return Matrix()

    # Visual dimensions needed for percentage math and anchors
    vis_dims = get_visible_page_dimensions(page, apply_rotate=True)
    if vis_dims is None:
        raise OperationError("Could not get page dimensions")
    v_x0, v_y0, v_w, v_h = vis_dims

    visual_matrix = Matrix()

    for op in operations:
        visual_matrix = visual_matrix @ _step_matrix(op, v_x0, v_y0, v_w, v_h, Matrix)

    if visual_matrix == Matrix():
        return Matrix()

    # Wrap the visual transformations to execute safely inside the unrotated content stream
    wrapped = wrap_visual_matrix(page, visual_matrix)
    if wrapped is None:
        raise OperationError("Could not get page dimensions")
    return wrapped


def _step_matrix(op, v_x0, v_y0, v_w, v_h, pikepdf_matrix):
    step_matrix = pikepdf_matrix()

    if op.name == "shift":
        dx = _eval_dim(op.params["dx"], v_w)
        dy = _eval_dim(op.params["dy"], v_h)
        step_matrix = pikepdf_matrix().translated(dx, dy)

    elif op.name in ("scale", "spin"):
        if op.params.get("anchor_type") == "coord":
            offset_x = _eval_dim(op.params["anchor_x"], v_w)
            offset_y = _eval_dim(op.params["anchor_y"], v_h)
            ax, ay = v_x0 + offset_x, v_y0 + offset_y
        else:
            anchor_name = op.params.get("anchor_name", "center")
            ax, ay = resolve_anchor(anchor_name, v_x0, v_y0, v_w, v_h)

        m1 = pikepdf_matrix().translated(-ax, -ay)
        m3 = pikepdf_matrix().translated(ax, ay)

        if op.name == "scale":
            s = float(op.params["value"])
            m2 = pikepdf_matrix().scaled(s, s)
        else:  # spin
            deg = float(op.params["value"])
            m2 = pikepdf_matrix().rotated(deg)

        # translate anchor to origin: m1
        # then linearly transform: m2
        # then translate anchor back: m3
        # see notes above:
        # composition order here using @ is left to right
        step_matrix = m1 @ m2 @ m3

    return step_matrix


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
