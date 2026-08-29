# src/pdftl/operations/add_marks.py
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/add_marks.py

# Portions derived from printer_marks.py (Spectra-PDF) by Jason Ulbright.
# Used under the MIT License (see NOTICES.md)

# MIT License
#
# Copyright (c) 2026 Jason Ulbright
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""Add printer marks (crop marks, registration targets, a colour bar) to
PDF pages, growing the page to hold them.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Pdf

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.operations.helpers.marks_render import apply_marks_to_page
from pdftl.operations.parsers.add_marks_parser import parse_add_marks_args

logger = logging.getLogger(__name__)

_LONG_DESC = """
The `add_marks` operation draws printer marks -- crop marks, registration
targets, and (optionally) a colour bar -- outside each page's trim, growing
`/MediaBox` and `/CropBox` to make room. `/TrimBox`, `/BleedBox` and
`/ArtBox` are left untouched.

## Specification format

    <pages>(<key>=<value>,...)

`<pages>` follows the same page-spec syntax as other operations (e.g.
`1-5`, `1,3,5-end`). A bare page spec with no parenthesized options uses
the defaults below. A bare `add_marks` with no specs at all applies the
defaults to every page.

### Options

* `cropmarks=<western|japanese|false>`: mark style, or `false` to omit
  crop marks entirely. `japanese` draws the double crop mark whose gap
  indicates bleed, plus edge centre marks. Default: `western`.
* `registration=<true|false>`: draw the four edge-midpoint registration
  targets. Default: `true`.
* `colorbars=<true|false>`: draw a fixed CMYK colour bar (process solids
  and 75/50/25% tints). This does not inspect the document for spot inks --
  it matches Acrobat's own "Add Printer Marks" colour bar, which is
  likewise a generic swatch strip. Default: `false`.
* `pageinfo=<true|false>`: print filename, page number, and timestamp at the
  bottom edge of the page. Default: `false`.
* `startarget=<true|false>`: draw an 18-diameter slur/doubling target in the
  top-left margin -- a press QA aid for spotting slur or doubling by eye,
  independent of the colour bar. Default: `false`.
* `weight=<0.125|0.25|0.5>`: stroke weight in points. Default: `0.25`.
* `offset=<dim>`: gap between the trim edge and the start of a mark, e.g.
  `9pt`, `0.3cm`. Default: `9pt`.
* `length=<dim>`: how far a mark runs outward from `offset`. The page
  grows by `offset + length` on every edge. Default: `18pt`.

A bare `remove` token (in place of key=value options) strips any marks
previously added and restores the page's original boxes, drawing nothing.
It cannot be combined with any other option -- `4(remove,colorbars=true)`
is an error, since removing only some previously-drawn marks while
keeping others isn't supported.

Running `add_marks` again on an already-marked page removes the previous
marks and box growth first, so repeated runs never compound.
"""

_EXAMPLES = [
    {
        "cmd": "in.pdf add_marks output out.pdf",
        "desc": "Add default (western crop marks + registration) to all pages",
    },
    {
        "cmd": "in.pdf add_marks '2-4(cropmarks=japanese,colorbars=true)' output out.pdf",
        "desc": "Japanese double crop marks and a colour bar on pages 2-4",
    },
    {
        "cmd": "in.pdf add_marks 2-4 '8-end(offset=1cm,length=2cm)' output out.pdf",
        "desc": "Default marks on pages 2-4, larger marks from page 8 on",
    },
    {
        "cmd": "in.pdf add_marks '4(remove)' output out.pdf",
        "desc": "Remove previously added marks from page 4, leaving other pages untouched",
    },
]


@register_operation(
    "add_marks",
    tags=["in_place", "geometry", "print_production"],
    type="single input operation",
    desc="Add crop marks, registration targets and a colour bar",
    long_desc=_LONG_DESC,
    usage="<input> add_marks <spec>... output <output>",
    examples=_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def add_marks_to_pdf(pdf: Pdf, args: list) -> OpResult:
    """Entry point registered with the pdftl operation registry."""
    page_configs = parse_add_marks_args(args or [], len(pdf.pages))

    filename = getattr(pdf, "filename", "")
    if filename:
        import os

        filename = os.path.basename(filename)
    else:
        filename = "document.pdf"
    total_pages = len(pdf.pages)

    marked = 0
    for page_num, config in page_configs.items():
        page = pdf.pages[page_num - 1]
        report = apply_marks_to_page(pdf, page, config, filename, page_num, total_pages)
        if report.get("marked"):
            marked += 1
        elif report.get("skipped_reason"):
            logger.warning("add_marks: skipping page %s (%s).", page_num, report["skipped_reason"])

    logger.info("add_marks: marked %d of %d requested page(s).", marked, len(page_configs))
    return OpResult(success=True, pdf=pdf)
