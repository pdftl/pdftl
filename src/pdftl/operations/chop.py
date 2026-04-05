# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/chop.py

"""Chop pages of a PDF into smaller pieces either
horizontally (into rows) or vertically (into columns).

The chop operation allows you to split pages of a PDF into
smaller parts. The resulting pages can be manipulated
individually, depending on the specified chopping rules. The
operation supports multiple specification formats, including
page ranges, piece sizes, and more.

For further details on the syntax, see the 'Specification
syntax' section below.

"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Pdf

import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.operations.parsers.chop_parser import parse_chop_spec, parse_chop_specs_to_rules

_CHOP_LONG_DESC = """

Chops specified pages into multiple smaller pieces by splitting them
either horizontally or vertically. The output PDF contains the
resulting chopped pieces in order. Pages from the input file that are
not matched by any spec are copied to the output unmodified.  The size
of each piece may be specified using `<spec>...`; see examples.
Depending on your shell, you may need to quote a `<spec>` which uses
parentheses.

The chop operation is controlled by one or more specification strings.
A specification can apply to all pages, or be targeted to specific
pages using pageranges or even/odd keywords.

Specification syntax:

    `[<page_spec>][cols|rows][<sizes_or_pieces>]`

Examples:

   `1-10cols2`

     Apply a vertical 2-piece chop to pages 1 through 10.

   `evencols(1:2)`

     Apply a vertical 1-to-2 ratio chop to all even pages.

   `odd 4-endcols3`

     Apply a vertical 3-piece chop to odd pages from 4 to the end.

   `rows`

     Chop horizontally (creating rows).

### Specification details


`<page_spec>` is a page specification, consisting of an
optional page range (like `1-4`) followed immediately by an
optional `even` or `odd`.  If no page range is given, all
pages are assumed. See also the help topic [[`page_spec`]].


`<sizes_or_pieces>` defines the dimensions of the chopped
pieces.  This can be specified in several ways.

    If omitted, the default is 2 equal pieces.

    `<integer>`
      Chop into N equal-sized pieces.

      Example: `cols3` (Chop into 3 equal vertical columns).

    `<list>`
      comma-separated list of custom sizes. Parentheses are optional.

      Example: `rows(100, fill, 100)` or `rows100,fill,100`

   `<ratios>`
      A colon-separated list of ratios.

      Example: `cols(1:2)` (A vertical chop with the second column being
      twice as wide as the first).

Size Units (for use in `<list>` format):

    `pt` (default)

      Size in points. 1 inch = 72 points. `100` is the same as `100pt`.

    `%`

      Percentage of the total page width (for `cols`) or height (for `rows`).

    `fill`

      A keyword that expands to fill the remaining space. If used
      multiple times, the remaining space is split evenly between each
      fill .

    `d`

      Appending `d` to any size in a custom list will cause that piece
      to be discarded from the output. This is useful for trimming
      margins.

