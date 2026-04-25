# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/rebox.py

"""Crop or clip pages in a PDF file or preview the effect"""

import logging

logger = logging.getLogger(__name__)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Pdf

import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.exceptions import UserCommandLineError
from pdftl.operations.helpers.crop_fit import FitCropContext
from pdftl.operations.parsers.rebox_parser import parse_rebox_content, specs_to_page_rules
from pdftl.utils.affix_content import affix_content
from pdftl.utils.dimensions import get_visible_page_dimensions

_RECT_LONG_DESC = """

One format is `page-range(left[,top[,right[,bottom]]])`.  Here, `left`,
`top` etc. are offsets from the current page boundaries; positive numbers
move inwards towards the center of the page.  If you omit some of
these, the rest are filled in in the obvious way.  Units can be `pt`
(points), `in` (inches), `mm`, `cm` or `%` (a percentage). If omitted,
the default unit is `pt`.

For example, `1-end(10pt,20pt,10pt,20pt)` removes a
margin of 10 points from the left and right, and
20 points from the top and bottom.

Alternatively, specify `1-3(a4)` to crop pages `1-3` to size a4.

Many paper size names are allowed, see `data/paper_sizes.py`.

For landscape add the suffix `_l` to the paper size, e.g.,  `a4_l`.

You can also {verb} to the visible content using `fit`:

- `1-end(fit)` or simply '(fit)' {verb}s each page to its content.

- `1-10(fit-group)` {verb}s pages 1-10 to the union of their content.

- `1-10(fit-group=2-3)` {verb}s pages 1-10 to the union of the contents of pages 2-3.

Or use `abs` to specify an exact bounding box `x0,y0,x1,y1` where
`x0,y0` are the coordinates of the bottom left corner and `x1,y1` the
coordinates of the upper right corner:

- `1-10(abs,100,150,400,500)` {verb}s pages 1-10 to the absolutely
  positioned box with corners at (100pt,150pt) and (400pt,500pt)

You can also include a comma-separated list of up to 4 dimensions to
expand the {verb} rectangle: `(fit,1cm)` or `(fit-group, 10,0,20,50)`.

When using `abs` you can also give units or percentages, or just
numbers to default to `pt`. This uses the PDF page coordinate system,
so x-values increase to the right and y-values increase
upwards. Often, but not always, the origin (0,0) is at the bottom left
corner of the page (this depends on the page MediaBox, as shown by
`dump_data`, for example.)

If the `preview` keyword is given, a rectangle will be drawn instead
of {verbing}.

"""


_CLIP_LONG_DESC = """

Clip a page to a rectangle defined by offsets from the page edges, or
in other ways. The `clip` operation is idential to `crop` in terms of
specifying the rectangle, except that instead of cropping to the
rectangle (by changing the page boundaries), all page content is
enclosed in a clipping rectangle. The effect is then that any content
outside that rectangle is hidden, while the page boundaries are
unchanged.

To find out how to specify the rectangle, read the help for `crop`. Or
see below:

""" + _RECT_LONG_DESC.format(verb="clip", verbing="clipping")


_CROP_LONG_DESC = """

Crops pages to a rectangle defined by offsets from the edges or in
various other ways.

""" + _RECT_LONG_DESC.format(verb="crop", verbing="cropping")

_CROP_EXAMPLES = [
    {
        "cmd": "in.pdf crop '1-end(1cm,2cm)' output out.pdf",
        "desc": (
            "Remove a 1cm margin from the sides\nand 2cm from the top and bottom of all pages:"
        ),
    },
    {
        "cmd": "in.pdf crop '1-end(fit,10pt)' output clean.pdf",
        "desc": "Crop every page to its visible content plus 10pt padding.",
    },
    {
        "cmd": "in.pdf crop '2-8even(a5)' preview output out.pdf",
        "desc": (
            "Preview effect of cropping the even-numbered pages\nbetween pages 2 and 8 to A5"
        ),
    },
]
_CLIP_EXAMPLES = [
    {
        "cmd": "in.pdf clip '1-end(1cm,2cm)' output out.pdf",
        "desc": ("Clip to 1cm from the sides\nand 2cm from the top and bottom of all pages:"),
    },
    {
        "cmd": "in.pdf clip '1-end(fit,-10pt)' output clean.pdf",
        "desc": "Clip every page to its visible content minus 10pt",
    },
    {
        "cmd": "in.pdf clip '2-8even(a5)' preview output out.pdf",
        "desc": (
            "Preview effect of clipping the even-numbered pages\nbetween pages 2 and 8 to A5"
        ),
    },
]


@register_operation(
    "clip",
    tags=["in_place", "geometry"],
    type="single input operation",
    desc="Clip page content to a rectangle",
    long_desc=_CLIP_LONG_DESC,
    usage="<input> clip <specs>... [preview] output <file> [<option...>]",
    examples=_CLIP_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}, {c.OPERATION_NAME: "clip"}),
)
@register_operation(
    "crop",
    tags=["in_place", "geometry"],
    type="single input operation",
    desc="Crop pages to a rectangle",
    long_desc=_CROP_LONG_DESC,
    usage="<input> crop <specs>... [preview] output <file> [<option...>]",
    examples=_CROP_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}, {c.OPERATION_NAME: "crop"}),
)
def crop_or_clip_pages(pdf: "Pdf", specs: list, operation="crop") -> OpResult:
    """
    Crop or clip pages in a PDF using specs like '1-3(10pt,5%)'.
    """
    try:
        page_rules, preview = specs_to_page_rules(specs, len(pdf.pages), operation)
    except ValueError as exc:
        raise UserCommandLineError(exc) from exc

    # Initialize context for smart cropping (lazy loads engine if needed)
    fit_ctx = FitCropContext(pdf)

    for i in range(len(pdf.pages)):
        if i in page_rules:
            # We pass fit_ctx and all_rules to handle 'fit-group' logic
            _apply_rule_to_page(page_rules[i], i, pdf, preview, fit_ctx, page_rules, operation)

    return OpResult(success=True, pdf=pdf)


