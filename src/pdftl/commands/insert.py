# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/insert.py

"""Insert blank pages into a PDF file"""

import logging
from typing import TYPE_CHECKING, Optional

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pikepdf import Pdf, Array

import pdftl.core.constants as c
from pdftl.commands.parsers.crop_parser import parse_paper_spec
from pdftl.commands.parsers.insert_parser import InsertSpec, parse_insert_args
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.exceptions import UserCommandLineError
from pdftl.utils.dimensions import dim_str_to_pts
from pdftl.utils.page_specs import page_numbers_matching_page_spec

_INSERT_LONG_DESC = """

Inserts blank pages before or after specific pages.

Syntax: `[N][(spec)] [{after|before} [<range>]]`

Arguments:
  - `N`: Count of pages to insert (default: 1).
  - `(spec)`: Geometry/size specification.
  - `range`: Target pages (default: 1-end).
  - `after/before`: Position relative to target (default: after).

Geometry Specifications:
  - `(A4)`, `(letter)`: Standard paper sizes, case insensitive.
    Append `_l` for landscape: `(a4_l)`.
  - `(20cm, 10cm)`: Custom dimensions (width, height).
  - `(50%, 100%)`: Dimensions relative to the target page.
  - `(model=N)`: Copy geometry from page N.
"""

_INSERT_EXAMPLES = [
    {
        "cmd": "in.pdf insert output out.pdf",
        "desc": "Insert 1 blank page after every page, copying its geometry.",
    },
    {"cmd": "in.pdf insert (A4) output out.pdf", "desc": "Insert an A4 page after every page."},
    {
        "cmd": "in.pdf insert '(210mm,297mm)' after end output out.pdf",
        "desc": "Append an A4 page (defined by dims) to the end.",
    },
    {
        "cmd": "in.pdf insert 2 after 1 output out.pdf",
        "desc": "Insert 2 blank pages after page 1.",
    },
    {"cmd": "in.pdf insert (50%,100%)", "desc": "Insert a half-width page after every page."},
    {
        "cmd": "in.pdf insert output out.pdf",
        "desc": "Insert 1 blank page after every page (using defaults).",
    },
    {
        "cmd": "in.pdf insert '(50%,100%)' after 1 output out.pdf",
        "desc": "Insert a half-width page (relative to page 1) after page 1.",
    },
]


@register_operation(
    "insert",
    tags=["pages", "geometry"],
    type="single input operation",
    desc="Insert blank pages",
    long_desc=_INSERT_LONG_DESC,
    usage="<input> insert [N][(geometry)] [{after|before} <range>] ...",
    examples=_INSERT_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def insert_pages(pdf: "Pdf", args: list) -> OpResult:
    """Insert blank pages into the PDF."""
    import pikepdf

    spec = parse_insert_args(args)
    total_pages = len(pdf.pages)

    target_page_nums = page_numbers_matching_page_spec(spec.target_page_spec, total_pages)
    if not target_page_nums:
        logger.warning("Insert command matched no pages (range: %s).", spec.target_page_spec)
        return OpResult(success=True, pdf=pdf)

    # Resolve Model Indices if needed
    model_indices = _resolve_model_indices(spec, total_pages)
    is_model_mode = bool(model_indices)

    # Plan Actions
    actions = []
    for i, target_page_num in enumerate(target_page_nums):
        target_idx = target_page_num - 1
        insert_at = target_idx + 1 if spec.mode == "after" else target_idx

        media_box, crop_box, trim_box = _resolve_geometry(
            pdf, spec, is_model_mode, model_indices, target_idx, i
        )
        actions.append((insert_at, media_box, crop_box, trim_box))

    # Execute Actions (Reverse sort to keep indices valid)
    actions.sort(key=lambda x: x[0], reverse=True)
    dummy_pdf = pikepdf.new()

    count_inserted = 0
    for insert_idx, media_box, crop_box, trim_box in actions:
        for _ in range(spec.insert_count):
            new_page = dummy_pdf.add_blank_page()
            new_page.MediaBox = list(media_box)  # type: ignore[call-overload]
            if crop_box:
                new_page.CropBox = list(crop_box)  # type: ignore[call-overload]
            if trim_box:
                new_page.TrimBox = list(trim_box)  # type: ignore[call-overload]

            pdf.pages.insert(insert_idx, new_page)
            count_inserted += 1

    logger.info("Inserted %d blank pages.", count_inserted)
    return OpResult(success=True, pdf=pdf)


def _resolve_model_indices(spec: InsertSpec, total_pages: int) -> list[int]:
    """Resolves 'model=N' or 'model=N-M' to a list of page indices."""
    if not spec.geometry_spec or not spec.geometry_spec.startswith("model="):
        return []

    model_str = spec.geometry_spec.split("=", 1)[1]
    nums = page_numbers_matching_page_spec(model_str, total_pages)

    if not nums:
        raise UserCommandLineError(f"Model spec '{model_str}' matched no pages.")

    return [n - 1 for n in nums]


def _resolve_geometry(
    pdf: "Pdf",
    spec: InsertSpec,
    is_model_mode: bool,
    model_indices: list[int],
    target_idx: int,
    seq_idx: int,
) -> tuple["Array", Optional["Array"], Optional["Array"]]:
    """Determines the boxes for the new page based on the strategy."""
    from pikepdf import Array

    # Identify context for relative units (%, etc)
    target_page = pdf.pages[target_idx]
    target_media = target_page.MediaBox
    ref_w = float(target_media[2]) - float(target_media[0])
    ref_h = float(target_media[3]) - float(target_media[1])

    # Strategy A: Explicit Geometry
    if spec.geometry_spec and not is_model_mode:
        # 1. Try named paper size
        paper_size = parse_paper_spec(spec.geometry_spec)

        # 2. Try custom dimensions (e.g. "20cm, 50mm" or "50%, 100%")
        if not paper_size and "," in spec.geometry_spec:
            parts = [p.strip() for p in spec.geometry_spec.split(",")]
            if len(parts) == 2:
                try:
                    # New util usage:
                    w = dim_str_to_pts(parts[0], total_dimension=ref_w)
                    h = dim_str_to_pts(parts[1], total_dimension=ref_h)
                    paper_size = (w, h)
                except ValueError:
                    # Fallthrough to unknown spec error
                    pass

        if paper_size:
            # Create a clean MediaBox at 0,0
            return Array([0, 0, paper_size[0], paper_size[1]]), None, None

        raise UserCommandLineError(f"Unknown geometry spec: {spec.geometry_spec}")

    # Strategy B: Model Page
    if is_model_mode:
        source_page = pdf.pages[model_indices[seq_idx % len(model_indices)]]
    else:
        # Strategy C: Relative (Copy Target Page directly)
        source_page = target_page

    return (
        getattr(source_page, "MediaBox"),
        getattr(source_page, "CropBox", None),
        getattr(source_page, "TrimBox", None),
    )