"""

_CHOP_EXAMPLES = [
    {
        "cmd": "in.pdf chop rows output out.pdf",
        "desc": "Chop all pages horizontally in half",
    },
    {
        "cmd": "in.pdf chop 1-3rows output out.pdf",
        "desc": "Chop pages 1-3 horizontally in half",
    },
    {
        "cmd": "in.pdf chop 1-3rows(3) output out.pdf",
        "desc": "Chop pages 1-3 horizontally in 3 pieces",
    },
    {
        "cmd": "in.pdf chop cols(5%,fill,5%) output out.pdf",
        "desc": "Trim 5% from the left and right margins of every page",
    },
    {
        "cmd": "in.pdf chop 2-4cols(25%,fill) output out.pdf",
        "desc": "Chop pages 2-4 vertically in the ratio 1:3",
    },
    {
        "cmd": "in.pdf chop cols(1:2) output out.pdf",
        "desc": "Split pages into two columns in the ratio 1:2",
    },
]


@register_operation(
    "chop",
    tags=["in_place", "geometry"],
    type="single input operation",
    desc="Chop pages into multiple smaller pieces",
    long_desc=_CHOP_LONG_DESC,
    usage="<input> chop <spec>... output <file> [<option>...]",
    examples=_CHOP_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def chop_pages(source_pdf: "Pdf", specs: list) -> OpResult:
    """
    Chops specified pages of a PDF into multiple smaller pages.

    BUG FIXME: currently does strange things with page rotation (pages out of order?)
    """
    # 1. Parse all specifications into a clear plan of action (page_rules)
    if not specs:
        specs = ["cols"]  # Default spec if none are provided

    total_pages = len(source_pdf.pages)
    page_rules = parse_chop_specs_to_rules(specs, total_pages)

    # 2. Apply the rules to generate the final sequence of pages
    final_pages = []
    # Iterate over a static copy of the original pages list, as the underlying
    # pdf.pages will be modified during the chopping process.
    original_pages = list(source_pdf.pages)

    for i, source_page in enumerate(original_pages):
        chop_spec_to_use = page_rules.get(i)

        if not chop_spec_to_use:
            final_pages.append(source_page)
        else:
            chopped_pages = _apply_chop_to_page(source_pdf, source_page, chop_spec_to_use)
            final_pages.extend(chopped_pages)

    # 3. Completely replace the old pages with the new list in the correct order.
    del source_pdf.pages[:]
    source_pdf.pages.extend(final_pages)

    return OpResult(success=True, pdf=source_pdf)


##################################################


def _apply_chop_to_page(pdf: "Pdf", source_page, chop_spec_to_use):
    """Chops a single source page into multiple smaller pages based on the specified chop rule.

    The function uses the provided chopping specification (`chop_spec_to_use`) to determine
    how the page should be divided. Each resulting smaller page is appended to the output
    PDF.
    """
    import pikepdf

    # 1. Extract absolute raw physical bounds and rotation
    raw_x0, raw_y0, raw_x1, raw_y1 = [float(x) for x in source_page.mediabox]
    raw_w = raw_x1 - raw_x0
    raw_h = raw_y1 - raw_y0
    rotation = int(source_page.get("/Rotate", 0)) % 360

    # 2. Determine visual dimensions (what the user sees on screen)
    if rotation in (90, 270):
        visual_w, visual_h = raw_h, raw_w
    else:
        visual_w, visual_h = raw_w, raw_h

    # 3. Get visual chopped rectangles (rooted at 0,0) from the parser
    visual_mediabox = [0.0, 0.0, visual_w, visual_h]
    visual_rects = parse_chop_spec(chop_spec_to_use, visual_mediabox)

    def make_new_chopped_page(rect):
        pdf.pages.append(source_page)
        new_page = pdf.pages[-1]

        # Extract visual coordinates (vx, vy)
        vx0, vy0, vx1, vy1 = [float(val) for val in rect]

        # 4. Map the visual rectangle back to the physical canvas space
        if rotation == 0:
            px0, px1 = vx0, vx1
            py0, py1 = vy0, vy1
        elif rotation == 90:  # 90 degrees clockwise
            px0, px1 = raw_w - vy1, raw_w - vy0
            py0, py1 = vx0, vx1
        elif rotation == 180:  # 180 degrees (upside down)
            px0, px1 = raw_w - vx1, raw_w - vx0
            py0, py1 = raw_h - vy1, raw_h - vy0
        elif rotation == 270:  # 270 degrees clockwise (90 CCW)
            px0, px1 = vy0, vy1
            py0, py1 = raw_h - vx1, raw_h - vx0

        # Shift by the physical origin (in case original page wasn't rooted at 0,0)
        new_page.mediabox = pikepdf.Array([px0 + raw_x0, py0 + raw_y0, px1 + raw_x0, py1 + raw_y0])

        # 5. Clean up other bounding boxes so they don't hide the new MediaBox
        for box in ("/CropBox", "/TrimBox", "/BleedBox", "/ArtBox"):
            if box in new_page:
                del new_page[box]

        # Note: We DO NOT delete new_page.Rotate and DO NOT inject a content transform!
        # The physical MediaBox and the native rotation flag handle everything gracefully.

        return new_page

    return [make_new_chopped_page(rect) for rect in visual_rects]
