# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/booklet.py

"""Impose pages into printable booklet signatures."""

from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from pikepdf import Page, Pdf

import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.layouts import GridLayout
from pdftl.operations.montage import _apply_montage_logic
from pdftl.operations.parsers.paper_parser import parse_paper_spec
from pdftl.utils.blank_page import make_blank_page
from pdftl.utils.dimensions import get_visible_page_dimensions
from pdftl.utils.page_specs import expand_specs_to_pages

_BOOKLET_LONG_DESC = """
The `booklet` operation arranges pages so they can be printed as a foldable booklet.
It automatically pads the document with blank pages to a multiple of 4,
reorders the pages into printing signatures, and imposes them 2-up onto
landscape sheets.

By default, it creates one giant signature (meaning you fold the entire
stack of paper in half). For larger books, you can specify a `sig` (signature)
size in sheets to create smaller chunks that are folded and bound together.

### Configuration Syntax

| Argument | Description |
| :--- | :--- |
| `sig=<N>` | Sheets per signature (e.g., `sig=4` = 16 pages/chunk). Default is 0 (all). |
| `canvas=<size>` | Set output page size (`A4_L`, `letter_L`). Default is auto-calculated. |
| `margin=<pts>` | Set page margin in points |
| `gutter=<pts>` | Set spacing between the two pages on the sheet |
| `rtl=true` | Use Right-to-Left binding (for Arabic, Hebrew, or Manga). |

### Example Usage

```bash
pdftl in.pdf booklet 1-end sig=4 canvas=A4_L output print_ready.pdf
```
"""

_BOOKLET_EXAMPLES = [
    {
        "cmd": "in.pdf booklet output booklet.pdf",
        "desc": "Create a standard single-signature booklet from the input PDF.",
    },
    {
        "cmd": "in.pdf booklet sig=4 output signatures.pdf",
        "desc": "Create a booklet grouped into 4-sheet (16-page) signatures.",
    },
    {
        "cmd": "manga.pdf booklet rtl=true output right_to_left.pdf",
        "desc": "Create a booklet with right-to-left reading order.",
    },
]


@register_operation(
    "booklet",
    tags=["from_scratch", "imposition", "page_order"],
    desc="Impose pages into printable booklet signatures",
    usage="<input>... booklet <spec>... output <file>",
    long_desc=_BOOKLET_LONG_DESC,
    examples=_BOOKLET_EXAMPLES,
    args=(
        [c.INPUTS, c.OPERATION_ARGS, c.OPENED_PDFS],
        {c.ALIASES: c.ALIASES},
    ),
)
def booklet_pages(inputs, specs, opened_pdfs, aliases=None) -> OpResult:
    """
    Imposes pages into a booklet sequence.
    """
    import pikepdf

    new_pdf = pikepdf.new()

    # 1. Separate Page Selectors from Config Arguments
    page_specs = []
    config = _parse_booklet_config(specs, page_specs)

    # 2. Resolve Source Pages
    if not page_specs:
        if aliases:
            page_specs = list(aliases.keys())
        elif inputs:
            page_specs = ["1-end"]

    source_pages_to_process = expand_specs_to_pages(page_specs, aliases, inputs, opened_pdfs)

    if not source_pages_to_process:
        raise ValueError("No source pages selected for booklet.")

    raw_pages = [p.pdf.pages[p.index] for p in source_pages_to_process]

    # 3. Determine Canvas Size
    if config["canvas_size"]:
        canvas_size = config["canvas_size"]
    else:
        # Smart Default: Calculate based on placing 2 of the first page side-by-side
        max_w = max(get_visible_page_dimensions(p, box="trimbox")[2] for p in raw_pages)
        max_h = max(get_visible_page_dimensions(p, box="trimbox")[3] for p in raw_pages)
        canvas_size = (max_w * 2, max_h)

    # 4. Pad and Reorder Pages
    ordered_pages = _build_booklet_permutation(raw_pages, sig=config["sig"], rtl=config["rtl"])

    # 5. Create a blank page for padding
    dummy_pdf = pikepdf.new()
    make_blank_page(dummy_pdf, raw_pages[0].trimbox)
    blank_page = dummy_pdf.pages[0]

    final_pages = [p if p is not None else blank_page for p in ordered_pages]

    # 6. Apply via Montage Engine
    # A booklet is just a 2x1 grid filled with our carefully ordered pages
    layout_strategy = GridLayout(
        columns=2, rows=1, margin=config["margin"], gutter=config["gutter"]
    )

    _apply_montage_logic(
        target_pdf=new_pdf,
        source_pages=final_pages,
        strategy=layout_strategy,
        canvas_size=canvas_size,
        preserve_aspect_ratio=True,
    )

    return OpResult(success=True, pdf=new_pdf)


def _parse_booklet_config(specs: List[str], out_page_specs: List[str]) -> Dict[str, Any]:
    """Parses booklet configuration from the command line."""
    config = {
        "sig": 0,  # 0 means one giant signature
        "canvas_size": None,
        "margin": 0.0,
        "gutter": 0.0,
        "rtl": False,
    }

    for token in specs:
        if "=" in token:
            key, val = token.split("=", 1)
            key = key.lower().strip()
            val = val.lower().strip()

            if key in ["sig", "signature"]:
                config["sig"] = int(val)
            elif key == "canvas":
                parsed_size = parse_paper_spec(val)
                if parsed_size:
                    config["canvas_size"] = parsed_size
                else:
                    raise ValueError(f"Unknown canvas size: '{val}'")
            elif key == "margin":
                config["margin"] = float(val)
            elif key == "gutter":
                config["gutter"] = float(val)
            elif key == "rtl":
                config["rtl"] = val in ["true", "1", "yes"]
        else:
            out_page_specs.append(token)

    return config


def _build_booklet_permutation(raw_pages: List["Page"], sig: int, rtl: bool) -> List["Page"]:
    """
    Chunks the document into signatures and calculates the 2-up permutation
    required for standard duplex booklet printing.
    """
    pages = list(raw_pages)

    # Pad total document to a multiple of 4
    remainder = len(pages) % 4
    if remainder != 0:
        pages.extend([None] * (4 - remainder))

    # If sig=0, use one giant signature for the entire document
    sig_pages = sig * 4 if sig > 0 else len(pages)
    ordered = []

    # Process each signature block independently
    for i in range(0, len(pages), sig_pages):
        chunk = pages[i : i + sig_pages]

        # The last chunk might be smaller than sig_pages, but is guaranteed
        # to be a multiple of 4 because of our padding above.
        c_len = len(chunk)
        sheets = c_len // 4

        for s in range(sheets):
            # For each physical sheet, we yield 4 pages:
            # Front Left, Front Right, Back Left, Back Right

            if not rtl:
                # Standard LTR reading
                ordered.append(chunk[c_len - 2 * s - 1])  # Front Left (Last)
                ordered.append(chunk[2 * s])  # Front Right (First)

                ordered.append(chunk[2 * s + 1])  # Back Left (Second)
                ordered.append(chunk[c_len - 2 * s - 2])  # Back Right (Last-1)
            else:
                # Manga / Arabic / Hebrew reading
                ordered.append(chunk[2 * s])  # Front Left (First)
                ordered.append(chunk[c_len - 2 * s - 1])  # Front Right (Last)

                ordered.append(chunk[c_len - 2 * s - 2])  # Back Left (Last-1)
                ordered.append(chunk[2 * s + 1])  # Back Right (Second)

    return ordered
