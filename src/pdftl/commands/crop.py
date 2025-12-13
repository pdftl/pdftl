# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/crop.py

"""Crop pages in a PDF file or preview the effect of a crop"""

import logging

logger = logging.getLogger(__name__)
from pikepdf import Pdf

from pdftl.core.registry import register_operation
from pdftl.utils.affix_content import affix_content

from .parsers.crop_parser import (
    parse_crop_margins,
    parse_paper_spec,
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

If the `preview` keyword is given, a rectangle will be drawn instead of cropping.

"""

_CROP_EXAMPLES = [
    {
        "cmd": "in.pdf crop '1-end(1cm,2cm)' output out.pdf",
        "desc": (
            "Remove a 1cm margin from the sides\n"
            "and 2cm from the top and bottom of all pages:"
        ),
    },
    {
        "cmd": "in.pdf crop '2-8even(a5)' preview output out.pdf",
        "desc": (
            "Preview effect of cropping the even-numbered pages\n"
            "between pages 2 and 8 to A5"
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
def crop_pages(pdf: Pdf, specs: list):
    """
    Crop pages in a PDF using specs like '1-3(10pt,5%)'.
    """
    page_rules, preview = specs_to_page_rules(specs, len(pdf.pages))

    for i in range(len(pdf.pages)):
        if i in page_rules:
            _apply_crop_rule_to_page(page_rules[i], i, pdf, preview)

    return pdf


def _apply_crop_rule_to_page(page_rule, i, pdf, preview):
    assert i < len(pdf.pages)

    page = pdf.pages[i]

    if (page_box := _get_page_dimensions(page)) is None:
        logger.warning("Warning: Skipping page %s as it has no valid MediaBox.", i + 1)
        return

    new_box = _calculate_new_box(page_box, page_rule)

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


def _calculate_new_box(page_box, spec_str):
    """
    Calculates the new mediabox from the current box and a spec string.
    Returns a tuple (x0, y0, x1, y1) or None if calculation fails.
    """
    x0, y0, width, height = page_box

    paper_size = parse_paper_spec(spec_str)
    if paper_size:
        left, top, right, bottom = _crop_margins_from_paper_size(
            width, height, *paper_size
        )
    else:
        left, top, right, bottom = parse_crop_margins(spec_str, width, height)

    new_x0, new_x1 = x0 + left, (x0 + width) - right
    new_y0, new_y1 = y0 + bottom, (y0 + height) - top

    if new_x0 >= new_x1 or new_y0 >= new_y1:
        return None  # Invalid crop dimensions

    return new_x0, new_y0, new_x1, new_y1


def _apply_crop_or_preview(page, new_box, preview):
    """Applies the calculated crop box or a preview rectangle to the page."""
    if preview:
        new_x0, new_y0, new_x1, new_y1 = new_box
        width, height = new_x1 - new_x0, new_y1 - new_y0
        stream = f"q 1 0 0 RG {new_x0} {new_y0} {width} {height} re s Q"
        # The 'pdf' object isn't needed if 'inject' can be simplified
        # to just operate on the page's streams.
        affix_content(page, stream, "tail")
    else:
        # When cropping, update all relevant boxes to the new dimensions.
        page.mediabox = new_box
        for box_key in ("/CropBox", "/TrimBox", "/BleedBox"):
            if box_key in page:
                page[box_key] = new_box


def _crop_margins_from_paper_size(width, height, paper_width, paper_height):
    """Calculate cropped page corners"""
    left = (width - paper_width) / 2
    top = (height - paper_height) / 2
    right, bottom = left, top
    return left, top, right, bottom


def _get_page_dimensions(page):
    """Safely retrieves the page's MediaBox dimensions."""
    try:
        x0, y0, x1, y1 = (float(p) for p in page.mediabox)
        return x0, y0, x1 - x0, y1 - y0
    except (TypeError, IndexError, ValueError):
        return None
