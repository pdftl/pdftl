# src/pdftl/operations/zoom.py

"""Rescale entire pages to fit target dimensions or paper sizes."""

from typing import TYPE_CHECKING
import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult, HelpExample
from pdftl.utils.page_specs import page_numbers_matching_page_spec
from pdftl.utils.dimensions import dim_str_to_pts, get_visible_page_dimensions
from pdftl.utils.page_specs.types import PageTransform
from pdftl.utils.keyval_parser import parse_keyval_string
from pdftl.exceptions import InvalidArgumentError

if TYPE_CHECKING:
    from pikepdf import Pdf

_ZOOM_LONG_DESC = """
The `zoom` operation rescales entire pages (including the MediaBox)
to fit a specified target dimension. Unlike `place`, which only moves content
within existing boundaries, `zoom` physically transforms the page size.

This is an **in-place** operation: unspecified pages are left unchanged.

**Syntax:**
  `zoom "<pages>(<target>[,<options>])"`

**Target Formats:**
  * **Relative/Percentage:** `(50%)` or `(200%)`. Resizes the page relative
    to its current dimensions.
  * **Single Value/Paper:** `(A4)` or `(100mm)`. Scales the page uniformly
    so that it fits inside a bounding box of that size (aspect ratio preserved).
  * **Explicit Box:** `(100mm,200mm)`. Scales the page uniformly to fit
    inside the specified width and height.
  * **Axis Specific:** `(width=A4)` or `(height=11in)`. Scales the page
    proportionally based only on the specified dimension.

**Options:**
  * `shrink`: Only scale pages down. If the page is already smaller than
    the target, it remains unchanged.
  * `grow`: Only scale pages up. If the page is already larger than the
    target, it remains unchanged.

**Note:** Scaling is always uniform. If a target rectangle is provided, the
operation uses the limiting dimension to ensure the entire page fits inside
the "envelope".
"""

_ZOOM_EXAMPLES = [
    HelpExample(
        desc="Shrink only the first 3 pages to 50% of their size",
        cmd="in.pdf zoom '1-3(50%)' output out.pdf",
    ),
    HelpExample(
        desc="Scale the whole document to A4 width", cmd="in.pdf zoom '(width=A4)' output out.pdf"
    ),
    HelpExample(
        desc="Ensure all pages fit within a 10x10 inch box, but only if they are larger",
        cmd="in.pdf zoom '(10in,shrink)' output out.pdf",
    ),
    HelpExample(
        desc="Mixed operation: First page to A4, others to 80%",
        cmd="in.pdf zoom '1(A4)' '2-end(80%)' output out.pdf",
    ),
]


@register_operation(
    "zoom",
    tags=["in_place", "geometry"],
    desc="Rescale entire pages",
    usage="<input> zoom <spec>... output <file>",
    examples=_ZOOM_EXAMPLES,
    long_desc=_ZOOM_LONG_DESC,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def zoom_pages(source_pdf: "Pdf", zoom_specs: list) -> OpResult:
    total_pages = len(source_pdf.pages)
    all_transforms = []

    for spec in zoom_specs:
        if "(" not in spec or not spec.endswith(")"):
            raise InvalidArgumentError(f"Invalid zoom spec: '{spec}'. Expected 'range(params)'")

        range_part, params_raw = spec.rstrip(")").split("(", 1)
        bare_tokens = []
        params = parse_keyval_string(params_raw, bare_tokens=bare_tokens, context="zoom")

        target_indices = page_numbers_matching_page_spec(range_part, total_pages)

        for p_num in target_indices:
            idx = p_num - 1
            page = source_pdf.pages[idx]
            vis_dims = get_visible_page_dimensions(page, apply_rotate=True)

            if vis_dims:
                _, _, vw, vh = vis_dims
                factor = _calculate_zoom_factor(vw, vh, params, bare_tokens)

                all_transforms.append(
                    PageTransform(pdf=source_pdf, index=idx, rotation=(0, False), scale=factor)
                )

    # Apply scaling in-place
    for pt in all_transforms:
        from pdftl.utils.scale import apply_scaling

        apply_scaling(source_pdf.pages[pt.index], pt.scale)

    return OpResult(success=True, pdf=source_pdf)


def _calculate_zoom_factor(vw, vh, params, bare):
    target_w, target_h = None, None

    if "width" in params:
        target_w = dim_str_to_pts(params["width"], vw, axis="width")
    if "height" in params:
        target_h = dim_str_to_pts(params["height"], vh, axis="height")

    dim_tokens = [t for t in bare if t.lower() not in ("shrink", "grow")]
    if target_w is None and target_h is None and dim_tokens:
        try:
            tw_str = dim_tokens[0]
            target_w = dim_str_to_pts(tw_str, vw, axis="width")
            th_str = dim_tokens[1] if len(dim_tokens) > 1 else tw_str
            target_h = dim_str_to_pts(th_str, vh, axis="height")
        except (InvalidArgumentError, ValueError):
            pass

    ratios = []
    if target_w is not None:
        ratios.append(target_w / vw)
    if target_h is not None:
        ratios.append(target_h / vh)

    s = min(ratios) if ratios else 1.0

    if any(t.lower() == "shrink" for t in bare) and s > 1.0:
        s = 1.0
    if any(t.lower() == "grow" for t in bare) and s < 1.0:
        s = 1.0

    return s
