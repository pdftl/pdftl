# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/add_text.py

"""Add user-specified text strings to PDF pages

This operation uses a helper class to create text overlays,
which are then applied to the target pages.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Pdf

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.text_templates import build_static_context, build_page_context

logger = logging.getLogger(__name__)

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

   - `n` or `count`: The sequence number (1-based index within the current spec)

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


2. Source Metadata (Pipeline): These variables track the original
source file of a page, even after operations like `cat` or `shuffle`.

   - `source_filename`: The filename of the specific source PDF this page came from.

   - `source_path`: The full file path of the source PDF.

   - `source_page`: The original page number in the source file.

   - `source_rotation`: The rotation of the source page (0, 90, 180, 270).

   - `source_width` / `source_height`: The dimensions of the source page.

   - `source_orientation`: "Portrait" or "Landscape".


3. Arithmetic & Formatting: Support for offsets and Python-style
padding. Useful for Bates stamping.
   - Offset: `{page+100}` starts numbering at 101.
   - Padding: `{page:06d}` produces "000001".
   - Combined: `{page+5000:06d}` produces "005001".

4. Complex: e.g., {total-page} gives the number of pages
remaining.  (for now, this is the only complex possibility).

5. Metadata: e.g., {meta:Title}. The metadata variables
`allow` you to insert information stored within the PDF
document's own metadata dictionary (`/Title`, `/Author`,
etc.) into your text.

The format for a metadata variable is: {meta:`<KeyName>`}
where `<KeyName>` is the exact, case-sensitive key found in
the PDF's document information dictionary (it corresponds to
the PDF keys like `/Title` after the leading slash is
stripped).

The available keys are determined by the contents of the PDF
itself, but common examples derived from the PDF
specification include: Title, Author, Subject, Keywords,
Creator, Producer, CreationDate. If the specified `<KeyName>`
does not exist in the PDF's metadata, the variable will be
substituted with an `empty` string.


6. Escaping: `{{...}}` renders a literal `{...}` string.


### Hyperlinks

You can create clickable hyperlinks within your text using standard
Markdown syntax: `[Display Text](URL)`. Dynamic variables are supported
in both the display text and the URL.

Example: `[Visit Page {page}](https://example.com/p{page})`
Escaping: Use `\[` and `\]` if you need literal brackets inside the text.


### Options

Options are passed as comma-separated key=value pairs inside
parentheses, e.g., (`position=bottom-center`, `size=10`).

#### Positioning and layout options

`position=<keyword>`: Preset position (top-left, center, mid-center,
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

`bgcolor=<string>`: Background color. 1, 3, or 4 space-separated
numbers between 0 and 1.

`padding=<dim>`: Padding for background colored rectangle, if present

`linkcolor=<string>`: Color for hyperlinks (uses the same format as `color`).
Defaults to the main `color` if not set.

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
    {
        "desc": "Stamp pages with their original filename (useful in pipelines)",
        "cmd": (
            "A.pdf B.pdf cat --- add_text "
            "'1-end/Source: {source_filename} (p.{source_page})/"
            "(position=bottom-left, size=8)' "
            "output out.pdf"
        ),
    },
    {
        "desc": "Apply a Bates stamp (starting at DEF-005001) to the bottom-right",
        "cmd": (
            "in.pdf add_text "
            "'/DEF-{page+5000:06d}/(position=bottom-right, size=10, color=1 0 0)' "
            "output out.pdf"
        ),
    },
    {
        "desc": "Add a clickable Markdown link with a custom link color",
        "cmd": (
            "in.pdf add_text "
            "'/Visit [Our Website](https://example.com)/"
            "(position=top-right, size=12, linkcolor=0 0 1)' "
            "output out.pdf"
        ),
    },
    {
        "desc": "Apply sequential sequence numbering that resets per specification",
        "cmd": (
            "in.pdf add_text "
            "'1-3/Exhibit A-{n}/(position=top-left, size=10)' "
            "'4-6/Exhibit B-{n}/(position=top-left, size=10)' "
            "output out.pdf"
        ),
    },
]


@register_operation(
    "add_text",
    tags=["in_place", "text"],
    type="single input operation",
    desc="Add user-specified text strings to PDF pages",
    long_desc=_ADD_TEXT_LONG_DESC,
    usage="<input> add_text <spec>... output <file> [<option>...]",
    examples=_ADD_TEXT_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def add_text_pdf(pdf: "Pdf", specs: list[str]) -> OpResult:
    """
    Applies all parsed add_text rules to a PDF **in-place**.

    This function coordinates the parser and the TextDrawer to
    apply text overlays to the input PDF.
    """
    from pikepdf import Rectangle
    from pdftl.operations.helpers.text_drawer import TextDrawer
    from pdftl.operations.parsers.add_text_parser import parse_add_text_specs_to_rules

    static_context = build_static_context(pdf)

    try:
        page_rules = parse_add_text_specs_to_rules(specs, len(pdf.pages))
        logger.debug("page_rules=%s", page_rules)
    except ValueError as exc:
        raise InvalidArgumentError(f"Error in add_text spec: {exc}") from exc

    if not page_rules:
        return OpResult(success=True, pdf=pdf)

    _ = TextDrawer(page_box=Rectangle(0, 0, 1, 1))
    drawer = TextDrawer(page_box=Rectangle(0, 0, 1, 1))
    overlay_page_indices = _build_overlay_index(pdf, page_rules, static_context, drawer)
    overlay_bytes = drawer.save()

    if overlay_bytes:
        _apply_overlays(pdf, overlay_bytes, overlay_page_indices)

    return OpResult(success=True, pdf=pdf)


def _build_overlay_index(pdf, page_rules, static_context, drawer):
    """Populate the shared drawer canvas and return a mapping of original→overlay page indices."""
    overlay_page_indices = {}
    for i, page in enumerate(pdf.pages):
        rules_for_page = page_rules.get(i)
        if not rules_for_page:
            continue
        _generate_page_overlay(i, page, rules_for_page, static_context, drawer)
        overlay_page_indices[i] = len(overlay_page_indices)
    return overlay_page_indices


def _apply_overlays(pdf, overlay_bytes, overlay_page_indices):
    """Merge the unified overlay PDF back onto the original pages."""
    import io
    from pikepdf import Pdf as PikePdf
    from pikepdf.exceptions import PdfError

    try:
        with PikePdf.open(io.BytesIO(overlay_bytes)) as overlay_pdf:
            for original_idx, generated_idx in overlay_page_indices.items():
                if generated_idx < len(overlay_pdf.pages):
                    target_page = pdf.pages[original_idx]
                    overlay_page = overlay_pdf.pages[generated_idx]
                    target_page.add_overlay(overlay_page)
                    _copy_annotations(target_page, overlay_page, pdf)
    except (PdfError, TypeError) as e:
        logger.warning("Failed to apply global resource overlay map: %s", e)


def _generate_page_overlay(i, page, rules_for_page, static_context, drawer):
    from pikepdf import Rectangle

    # Use TrimBox if available, fallback to CropBox/MediaBox.
    page_box = Rectangle(*page.trimbox)

    # Get the unrotated physical dimensions
    physical_width = float(page_box.width)
    physical_height = float(page_box.height)

    # Extract rotation safely (avoids the bound method bug)
    rotation = int(page.get("/Rotate", 0)) % 360

    # Calculate visual dimensions
    if rotation in (90, 270):
        visual_width = physical_height
        visual_height = physical_width
    else:
        visual_width = physical_width
        visual_height = physical_height

    # --- Build Page Context ---
    page_context = build_page_context(static_context, page, i + 1)

    # --- Update single drawer configuration safely ---
    visual_page_box = Rectangle(0, 0, visual_width, visual_height)
    drawer.reset_page_box(visual_page_box)

    for rule in rules_for_page:
        page_context["n"] = rule.get("n", 1)
        drawer.draw_rule(rule, page_context)


def _get_page_origin(page):
    box = page.trimbox
    return float(box[0]), float(box[1])


def _translate_rect(rect, ox, oy):
    return [float(a) + b for a, b in zip(rect, [ox, oy, ox, oy])]


def _rotate_rect(rect, page):
    """
    Maps a rect from Visual Space (what the user sees)
    to Physical Space (the PDF dictionary).
    """
    rotation = int(page.get("/Rotate", 0)) % 360
    if rotation == 0:
        return rect

    # These are the PHYSICAL dimensions (e.g., 500 x 800)
    p1, p2, p3, p4 = page.trimbox
    w, h = float(p3 - p1), float(p4 - p2)

    x1, y1, x2, y2 = [float(t) for t in rect]

    if rotation == 90:
        # 90 CW: The visual X is the physical Y.
        # The visual Y is the physical (Width - X).
        return [w - y2, x1, w - y1, x2]

    if rotation == 180:
        return [w - x2, h - y2, w - x1, h - y1]

    if rotation == 270:
        # 270 CW: The visual X is physical (Height - Y)
        # The visual Y is physical X
        return [y1, h - x2, y2, h - x1]

    return rect


def _copy_annotations(page, overlay_page, pdf):
    """Copies annotations from an overlay page to the target page."""
    from pikepdf import Array, Name

    overlay_annots = getattr(overlay_page, "Annots", None)
    if not overlay_annots:
        return

    if Name.Annots not in page:
        page[Name.Annots] = Array()

    for annot in overlay_annots:
        new_annot = pdf.copy_foreign(annot)
        if r := getattr(new_annot, "Rect", None):
            ox, oy = _get_page_origin(page)
            rotated = _rotate_rect(r, page)
            translated = _translate_rect(rotated, ox, oy)
            new_annot.Rect = translated
        page[Name.Annots].append(new_annot)
