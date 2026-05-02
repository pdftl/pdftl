# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/highlight.py

"""Superimpose highlight boxes over text matching a regex pattern"""

import io
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Pdf

import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.page_specs import page_numbers_matching_page_spec
from pdftl.utils.string_utils import split_escaped
from pdftl.utils.dependencies import ensure_dependencies
from pdftl.utils.keyval_parser import parse_keyval_string

logger = logging.getLogger(__name__)

_HIGHLIGHT_LONG_DESC = r"""

The `highlight` operation searches for text matching a regular
expression and superimposes a highlight annotation over its
exact visual coordinates.

**Note**: This operation requires the 'pypdfium2' library. If not
installed, run: `pip install pdftl[highlight]`.

A highlight specification (`<spec>`) has the format:

>  `[optional page range]<delimiter><regex><delimiter>[<options>]`

`<delimiter>` must be a single, non-alphanumeric character
(e.g., `/`, `!`, `#`).

### Options
Options are provided as a comma-separated list enclosed in parentheses
at the end of the spec.
- `author`: The author of the annotation (default: empty).
- `contents`: Pop-up text associated with the highlight.
- `color`: Space-separated RGB values, e.g., `1 1 0` for yellow (default).
- `print`: `yes` or `no`. Whether the highlight prints (default: `yes`).
- `opacity`: Float between 0.0 and 1.0.

### Examples of specifications:
- `1-3/CONFIDENTIAL/`
- `/Jane Doe/(author=Legal, color=0 1 0)`
- `odd!INV-\d{5}!(contents=Check this invoice, print=no)`

"""

_HIGHLIGHT_EXAMPLES = [
    {
        "desc": 'Highlight all instances of "CONFIDENTIAL" on all pages',
        "cmd": "in.pdf highlight '/CONFIDENTIAL/' output out.pdf",
    },
    {
        "desc": "Highlight specific invoice number formats on pages 1 through 5",
        "cmd": r"in.pdf highlight '1-5/INV-\d{4,5}/' output out.pdf",
    },
    {
        "desc": "Highlight in green, with custom author and pop-up note",
        "cmd": (
            "in.pdf highlight "
            "'/URGENT/(author=Jane, color=0 1 0, contents=Fix this)' output out.pdf"
        ),
    },
]