def _apply_rule_to_page(page_rule, i, pdf, preview, fit_ctx, all_rules, operation):
    if not i < len(pdf.pages):
        raise ValueError(f"With {len(pdf.pages)} pages, i={i} is too large")
    page = pdf.pages[i]

    # Verify a bounding box exists, but defer extracting dimensions to `_calculate_new_box`
    if get_visible_page_dimensions(page) is None:
        logger.warning("Warning: Skipping page %s as it has no valid MediaBox.", i + 1)
        return

    # Pass the 'page' object instead of 'page_dims' so we can handle rotation states
    new_box = _calculate_new_box(page, page_rule, i, fit_ctx, all_rules, operation)

    if new_box is None:
        logger.warning(
            "Warning: Cropping page %s gave zero or negative dimensions. Skipping.",
            i + 1,
        )
        return

    logger.debug(
        "Cropping page %s: New MediaBox [%.2f, %.2f, %.2f, %.2f]",
        i + 1,
        new_box[0],
        new_box[1],
        new_box[2],
        new_box[3],
    )

    _apply_or_preview(pdf, page, new_box, preview, operation)


def _calculate_new_box(page, spec_str, page_idx, fit_ctx, all_rules, operation):
    """
    Calculates the new mediabox from the current box dimensions and a spec string.
    Returns a tuple (x0, y0, x1, y1) or None if calculation fails.
    """
    # Fetch both coordinate spaces
    unrotated_dims = get_visible_page_dimensions(page, apply_rotate=False)
    visual_dims = get_visible_page_dimensions(page, apply_rotate=True)

    if not unrotated_dims or not visual_dims:
        return None

    ux0, uy0, u_width, u_height = unrotated_dims
    vx0, vy0, v_width, v_height = visual_dims

    # Use the master parser which handles fit/paper/margin modes based on visual dimensions
    parsed = parse_rebox_content(spec_str, v_width, v_height, operation)

    if parsed["type"] == "abs":
        logger.debug(f"values={parsed['values']}")
        return parsed["values"]

    elif parsed["type"] == "fit":
        # 'fit' mode bounding boxes are extracted natively and bypass rotation shifts
        return fit_ctx.calculate_rect(page_idx, parsed, spec_str, all_rules)

    elif parsed["type"] == "paper":
        left, top, right, bottom = _crop_margins_from_paper_size(
            v_width, v_height, *parsed["size"]
        )
    else:  # type == 'margin'
        left, top, right, bottom = parsed["values"]

    # -------------------------------------------------------------
    # MARGIN UN-ROTATION
    # -------------------------------------------------------------
    try:
        rotation = int(page.Rotate) % 360
    except (AttributeError, TypeError, ValueError):
        rotation = 0

    u_left, u_top, u_right, u_bottom = left, top, right, bottom

    if rotation == 90:
        u_left, u_top, u_right, u_bottom = top, right, bottom, left
    elif rotation == 180:
        u_left, u_top, u_right, u_bottom = right, bottom, left, top
    elif rotation == 270:
        u_left, u_top, u_right, u_bottom = bottom, left, top, right

    # Apply mapped margins to the unrotated box
    new_x0 = ux0 + u_left
    new_x1 = (ux0 + u_width) - u_right
    new_y0 = uy0 + u_bottom
    new_y1 = (uy0 + u_height) - u_top

    if new_x0 >= new_x1 or new_y0 >= new_y1:
        return None  # Invalid crop dimensions

    return new_x0, new_y0, new_x1, new_y1


def _box_width_height(box):
    return abs(box[2] - box[0]), abs(box[3] - box[1])


def _apply_or_preview(pdf, page, new_box, preview, operation):
    if preview:
        _overlay_preview_rectangle(page, new_box)
    elif operation == "crop":
        page.mediabox = new_box
        for box_key in ("/CropBox", "/TrimBox", "/BleedBox"):
            if box_key in page:
                page[box_key] = new_box
    elif operation == "clip":
        re_args = _overlay_rect_args(new_box)
        affix_content(page, f"q {re_args} re W n\n", "head")
        affix_content(page, "\nQ", "tail")
    else:
        raise ValueError(f"Internal error: invalid operation '{operation}'")


def _overlay_rect_args(box):
    new_x0, new_y0, new_x1, new_y1 = box
    crop_width, crop_height = _box_width_height(box)
    return f"{new_x0} {new_y0} {crop_width} {crop_height}"


def _overlay_preview_rectangle(page, new_box):
    import pikepdf

    page_size = _box_width_height(page.mediabox)
    with pikepdf.new() as overlay_pdf:
        overlay_pdf.add_blank_page(page_size=page_size)
        overlay_page = overlay_pdf.pages[0]

        overlay_page.mediabox = pikepdf.Array(list(page.mediabox))
        if hasattr(page, "Rotate"):
            overlay_page.Rotate = int(page.Rotate)

        re_args = _overlay_rect_args(new_box)
        stream = f"q 1 0 0 RG {re_args} re s"
        affix_content(overlay_page, stream, "tail")
        page.add_overlay(overlay_page)


def _crop_margins_from_paper_size(width, height, paper_width, paper_height):
    left = (width - paper_width) / 2
    top = (height - paper_height) / 2
    right, bottom = left, top
    return left, top, right, bottom
