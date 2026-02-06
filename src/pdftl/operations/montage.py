# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/montage.py

"""Impose pages onto a grid or custom layout."""

from typing import TYPE_CHECKING, Any, Dict, List, Tuple

if TYPE_CHECKING:
    from pikepdf import Page, Pdf

import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.layouts import GridLayout
from pdftl.utils.geometry import calculate_fit_metrics, calculate_placement_matrix
from pdftl.utils.page_specs import expand_specs_to_pages

_MONTAGE_LONG_DESC = """
The `montage` operation allows you to impose multiple source pages onto a
sequence of output pages (canvases). It is useful for creating contact sheets,
N-up handouts, or tiling pages.

You can mix page selectors (like `1-5`) with configuration arguments.
Configuration arguments allow you to set the canvas size, grid topology,
margins, and spacing.

Configuration Syntax:
  `canvas=A4`      Set output page size (A4, Letter, A3, etc.)
  `grid=2x2`       Set columns and rows (e.g., 2x2, 3x4)
  `margin=10`      Set page margin in points
  `gutter=5`       Set spacing between slots in points
  `fit=contain`    'contain' (default) preserves aspect ratio; 'fill' stretches.

Example:
  pdftl in.pdf montage 1-10 grid=2x2 canvas=A4 output out.pdf
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
        "cmd": "A=front.pdf B=back.pdf montage A1 B1 grid=2x1 output side_by_side.pdf",
        "desc": "Place page 1 of front.pdf and page 1 of back.pdf side-by-side",
    },
]

# Basic paper sizes map (fallback if a full database isn't available)
_PAPER_SIZES = {
    "a4": (595.28, 841.89),
    "letter": (612.0, 792.0),
    "legal": (612.0, 1008.0),
    "a3": (841.89, 1190.55),
    "a5": (419.53, 595.28),
}


@register_operation(
    "montage",
    tags=["from_scratch", "imposition", "geometry"],
    desc="Impose pages onto a grid layout",
    usage="<input>... montage <spec>... output <file>",
    long_desc=_MONTAGE_LONG_DESC,
    examples=_MONTAGE_EXAMPLES,
    args=(
        [c.INPUTS, c.OPERATION_ARGS, c.OPENED_PDFS],
        {c.ALIASES: c.ALIASES},
    ),
    # grammar_spec="token+", # Accepts a mix of page specs and config tokens
)
def montage_pages(inputs, specs, opened_pdfs, aliases=None) -> OpResult:
    """
    Imposes pages onto new canvas pages based on a grid or layout strategy.
    """
    import pikepdf

    new_pdf = pikepdf.new()

    # 1. Separate Page Selectors from Config Arguments
    page_specs = []
    config = _parse_montage_config(specs, page_specs)

    # 2. Resolve Source Pages
    # If no page specs were provided, default to "all pages from all inputs"
    # (This matches standard pdftl/pdftk behavior when omitted)
    if not page_specs:
        # Construct a default spec for all inputs if implicit behavior is desired,
        # otherwise we assume the user might have passed inputs positionally.
        # For simplicity, we assume expand_specs handles the empty case or we rely
        # on specific inputs.
        pass

    source_pages_to_process = expand_specs_to_pages(page_specs, aliases, inputs, opened_pdfs)

    if not source_pages_to_process:
        # Fallback: if user didn't specify "1-5", use all pages from first input
        # This mirrors typical behavior if inputs are provided but no ranges.
        if inputs:
            # Re-run expand with a default "take everything" approach if possible,
            # or manual fallback. For now, raise if empty.
            raise ValueError("No source pages selected for montage.")

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
        source_pages=[p.page for p in source_pages_to_process],
        strategy=layout_strategy,
        canvas_size=config["canvas_size"],
        preserve_aspect_ratio=config["preserve_aspect_ratio"],
    )

    return OpResult(success=True, pdf=new_pdf)


def _parse_montage_config(specs: List[str], out_page_specs: List[str]) -> Dict[str, Any]:
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
        "canvas_size": _PAPER_SIZES["a4"],  # Default A4 Portrait
        "preserve_aspect_ratio": True,
    }

    for token in specs:
        if "=" in token:
            key, val = token.split("=", 1)
            key = key.lower().strip()
            val = val.lower().strip()

            if key == "grid":
                # Parse "2x2"
                if "x" in val:
                    c, r = val.split("x")
                    config["cols"] = int(c)
                    config["rows"] = int(r)
            elif key == "canvas":
                # Parse "A4", "Letter"
                if val in _PAPER_SIZES:
                    config["canvas_size"] = _PAPER_SIZES[val]
                # Future: Support "500x500" custom size parsing
            elif key == "margin":
                config["margin"] = float(val)
            elif key == "gutter":
                config["gutter"] = float(val)
            elif key == "cols":
                config["cols"] = int(val)
            elif key == "rows":
                config["rows"] = int(val)
            elif key == "fit":
                config["preserve_aspect_ratio"] = val != "fill"
        else:
            # Assume it's a page spec (e.g. "1-5", "even", "A")
            out_page_specs.append(token)

    return config


def _apply_montage_logic(
    target_pdf: "Pdf",
    source_pages: List["Page"],
    strategy: GridLayout,
    canvas_size: Tuple[float, float],
    preserve_aspect_ratio: bool,
):
    """
    The engine that combines Layout, Geometry, and Pikepdf to build the document.
    """
    target_w, target_h = canvas_size

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
        box = src_page.trimbox if src_page.trimbox else src_page.mediabox
        src_w = float(box[2] - box[0])
        src_h = float(box[3] - box[1])

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
            rotate=0,
            anchor_source="bottom-left",  # fit_metrics assumes simple bottom-left box match
            anchor_target="bottom-left",
        )

        # D. Stamp (Form XObject)
        form_xobj = target_pdf.copy_foreign(src_page).as_form_xobject()
        form_name = target_page.resources.add_xobject(target_pdf, form_xobj)

        # Apply Matrix (a, b, c, d, e, f)
        m = matrix
        cmd = f"q {m[0]:.6f} {m[1]:.6f} {m[2]:.6f} {m[3]:.6f} {m[4]:.6f} {m[5]:.6f} cm /{form_name} Do Q "
        target_page.contents_add(cmd.encode("ascii"))
