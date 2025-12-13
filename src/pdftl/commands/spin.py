# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/spin.py

"""Spin PDF pages about their centres"""

import logging
import math

logger = logging.getLogger(__name__)
from pdftl.core.registry import register_operation
from pdftl.utils.affix_content import affix_content
from pdftl.utils.page_specs import page_numbers_matching_page_spec

_SPIN_LONG_DESC = """
Spins page content about the center of the page, by an arbitrary angle.
The page media (paper size and orientation) is left unchanged.
Each spec is of the form `<page_range>:<angle>` where `<angle>` is in degrees.
A positive angle is a counterclockwise spin.

"""

_SPIN_EXAMPLES = [
    {
        "cmd": "in.pdf spin 1-3:45 6-end:-20 output out.pdf",
        "desc": "Spin pages 1-3 by 45 degrees counterclockwise, "
        "leave pages 4,5 unchanged and spin all remainind by 20 degrees clockwise:",
    }
]


@register_operation(
    "spin",
    tags=["in_place", "geometry"],
    type="single input operation",
    desc="Spin page content in a PDF",
    long_desc=_SPIN_LONG_DESC,
    usage="<input> spin <spec>... output <file> [<option...>]",
    examples=_SPIN_EXAMPLES,
    args=(["input_pdf", "operation_args"], {}),
)
def spin_pdf(pdf, specs):
    """Spin pages of a PDF file"""
    total_pages = len(pdf.pages)
    split_specs = []
    for colon_splits in map(lambda x: x.split(":", 1), specs):
        spec_str = colon_splits[0]
        split_specs += spec_str
        angle = None
        if len(colon_splits) > 1:
            angle = colon_splits[1]
        logger.debug("spec_str=%s, angle=%s", spec_str, angle)
        if angle:
            for i in page_numbers_matching_page_spec(spec_str, total_pages):
                apply_spin(pdf.pages[i - 1], angle)

    # FIXME: apply any zooms etc from split_specs

    return pdf


def apply_spin(page, angle):
    """Apply a spin to a page"""
    box = page.cropbox
    angle = float(angle)
    c = math.cos(angle * math.pi / 180)
    s = math.sin(angle * math.pi / 180)

    x0 = (box[0] + box[2]) / 2
    y0 = (box[1] + box[3]) / 2

    # [ A t   [ r0   = [A r0 + t   t   = [ r0
    #   0 1 ]   1 ]        0      1 ]       1 ]
    # gives t = r0 - A r0

    tx = x0 - c * x0 + s * y0
    ty = y0 - s * x0 - c * y0
    affix_content(page, f"{c} {s} {-s} {c} {tx} {ty} cm", "head")
