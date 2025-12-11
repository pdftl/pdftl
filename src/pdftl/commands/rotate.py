# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/rotate.py

"""Rotate PDF pages by multiples of 90 degrees"""

from pikepdf import Pdf

from pdftl.core.registry import register_operation
from pdftl.utils.transform import transform_pdf

_ROTATE_LONG_DESC = """

Rotates pages by 90, 180, or 270 degrees. Each spec consists of a
page range followed by a rotation direction: north (0), east (90),
south (180), west (270), left (-90), right (+90), or down (+180).
For example, '1-endeast' rotates all pages 90 degrees clockwise.
'2-3left 4south' rotates pages 2-3 left and page 4 upside-down.

"""

_ROTATE_EXAMPLES = [
    {
        "cmd": "in.pdf rotate 1-endright output out.pdf",
        "desc": "Rotate all pages 90 degrees clockwise:",
    }
]


@register_operation(
    "rotate",
    tags=["in_place", "geometry"],
    type="single input operation",
    desc="Rotate pages in a PDF",
    long_desc=_ROTATE_LONG_DESC,
    usage="<input> rotate <spec>... output <file> [<option...>]",
    examples=_ROTATE_EXAMPLES,
    args=(["input_pdf", "operation_args"], {}),
)
def rotate_pdf(source_pdf: Pdf, specs: list):
    """
    Applies rotations and/or scaling to specified pages of a PDF.
    """
    return transform_pdf(source_pdf, specs)
