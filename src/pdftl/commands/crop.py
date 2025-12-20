# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/crop.py

"""Crop pages in a PDF file or preview the effect of a crop"""

import logging

logger = logging.getLogger(__name__)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Pdf

from pdftl.commands.helpers.crop_fit import FitCropContext
from pdftl.core.registry import register_operation
from pdftl.utils.affix_content import affix_content

from .parsers.crop_parser import (
    parse_crop_content,
    specs_to_page_rules,
)

_CROP_LONG_DESC = """

Crops pages to a rectangle defined by offsets from the edges.

The format is `page-range(left[,top[,right[,bottom]]])`.
If you omit some of these, the rest are filled in in the obvious way.
Units can be `pt` (points), `in` (inches),
`mm`, `cm` or `%` (a percentage). If omitted, the default unit is `pt`.

For example, `1-end(10pt,20pt,10pt,20pt)` removes a
margin of 10 points from the left and right, and
20 points from the top and bottom.

Alternatively, specify `1-3(a4)` to crop pages `1-3` to size a4.

Many paper size names are allowed, see `data/paper_sizes.py`.

For landscape add the suffix `_l` to the paper size, e.g.,  `a4_l`.

You can also crop to the visible content using `fit`:

- `1-end(fit)` or simply '(fit)' crops each page to its content.

- `1-10(fit-group)` crops pages 1-10 to the union of their content.

- `1-10(fit-group=2-3)` crops pages 1-10 to the union of the contents of pages 2-3.

You can also include a comma-separated list of up to 4 dimensions
  to expand the crop rectangle: `(fit,1cm)` or `(fit-group, 10,0,20,50)`.

If the `preview` keyword is given, a rectangle will be drawn instead of cropping.

"""

_CROP_EXAMPLES = [
    {
        "cmd": "in.pdf crop '1-end(1cm,2cm)' output out.pdf",
        "desc": (
            "Remove a 1cm margin from the sides\n" "and 2cm from the top and bottom of all pages:"
        ),
    },
    {
        "cmd": "in.pdf crop '1-end(fit,10pt)' output clean.pdf",
        "desc": "Crop every page to its visible content plus 10pt padding.",
    },
    {
        "cmd": "in.pdf crop '2-8even(a5)' preview output out.pdf",
        "desc": (
            "Preview effect of cropping the even-numbered pages\n" "between pages 2 and 8 to A5"
        ),
    },
]


@register_operation(
    "crop",
    tags=["in_place", "geometry"],
    type="single input operation",
    desc="Crop pages",
    long_desc=_CROP_LONG_DESC,
    usage="<input> crop <specs>... [preview] output <file> [<option...>]",
    examples=_CROP_EXAMPLES,
    args=(["input_pdf", "operation_args"], {}),
)
def crop_pages(pdf: "Pdf", specs: list):
    """
    Crop pages in a PDF using specs like '1-3(10pt,5%)'.
    """
    page_rules, preview = specs_to_page_rules(specs, len(pdf.pages))

    # Initialize context for smart cropping (lazy loads engine if needed)
    fit_ctx = FitCropContext(pdf)

    for i in range(len(pdf.pages)):
        if i in page_rules:
            # We pass fit_ctx and all_rules to handle 'fit-group' logic
            _apply_crop_rule_to_page(page_rules[i], i, pdf, preview, fit_ctx, page_rules)

    return pdf


def _apply_crop_rule_to_page(page_rule, i, pdf, preview, fit_ctx, all_rules):
    assert i < len(pdf.pages)

    page = pdf.pages[i]

    if (page_dims := _get_page_dimensions(page)) is None:
        logger.warning("Warning: Skipping page %s as it has no valid MediaBox.", i + 1)
        return

    new_box = _calculate_new_box(page_dims, page_rule, i, fit_ctx, all_rules)

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

    _apply_crop_or_preview(page, new_box, preview)


def _calculate_new_box(page_dims, spec_str, page_idx, fit_ctx, all_rules):
    """
    Calculates the new mediabox from the current box dimensions and a spec string.
    Returns a tuple (x0, y0, x1, y1) or None if calculation fails.
    """
    x0, y0, width, height = page_dims

    # Use the master parser which handles fit/paper/margin modes
    parsed = parse_crop_content(spec_str, width, height)

    if parsed["type"] == "fit":
        # 'fit' mode calculates absolute coordinates directly
        return fit_ctx.calculate_rect(page_idx, parsed, spec_str, all_rules)

    elif parsed["type"] == "paper":
        left, top, right, bottom = _crop_margins_from_paper_size(width, height, *parsed["size"])
    else:  # type == 'margin'
        left, top, right, bottom = parsed["values"]

    # Apply relative margins to the current box
    new_x0, new_x1 = x0 + left, (x0 + width) - right
    new_y0, new_y1 = y0 + bottom, (y0 + height) - top

    if new_x0 >= new_x1 or new_y0 >= new_y1:
        return None  # Invalid crop dimensions

    return new_x0, new_y0, new_x1, new_y1


def _box_width_height(box):
    return abs(box[2] - box[0]), abs(box[3] - box[1])


def _apply_crop_or_preview(page, new_box, preview):
    """Applies the calculated crop box or a preview rectangle to the page."""
    if preview:
        _overlay_preview_rectangle(page, new_box)
    else:
        # When cropping, update all relevant boxes to the new dimensions.
        page.mediabox = new_box
        for box_key in ("/CropBox", "/TrimBox", "/BleedBox"):
            if box_key in page:
                page[box_key] = new_box


def _overlay_preview_rectangle(page, new_box):
    import pikepdf

    new_x0, new_y0, new_x1, new_y1 = new_box
    crop_width, crop_height = _box_width_height(new_box)
    page_size = _box_width_height(page.mediabox)
    with pikepdf.new() as overlay_pdf:
        overlay_pdf.add_blank_page(page_size=page_size)
        overlay_page = overlay_pdf.pages[0]

        # overlay geometry should mirror source
        # use list to avoid copy_foreign shenanigans
        overlay_page.mediabox = list(page.mediabox)
        if hasattr(page, "Rotate"):
            overlay_page.Rotate = int(page.Rotate)

        stream = f"q 1 0 0 RG {new_x0} {new_y0} {crop_width} {crop_height} re s"
        affix_content(overlay_page, stream, "tail")
        page.add_overlay(overlay_page)


def _crop_margins_from_paper_size(width, height, paper_width, paper_height):
    """Calculate cropped page corners"""
    left = (width - paper_width) / 2
    top = (height - paper_height) / 2
    right, bottom = left, top
    return left, top, right, bottom


def _get_page_dimensions(page):
    """Safely retrieves the page's MediaBox dimensions.
    Returns: origin_x, origin_y, signed_width, signed_height, or None on error.
    """
    try:
        x0, y0, x1, y1 = (float(p) for p in page.mediabox)
        return x0, y0, x1 - x0, y1 - y0
    except (TypeError, IndexError, ValueError):
        return None
