# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/rotate.py

"""Rotate PDF pages by multiples of 90 degrees"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Pdf

from pdftl.core.registry import register_operation
from pdftl.utils.transform import transform_pdf

_ROTATE_LONG_DESC = """

Rotates pages by 90, 180, or 270 degrees. Each '<spec>' consists of a
page range followed by a rotation direction. A rotation direction is
either a cardinal direction or a relative direction.

The cardinal directions `north`, `east`, `south`, `west` are absolute
rotations, relative to the page's "natural" orientation which is
`north`. (You get to find out what this natural orientation is by
setting this to `north` and inspecting the file. Often it is `north`
already but not always.)

The relative directions `left`, `right`, `down` are relative to the
page's current rotation, viewed from the topside of the page. For
example, `down` will turn pages upside-down. And `right` rotates 90
degrees clockwise.

For example, '1-endeast' orients all pages 90 degrees clockwise
compared to their natural rotation.

And '2-3left 4south' rotates pages 2-3 leftwards and makes page 4
Australian.

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
def rotate_pdf(source_pdf: "Pdf", specs: list):
    """
    Applies rotations and/or scaling to specified pages of a PDF.
    """
    return transform_pdf(source_pdf, specs)
