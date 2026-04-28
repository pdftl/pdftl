# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/overlay.py

"""Apply an overlay or underlay of page(s) from one PDF file to another"""

import logging
from typing import TYPE_CHECKING

from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.exceptions import OperationError
from pdftl.utils.io_helpers import smart_pikepdf_open
from pdftl.utils.ocg import create_layer
import pdftl.core.constants as c

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)

_LAYER_CAVEAT = (
    "Note: If the overlay/background PDF contains its own internal layers (OCGs), "
    "they will be flattened and merged into the page content. Individual "
    "toggleability of the source's internal layers is not preserved."
)

_STAMP_LONG_DESC = f"""
The `stamp` operation overlays the first page of the stamp PDF onto every page
of the input document.

{_LAYER_CAVEAT}
"""

_MULTISTAMP_LONG_DESC = f"""
Overlay pages from the stamp PDF onto the corresponding pages of the input PDF:
page 1 of the stamp is overlaid onto page 1 of the input, etc.

{_LAYER_CAVEAT}
"""

_BACKGROUND_LONG_DESC = f"""
The `background` operation places the first page of the background PDF
underneath every page of the input document.

{_LAYER_CAVEAT}
"""

_MULTIBACKGROUND_LONG_DESC = f"""
Underlay pages from the background PDF behind the corresponding pages
in the input PDF: page 1 is placed behind page 1 of the input, etc.

{_LAYER_CAVEAT}
"""


def _register_overlay_op(name, desc, long_desc, examples):
    stamp_input_prefix = "stamp" if "stamp" in name else "background"
    return register_operation(
        name=name,
        desc=desc,
        usage=f"<input> {name} <{stamp_input_prefix}_pdf> [layer_name <name>] output <file>",
        long_desc=long_desc,
        examples=examples,
        tags=["in_place", "overlay", "layer"],
        type="single input operation",
        args=(
            [c.INPUT_PDF, c.OVERLAY_PDF, c.OPERATION_ARGS],
            {},
            {c.MULTI: "multi" in name, c.ON_TOP: "stamp" in name},
        ),
    )


@_register_overlay_op(
    "stamp",
    desc="Stamp a 1-page PDF onto each page",
    long_desc=_STAMP_LONG_DESC,
    examples=[
        {"cmd": "in.pdf stamp mark.pdf output out.pdf", "desc": "Basic stamp"},
        {
            "cmd": "in.pdf stamp mark.pdf layer_name 'Draft' output out.pdf",
            "desc": "Stamp onto a named layer",
        },
    ],
)
@_register_overlay_op(
    "multistamp",
    desc="Stamp multiple pages onto an input PDF",
    long_desc=_MULTISTAMP_LONG_DESC,
    examples=[{"cmd": "in.pdf multistamp overlay.pdf output out.pdf", "desc": "Multi-page stamp"}],
)
@_register_overlay_op(
    "background",
    desc="Use a 1-page PDF as the background",
    long_desc=_BACKGROUND_LONG_DESC,
    examples=[{"cmd": "in.pdf background letter.pdf output out.pdf", "desc": "Apply background"}],
)
@_register_overlay_op(
    "multibackground",
    desc="Use multiple pages as backgrounds",
    long_desc=_MULTIBACKGROUND_LONG_DESC,
    examples=[
        {"cmd": "in.pdf multibackground bgs.pdf output out.pdf", "desc": "Multi-page background"}
    ],
)
def apply_overlay(
    input_pdf: "pikepdf.Pdf",
    overlay_filename: str,
    operation_args: list[str],
    on_top: bool = True,
    multi: bool = False,
    scale_to_fit: bool = True,
) -> OpResult:
    """Apply overlay or underlay with optional OCG layering."""
    import pikepdf

    layer_name = _get_layer_name(operation_args)
    source = None if overlay_filename == "-" else overlay_filename
    with smart_pikepdf_open(source) as overlay_pdf:
        if not overlay_pdf.pages:
            raise OperationError(f"Overlay file '{overlay_filename}' is empty.")

        ocg = create_layer(input_pdf, layer_name) if layer_name else None

        for i, base_page in enumerate(input_pdf.pages):
            _process_page(i, base_page, overlay_pdf, pikepdf, scale_to_fit, on_top, multi, ocg)

    return OpResult(success=True, pdf=input_pdf)


def _get_layer_name(operation_args):
    """Parse layer_name from operation_args."""
    layer_name: str | None = None
    if operation_args:
        it = iter(operation_args)
        for arg in it:
            if arg == "layer_name":
                try:
                    layer_name = next(it)
                except StopIteration:
                    raise OperationError("The 'layer_name' option requires a value.")
    return layer_name


def _process_page(i, base_page, overlay_pdf, pikepdf, scale_to_fit, on_top, multi, ocg):
    overlay_idx = min(i, len(overlay_pdf.pages) - 1) if multi else 0
    overlay_page = overlay_pdf.pages[overlay_idx]

    rect = (
        pikepdf.Rectangle(*map(float, base_page.trimbox or base_page.MediaBox))
        if scale_to_fit
        else None
    )

    # Diff-and-Patch Logic
    old_xobjs = (
        set(base_page.Resources.XObject.keys())
        if "/Resources" in base_page and "/XObject" in base_page.Resources
        else set()
    )

    if on_top:
        base_page.add_overlay(overlay_page, rect=rect)
    else:
        base_page.add_underlay(overlay_page, rect=rect)

    if ocg and "/Resources" in base_page and "/XObject" in base_page.Resources:
        new_keys = set(base_page.Resources.XObject.keys()) - old_xobjs
        for key in new_keys:
            base_page.Resources.XObject[key].OC = ocg
