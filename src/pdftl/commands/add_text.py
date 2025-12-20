# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/add_text.py

"""Add user-specified text strings to PDF pages

This operation uses a helper class to create text overlays,
which are then applied to the target pages.
"""

import io
import logging
from datetime import datetime

logger = logging.getLogger(__name__)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Pdf

from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError

_ADD_TEXT_LONG_DESC = r"""
Add user-specified text strings to PDF pages.

Note: This operation requires the 'reportlab' library. If not
installed, run: `pip install pdftl[add_text]`.


A text specification (`<spec>`) has the format:

>  `[page range]<delimiter><text string><delimiter>[<options>]`

`<delimiter>` must be a single, non-alphanumeric character
(e.g., /, !, #).

### Dynamic Text Variables in text strings

The `<text string>` supports variable substitution using curly
braces {}, in one of the following formats.

1. Simple format: e.g., `{page}` gives the current page
number. Possible variables are:

   - `page`: The current page number (1-based index).

   - `total`: The total number of pages in the PDF.

   - `filename`: The name of the input PDF file, including the extension
  (e.g., "document.pdf").

   - `filename_base`: The base name of the input PDF file,
  without the extension (e.g., "document").

   - `filepath`: The full path to the input PDF file.

   - `date`: The current date formatted as YYYY-MM-DD (e.g.,
  2025-12-12).

   - `time`: The current time formatted as HH:MM:SS (e.g.,
  13:53:41).

   - `datetime`: The current date and time in ISO 8601 format
  (e.g., 2025-12-12T13:53:41.123456).


2. Basic arithmetic: e.g., `{page+1}` (supported on `page` and
`total`).

3. Complex: e.g., {total-page} gives the number of pages
remaining.  (for now, this is the only complex possibility).

4. Metadata: e.g., {meta:Title}. The metadata variables
`allow` you to insert information stored within the PDF
document's own metadata dictionary (\`/Title\`, \`/Author\`,
etc.) into your text.

The format for a metadata variable is: {meta:`<KeyName>`}
where `<KeyName>` is the exact, case-sensitive key found in
the PDF's document information dictionary (it corresponds to
the PDF keys like \`/Title\` after the leading slash is
stripped).

The available keys are determined by the contents of the PDF
itself, but common examples derived from the PDF
specification include: Title, Author, Subject, Keywords,
Creator, Producer, CreateionDate. If the specified `<KeyName>`
does not exist in the PDF's metadata, the variable will be
substituted with an `empty` string.


5. Escaping: `{{...}}` renders a literal `{...}` string.


### Options

Options are passed as comma-separated key=value pairs inside
parentheses, e.g., (`position=bottom-center`, `size=10`).

#### Positioning and layout options

`position=<keyword>`: Preset position (top-left, mid-center,
bottom-right, etc.). Cannot be used with `x`/`y`.

`x=<dim>`, `y=<dim>`: Absolute coordinates.

`offset-x=<dim>`, `offset-y=<dim>`: Offset relative to the main
position.

Dimension values (`<dim>`) must include a unit (e.g., `10pt`,
`5cm`, `20%`) or default to points (pt). Supported units are
pt, in, cm, mm, and %.

`rotate`=`<float>`: Angle in degrees (e.g., 45).

#### Formatting options

`font=<string>`: Font name (e.g., Helvetica-Bold).

`size=<float>`: Font size in points.

`color=<string>`: Text color. 1, 3, or 4 space-separated
numbers between 0 and 1. Examples: `0.5` is gray,
`1 0 0` is red, and `1 0 0 .5` is semi-transparent red.

`align=<'left'|'center'|'right'>`: Horizontal alignment.

"""

_ADD_TEXT_EXAMPLES = [
    {
        "desc": 'Add "Page X of Y" to the bottom-center of all pages',
        "cmd": (
            "in.pdf add_text "
            "'1-end/Page {page} of {total}/(position=bottom-center, size=10, offset-y=10pt)'"
            " output out.pdf"
        ),
    },
    {
        "desc": 'Add a large, rotated "DRAFT" watermark to odd pages',
        "cmd": (
            "in.pdf add_text "
            "'odd!DRAFT!(position=mid-center, font=Helvetica-Bold, "
            "size=72, rotate=45, color=0.8 0.8 0.8)'"
            " output out.pdf"
        ),
    },
    {
        "desc": "Add a header with the document's title to page 1",
        "cmd": (
            "in.pdf add_text "
            "'1/Document: {meta:Title}/(x=1cm,y=28cm,font=Times-Bold,size=14)'"
            " output out.pdf"
        ),
    },
]