@register_operation(
    "highlight",
    tags=["in_place", "text", "annotations"],
    type="single input operation",
    desc="Highlight text matching a regex pattern",
    long_desc=_HIGHLIGHT_LONG_DESC,
    usage="<input> highlight [<spec>...] output <output>",
    examples=_HIGHLIGHT_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def highlight_pdf(pdf: "Pdf", specs: list[str]) -> OpResult:
    """
    Applies all parsed highlight rules to a PDF in-place.
    """
    ensure_dependencies("highlight", ["pypdfium2"], "highlight")
    import pypdfium2 as pdfium

    if not specs:
        return OpResult(success=True, pdf=pdf)

    with io.BytesIO() as buffer:
        pdf.save(buffer)
        buffer.seek(0)

        with pdfium.PdfDocument(buffer) as pdfium_doc:
            for spec in specs:
                _apply_highlight_spec(pdf, pdfium_doc, spec)

    return OpResult(success=True, pdf=pdf)


def _find_options_part(s: str) -> tuple[str, str]:
    """Extracts balanced (...) options block from the end of the string."""
    options_part = ""
    rest_of_spec = s
    if not s.endswith(")"):
        return options_part, rest_of_spec

    nest_level = 0
    split_pos = -1
    for i in range(len(s) - 1, -1, -1):
        char = s[i]
        if char == ")":
            nest_level += 1
        elif char == "(":
            nest_level -= 1

        if nest_level == 0 and char == "(":
            split_pos = i
            break

    if split_pos != -1:
        options_part = s[split_pos:].strip()
        rest_of_spec = s[:split_pos].strip()

    return options_part, rest_of_spec


def _parse_color(color_str: str) -> list[float]:
    """Parses a space-separated color string into a list of floats."""
    try:
        parts = [float(c) for c in color_str.split()]
    except ValueError as exc:
        raise InvalidArgumentError(f"Invalid characters in color string: '{color_str}'") from exc

    num_parts = len(parts)
    if num_parts in (1, 3, 4):
        return parts

    raise InvalidArgumentError(
        f"Color string '{color_str}' must have 1, 3, or 4 space-separated numbers."
    )


def _parse_options(options_part: str) -> dict:
    """Parses the raw (key=value, ...) options string into a typed dictionary."""
    if not options_part:
        return {}

    # Strip the outer parentheses
    content = options_part[1:-1].strip()
    raw_opts = parse_keyval_string(content, lowercase_keys=True, context="highlight")

    opts = {}
    if "author" in raw_opts:
        opts["author"] = raw_opts["author"].strip("'\"")
    if "contents" in raw_opts:
        opts["contents"] = raw_opts["contents"].strip("'\"")
    if "color" in raw_opts:
        opts["color"] = _parse_color(raw_opts["color"].strip("'\""))
    if "print" in raw_opts:
        opts["print"] = raw_opts["print"].lower() in ("yes", "true", "1")
    if "opacity" in raw_opts:
        try:
            opts["opacity"] = float(raw_opts["opacity"])
        except ValueError:
            raise InvalidArgumentError(f"Invalid opacity value: '{raw_opts['opacity']}'")

    return opts


def _parse_highlight_spec(spec: str) -> tuple[str, str, dict]:
    """Splits the raw spec into page ranges, regex string, and parsed options."""
    if not spec:
        raise InvalidArgumentError("Empty highlight specification.")

    options_part, rest_of_spec = _find_options_part(spec)

    if not rest_of_spec:
        raise InvalidArgumentError(f"Missing regex in spec '{spec}'")

    delim = rest_of_spec[-1]
    spec_parts = split_escaped(rest_of_spec, delim)

    if len(spec_parts) != 3:
        raise InvalidArgumentError(
            f"Highlight specification '{rest_of_spec}' does not look correct. "
            "Expected format: [page_range]<delim><regex><delim>[options]"
        )

    page_spec = spec_parts[0] if spec_parts[0] else "1-end"
    regex_str = spec_parts[1]
    options = _parse_options(options_part)

    return page_spec, regex_str, options


def _apply_highlight_spec(pdf: "Pdf", pdfium_doc, spec: str):
    """Parses a spec and orchestrates highlighting across matching pages."""
    page_spec, regex_str, options = _parse_highlight_spec(spec)

    try:
        compiled_pattern = re.compile(regex_str, re.IGNORECASE)
    except re.error as exc:
        raise InvalidArgumentError(f"Invalid regular expression '{regex_str}': {exc}") from exc

    num_pages = len(pdf.pages)

    for page_num in page_numbers_matching_page_spec(page_spec, num_pages):
        idx = page_num - 1
        _process_highlight_page(pdf, pdfium_doc, idx, compiled_pattern, options)


def _process_highlight_page(
    pdf: "Pdf", pdfium_doc, idx: int, compiled_pattern: re.Pattern, options: dict
):
    """Extracts text for a single page and applies matched annotations."""
    pdfium_page = pdfium_doc.get_page(idx)
    try:
        textpage = pdfium_page.get_textpage()
        try:
            page_text = textpage.get_text_range()
            if not page_text:
                return

            page_annots = _generate_annotations_for_text(
                textpage, page_text, compiled_pattern, options
            )

            if page_annots:
                pike_page = pdf.pages[idx]
                if "/Annots" not in pike_page:
                    pike_page.Annots = []
                for annot in page_annots:
                    pike_page.Annots.append(annot)

        finally:
            textpage.close()
    finally:
        pdfium_page.close()


def _generate_annotations_for_text(
    textpage, page_text: str, compiled_pattern: re.Pattern, options: dict
) -> list:
    """Finds regex matches in the text string and generates bounding box annotations."""
    page_annots = []

    for match in compiled_pattern.finditer(page_text):
        start_idx = match.start()
        length = match.end() - start_idx

        if length <= 0:
            continue

        rect_count = textpage.count_rects(start_idx, length)

        for i in range(rect_count):
            left, bottom, right, top = textpage.get_rect(i)
            annot = _build_highlight_annotation(left, bottom, right, top, options)
            page_annots.append(annot)

    return page_annots


def _build_highlight_annotation(
    left: float, bottom: float, right: float, top: float, options: dict
):
    """Constructs a single PDF highlight annotation dictionary."""
    import pikepdf

    quad_points = [
        left,
        top,  # Top-Left
        right,
        top,  # Top-Right
        left,
        bottom,  # Bottom-Left
        right,
        bottom,  # Bottom-Right
    ]

    highlight_annot = pikepdf.Dictionary(
        Type=pikepdf.Name("/Annot"),
        Subtype=pikepdf.Name("/Highlight"),
        Rect=[left, bottom, right, top],
        QuadPoints=quad_points,
        C=options.get("color", [1.0, 1.0, 0.0]),
    )

    if options.get("print", True):
        highlight_annot.F = 4
    if "author" in options:
        highlight_annot.T = pikepdf.String(options["author"])
    if "contents" in options:
        highlight_annot.Contents = pikepdf.String(options["contents"])
    if "opacity" in options:
        highlight_annot.CA = options["opacity"]

    return highlight_annot
