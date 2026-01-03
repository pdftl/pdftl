# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/spin.py

"""Spin PDF pages about their centres"""

import logging
import math

logger = logging.getLogger(__name__)
import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.utils.affix_content import affix_content
from pdftl.utils.page_specs import parse_specs

_SPIN_LONG_DESC = """
Spins page content about the center of the page, by an arbitrary angle.
The page media (paper size and orientation) is left unchanged.
Each spec is of the form `<page_range>:<angle>` where `<angle>` is in degrees.
A positive angle is a counterclockwise spin.

You can use compound specifications for the page range.
- `1,3,5:45` (Spin pages 1, 3, and 5)
- `[1-10]even:180` (Spin even pages between 1 and 10)

"""

_SPIN_EXAMPLES = [
    {
        "cmd": "in.pdf spin 1-3:45 6-end:-20 output out.pdf",
        "desc": "Spin pages 1-3 by 45 degrees counterclockwise, "
        "leave pages 4,5 unchanged and spin all remainind by 20 degrees clockwise:",
    },
    {
        "cmd": "in.pdf spin [1-5,7]:90 output out.pdf",
        "desc": "Spin pages 1-5 and 7 by 90 degrees.",
    },
]


@register_operation(
    "spin",
    tags=["in_place", "geometry"],
    type="single input operation",
    desc="Spin page content in a PDF",
    long_desc=_SPIN_LONG_DESC,
    usage="<input> spin <spec>... output <file> [<option...>]",
    examples=_SPIN_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def spin_pdf(pdf, specs) -> OpResult:
    """Spin pages of a PDF file"""
    total_pages = len(pdf.pages)

    for raw_spec in specs:
        # 1. Parse the spin-specific syntax: "pages:angle"
        if ":" in raw_spec:
            page_sel_str, angle_str = raw_spec.rsplit(":", 1)
        else:
            # If no colon is present, we cannot determine an angle.
            # We log a warning and skip, or we could treat it as 0?
            # Existing behavior implies it might just be skipped.
            logger.warning("Invalid spin spec '%s' (missing :angle). Skipping.", raw_spec)
            continue

        try:
            angle = float(angle_str)
        except ValueError:
            raise ValueError(f"Invalid spin angle in spec '{raw_spec}': {angle_str}")

        # 2. Use the central parser to resolve the page selection.
        # This gives us support for "1,3,5" and "[1-5]even" automatically.
        for page_spec in parse_specs([page_sel_str], total_pages):

            # 3. Generate target page numbers from the PageSpec object
            step = 1 if page_spec.start <= page_spec.end else -1
            page_numbers = list(range(page_spec.start, page_spec.end + step, step))

            # Apply qualifiers (even/odd)
            if "even" in page_spec.qualifiers:
                page_numbers = [p for p in page_numbers if p % 2 == 0]
            if "odd" in page_spec.qualifiers:
                page_numbers = [p for p in page_numbers if p % 2 != 0]

            # Apply omissions
            for om_start, om_end in page_spec.omissions:
                page_numbers = [p for p in page_numbers if not om_start <= p <= om_end]

            # 4. Apply the spin
            for i in page_numbers:
                if 0 < (i - 1) < len(pdf.pages):  # Safety check
                    pass
                apply_spin(pdf.pages[i - 1], angle)

    # FIXME: apply any zooms etc from split_specs if relevant
    # (spin doesn't usually support zoom syntax)

    return OpResult(success=True, pdf=pdf)


def apply_spin(page, angle):
    """Apply a spin to a page"""
    box = page.cropbox
    angle = float(angle)
    c = math.cos(angle * math.pi / 180)
    s = math.sin(angle * math.pi / 180)

    x0 = (box[0] + box[2]) / 2
    y0 = (box[1] + box[3]) / 2

    # [ A t   [ r0    = [A r0 + t   t    = [ r0
    #   0 1 ]   1 ]         0       1 ]        1 ]
    # gives t = r0 - A r0

    tx = x0 - c * x0 + s * y0
    ty = y0 - s * x0 - c * y0
    affix_content(page, f"{c} {s} {-s} {c} {tx} {ty} cm", "head")