def _build_static_context(pdf: "Pdf", total_pages: int) -> dict:
    """Builds the context dict for variables that are the same for all pages."""
    from pathlib import Path

    try:
        # .docinfo is a property that lazy-loads the info dict
        metadata = {str(k).lstrip("/"): str(v) for k, v in pdf.docinfo.items()}
    except (AttributeError, TypeError, ValueError):
        logger.warning("Could not read PDF metadata for variable substitution.")
        metadata = {}

    filename = ""
    filename_base = ""
    if pdf.filename:
        filename = Path(pdf.filename).name
        filename_base = Path(pdf.filename).stem

    now = datetime.now()
    return {
        "total": total_pages,
        "metadata": metadata,
        "filename": filename,
        "filename_base": filename_base,
        "filepath": pdf.filename,
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "datetime": now.isoformat(),
    }


@register_operation(
    "add_text",
    tags=["in_place", "text"],
    type="single input operation",
    desc="Add user-specified text strings to PDF pages",
    long_desc=_ADD_TEXT_LONG_DESC,
    usage="<input> add_text <spec>... output <file> [<option>...]",
    examples=_ADD_TEXT_EXAMPLES,
    args=(["input_pdf", "operation_args"], {}),
)
def add_text_pdf(pdf: "Pdf", specs: list[str]) -> "Pdf":
    """
    Applies all parsed add_text rules to a PDF **in-place**.

    This function coordinates the parser and the TextDrawer to
    apply text overlays to the input PDF.
    """
    from pikepdf import Rectangle

    from .helpers.text_drawer import TextDrawer
    from .parsers.add_text_parser import parse_add_text_specs_to_rules

    total_pages = len(pdf.pages)

    # --- 1. Build static context ---
    static_context = _build_static_context(pdf, total_pages)

    # --- 2. Parse all specs ---
    try:
        page_rules = parse_add_text_specs_to_rules(specs, total_pages)
        logger.debug("page_rules=%s", page_rules)
    except ValueError as exc:
        raise InvalidArgumentError(f"Error in add_text spec: {exc}") from exc

    if not page_rules:
        return pdf  # No rules, return the original PDF

    # --- 3. Check for TextDrawer dependency ---
    # We must instantiate the TextDrawer *once* to check for the
    # import error. If it passes, we can proceed.
    # We pass a dummy page_box just for the init check.
    _ = TextDrawer(page_box=Rectangle(0, 0, 1, 1))

    # --- 4. Process all pages in-place ---
    for i, page in enumerate(pdf.pages):
        _process_page(i, page, page_rules, static_context, TextDrawer)

    return pdf


def _process_page(i, page, page_rules, static_context, drawer_class):

    from pikepdf import Rectangle

    rules_for_page = page_rules.get(i)
    if not rules_for_page:
        return

    page_box = Rectangle(*page.trimbox)
    page_context = {**static_context, "page": i + 1}

    # --- 5. Delegate drawing to drawer = TextDrawer ---
    # Create a new drawer for each page
    drawer = drawer_class(page_box=page_box)

    for rule in rules_for_page:
        drawer.draw_rule(rule, page_context)

    # Get the completed overlay PDF bytes
    overlay_bytes = drawer.save()

    # --- 6. Apply the overlay ---
    if overlay_bytes:
        from pikepdf import Pdf
        from pikepdf.exceptions import PdfError

        try:
            with Pdf.open(io.BytesIO(overlay_bytes)) as overlay_pdf:
                # This mutates the page object *in-place*
                page.add_overlay(overlay_pdf.pages[0])
        except (PdfError, TypeError) as e:
            logger.warning("Failed to apply overlay to page %d: %s", i + 1, e)
