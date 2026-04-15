# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/montage.py

"""Impose pages onto a grid or custom layout."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pikepdf import Page, Pdf

import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.exceptions import InvalidArgumentError
from pdftl.layouts import GridLayout
from pdftl.operations.parsers.paper_parser import parse_paper_spec
from pdftl.utils.dimensions import dim_str_to_pts, get_visible_page_dimensions
from pdftl.utils.geometry import calculate_fit_metrics, calculate_placement_matrix
from pdftl.utils.page_specs import page_numbers_matching_page_specs

_MONTAGE_LONG_DESC = """
The `montage` operation allows you to impose multiple source pages onto a
sequence of output pages (canvases). It is useful for creating contact sheets,
N-up handouts, or tiling pages.

You can mix page selectors (like `1-5`) with configuration arguments.
Configuration arguments allow you to set the canvas size, grid topology,
margins, and spacing.

### Configuration Syntax

| Argument | Description |
| :--- | :--- |
| `canvas=<size>` | Set output page size (`A4`, `letter`, `A3`, etc.) |
| `grid=<cols>x<rows>` | Set columns and rows (e.g., `2x2`, `3x4`) |
| `margin=<pts>` | Set page margin in points |
| `gutter=<pts>` | Set spacing between slots in points |
| `fit=<mode>` | `contain` (default) preserves aspect ratio; `fill` stretches. |
"""

_MONTAGE_EXAMPLES = [
    {
        "cmd": "in.pdf montage 1-12 grid=2x2 canvas=A4 output contact.pdf",
        "desc": "Create a 2x2 contact sheet on A4 paper",
    },
    {
        "cmd": "in.pdf montage 1-end grid=4x4 margin=20 gutter=10 output thumbnails.pdf",
        "desc": "Create 4x4 thumbnails with margins",
    },
    {
        "cmd": "A=front.pdf B=back.pdf cat A1 B1 --- montage grid=2x1 output side_by_side.pdf",
        "desc": "Place page 1 of front.pdf and page 1 of back.pdf side-by-side",
    },
]


@register_operation(
    "montage",
    tags=["from_scratch", "imposition", "geometry"],
    type="single input operation",
    desc="Impose pages onto a grid layout",
    usage="<input>... montage <spec>... output <file>",
    long_desc=_MONTAGE_LONG_DESC,
    examples=_MONTAGE_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def montage_pages(pdf, operation_args) -> OpResult:
    """
    Imposes pages onto new canvas pages based on a grid or layout strategy.
    """
    import pikepdf

    new_pdf = pikepdf.new()

    # 1. Separate Page Selectors from Config Arguments
    page_specs = []
    config = _parse_montage_config(operation_args, page_specs)

    # 2. Resolve Source Pages
    # If no page specs were provided, default to all pages from all aliases/inputs
    if not page_specs:
        page_specs = ["1-end"]

    source_pages_to_process = page_numbers_matching_page_specs(page_specs, len(pdf.pages))

    if not source_pages_to_process:
        raise InvalidArgumentError("No source pages selected for montage.")

    # 3. Setup Layout Strategy
    # Currently defaults to GridLayout based on parsed config
    layout_strategy = GridLayout(
        columns=config["cols"],
        rows=config["rows"],
        margin=config["margin"],
        gutter=config["gutter"],
    )

    # 4. Execute Montage
    _apply_montage_logic(
        target_pdf=new_pdf,
        source_pages=[pdf.pages[p - 1] for p in source_pages_to_process],
        strategy=layout_strategy,
        canvas_size=config["canvas_size"],
        preserve_aspect_ratio=config["preserve_aspect_ratio"],
    )

    return OpResult(success=True, pdf=new_pdf)


def _parse_montage_config(specs: list[str], out_page_specs: list[str]) -> dict[str, Any]:
    """
    Parses the raw argument list.
    - Key=Value pairs are treated as config.
    - Everything else is appended to out_page_specs.
    """
    # Defaults
    config = {
        "cols": 2,
        "rows": 2,
        "margin": 0.0,
        "gutter": 0.0,
        "canvas_size": parse_paper_spec("a4"),  # Default A4 Portrait
        "preserve_aspect_ratio": True,
    }

    for token in specs:
        if "=" in token:
            key, val = token.split("=", 1)
            key = key.lower().strip()
            val = val.lower().strip()
            config = _update_config_from_keyval(key, val, config)

        else:
            # Assume it's a page spec (e.g. "1-5", "even", "A")
            out_page_specs.append(token)

    return config


def _update_config_from_keyval(key, val, config):
    if key == "grid":
        # Parse "2x2"
        try:
            cols, rows = val.split("x")
            config["cols"] = int(cols)
            config["rows"] = int(rows)
        except ValueError:
            raise InvalidArgumentError(
                f"Invalid grid format: '{val}'. " "Expected format: 'cols x rows', e.g. '2x2'."
            )
    elif key == "canvas":
        # Use standard pdftl parser (e.g. "a4", "a4_l", "4x6")
        parsed_size = parse_paper_spec(val)
        if parsed_size:
            config["canvas_size"] = parsed_size
        else:
            raise InvalidArgumentError(
                f"Unknown canvas size or format: '{val}'. "
                "Try standard sizes like 'a4', 'letter', 'a4_l'."
            )
    elif key == "margin":
        config["margin"] = dim_str_to_pts(val)
    elif key == "gutter":
        config["gutter"] = dim_str_to_pts(val)
    elif key in ("cols", "rows"):
        try:
            config[key] = int(val)
        except ValueError as e:
            raise InvalidArgumentError(f"Could not parse {key} value '{val}' as an integer. ({e})")
    elif key == "fit":
        config["preserve_aspect_ratio"] = val != "fill"

    return config


def _apply_montage_logic(
    target_pdf: "Pdf",
    source_pages: list["Page"],
    strategy: GridLayout,
    canvas_size: tuple[float, float],
    preserve_aspect_ratio: bool,
):
    """
    The engine that combines Layout, Geometry, and Pikepdf to build the document.
    """
    from pikepdf import Name

    try:
        target_w, target_h = canvas_size
    except TypeError:
        raise InvalidArgumentError(f"Invalid canvas_size: {canvas_size}")

    # Generate Layout Slots
    layout_stream = strategy.generate_slots(
        item_count=len(source_pages), canvas_width=target_w, canvas_height=target_h
    )

    # Cache output pages: index -> Page object
    output_pages = {}

    for src_page, slot in zip(source_pages, layout_stream):

        # A. Ensure Target Page Exists
        if slot.page_index not in output_pages:
            while len(target_pdf.pages) <= slot.page_index:
                target_pdf.add_blank_page(page_size=canvas_size)
            output_pages[slot.page_index] = target_pdf.pages[slot.page_index]

        target_page = output_pages[slot.page_index]

        # B. Calculate Fit
        _, _, src_w, src_h = get_visible_page_dimensions(src_page, box="trimbox")

        src_rotation = int(src_page.get("/Rotate", 0))

        sx, sy, off_x, off_y = calculate_fit_metrics(
            src_w, src_h, slot.width, slot.height, preserve_aspect_ratio
        )

        # C. Calculate Placement Matrix
        # Slot gives the bottom-left of the cell.
        # Fit metrics give the offset from that bottom-left to center the content.
        final_x = slot.x + off_x
        final_y = slot.y + off_y

        matrix = calculate_placement_matrix(
            source_page=src_page,
            dest_x=final_x,
            dest_y=final_y,
            scale_x=sx,
            scale_y=sy,
            rotate=src_rotation,
            anchor_source="bottom-left",  # fit_metrics assumes simple bottom-left box match
            anchor_target="bottom-left",
        )

        # D. Stamp (Form XObject)
        # Convert source page to a Form XObject in its original PDF context first
        src_form = src_page.as_form_xobject()

        # Then copy the Form XObject over to the target PDF
        form_xobj = target_pdf.copy_foreign(src_form)

        # Add to the target page's resources
        form_name = target_page.add_resource(form_xobj, Name("/XObject"))

        # Apply Matrix (a, b, c, d, e, f)
        m = matrix
        cmd = f"q {m.a:.6f} {m.b:.6f} {m.c:.6f} {m.d:.6f} {m.e:.6f} {m.f:.6f} cm {form_name} Do Q "
        target_page.contents_add(cmd.encode("ascii"))
