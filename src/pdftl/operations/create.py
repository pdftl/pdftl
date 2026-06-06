"""Create a new PDF file from scratch"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import UserCommandLineError
from pdftl.operations.parsers.paper_parser import parse_paper_spec
from pdftl.operations.parsers.create_parser import parse_create_args
from pdftl.utils.blank_page import make_blank_page
from pdftl.utils.dimensions import dim_str_to_pts

logger = logging.getLogger(__name__)

_CREATE_LONG_DESC = """
Creates a new PDF document. Currently generates blank pages, but will
be extensible for other content sources in the future.

Syntax: `[N][(spec)]`

Arguments:
  - `N`: Count of pages to create (default: 1).
  - `(spec)`: Geometry/size specification (default: A4).
"""


@register_operation(
    "create",
    tags=["pages", "geometry", "source"],
    type="source operation",  # Note: No input PDF required
    desc="Create a new PDF",
    long_desc=_CREATE_LONG_DESC,
    usage="create [N][(geometry)] output <output>",
    examples=[
        {"cmd": "create output out.pdf", "desc": "Create a 1-page A4 blank PDF."},
        {"cmd": "create '5(letter)' output out.pdf", "desc": "Create a 5-page letter-sized PDF."},
    ],
    args=([c.OPERATION_ARGS], {}),
)
def create_pdf(args: list[str]) -> OpResult:
    """Create a new PDF from scratch with blank pages."""
    import pikepdf

    specs = parse_create_args(args)
    new_pdf = pikepdf.new()

    for spec in specs:
        insert_count = spec["count"]

        # Fallback to standard (A4) if no geometry spec was provided by the user
        geometry_spec = spec.get("geometry", None) or "A4"

        # 2. Resolve Geometry
        # First, try standard names/aliases (e.g., "A4", "letter", "A4_l")
        paper_size = parse_paper_spec(geometry_spec)

        # If it's not a standard name, check for custom dimensions like (20cm, 10cm)
        if not paper_size and "," in geometry_spec:
            try:
                # Strip outer parentheses and split by comma
                parts = [p.strip() for p in geometry_spec.strip("()").split(",")]
                if len(parts) != 2:
                    raise ValueError("Geometry must consist of exactly a width and a height.")
                width = dim_str_to_pts(parts[0])
                height = dim_str_to_pts(parts[1])
                paper_size = (width, height)
            except (ValueError, KeyError) as exc:
                raise UserCommandLineError(
                    f"Invalid custom geometry format '{geometry_spec}'. "
                    f"Expected format: (width, height) e.g., (21cm, 29.7cm)"
                ) from exc

        # If both resolution methods fail, bail out gracefully
        if not paper_size:
            raise UserCommandLineError(
                f"Unknown geometry specification: '{geometry_spec}'.\n"
                "  Must be a known format (e.g., A4, letter, A3_l) "
                "or custom dimensions (e.g., '20cm,10cm')."
            )

        # Define the bounding box for the new pages
        media_box = pikepdf.Array([0, 0, paper_size[0], paper_size[1]])

        # 3. Create the Document

        for _ in range(insert_count):
            make_blank_page(new_pdf, media_box)

    logger.info("Created new PDF with %d pages", len(new_pdf.pages))
    return OpResult(success=True, pdf=new_pdf)
