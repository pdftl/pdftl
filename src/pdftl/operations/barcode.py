# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/barcode.py

"""Generate and overlay barcodes onto PDF pages."""

import io
import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import pikepdf
    from PIL import Image

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import OperationError
from pdftl.utils.ocg import create_layer
from pdftl.utils.barcode_utils import generate_barcode
from pdftl.utils.dimensions import dim_str_to_pts, get_visible_page_dimensions
from pdftl.utils.text_templates import build_static_context, build_page_context
from pdftl.operations.parsers.barcode_parser import parse_barcode_specs_to_rules


logger = logging.getLogger(__name__)

_BARCODE_LONG_DESC = """
The `barcode` operation generates a barcode (e.g., QR code, DataMatrix, Code128) from specified
text or templates and overlays it onto the input document.

### Syntax
`[page_range]!<data>![(options)]`

* **page_range:** Standard page selection (e.g., `1-5`, `even`, `1-end`). Defaults to all pages
  if omitted.
* **data:** The string to encode into the barcode. Supports dynamic template variables like `{n}`
  (page number within the sequence) and `{page}` (absolute page number).
* **options:** A comma-separated list of `key=value` pairs enclosed in parentheses.

### Positioning & Layout
The barcode operation uses a strictly Cartesian coordinate system, matching the `add_text`
operation.
* **position:** Use preset anchors (`top-left`, `top-right`, `bottom-center`, `mid-center`,
  etc.). These place the barcode **exactly flush** against the visible page boundaries.
* **offset-x / offset-y:** Cartesian shifts applied after the base position. Use these to add
  inward margins. For example, if positioned `top-right`, use `offset-x=-36pt` to push the
  barcode left, and `offset-y=-36pt` to push it down.
* **x / y:** Absolute coordinates measured from the bottom-left corner of the page. Mutually
  exclusive with `position`.
* **width:** The physical dimension of the rendered barcode (e.g., `72pt`, `1.5in`). The height
  is calculated automatically to preserve the exact aspect ratio of the generated matrix.

### Barcode Options
* **format:** The type of barcode to generate (default: `QRCode`).
* **scale:** Internal rendering resolution multiplier to ensure the generated matrix is crisp
  before scaling it to the physical `width` (default: 10).
"""


def _parse_barcode_args(operation_args: list[str]) -> tuple[list[str], str | None]:
    """Parse operation_args into (specs, layer_name)."""
    specs: list[str] = []
    layer_name: str | None = None

    if not operation_args:
        return specs, layer_name

    it = iter(operation_args)
    for arg in it:
        if arg == "layer_name":
            try:
                layer_name = next(it)
            except StopIteration as exc:
                raise OperationError("The 'layer_name' option requires a value.") from exc
            continue
        specs.append(arg)

    return specs, layer_name


def _stamp_image_on_page(
    input_pdf: "pikepdf.Pdf",
    page: "pikepdf.Page",
    pil_image: "Image.Image",
    phys_x: float,
    phys_y: float,
    phys_w: float,
    phys_h: float,
    ocg: Any = None,
) -> None:
    """Encodes a PIL image to a temporary PDF and stamps it onto the target page."""
    import pikepdf

    pdf_buffer = io.BytesIO()

    if pil_image.mode not in ("1", "L", "RGB", "CMYK"):
        pil_image = pil_image.convert("RGB")

    pil_image.save(pdf_buffer, format="PDF")
    pdf_buffer.seek(0)

    img_pdf = pikepdf.Pdf.open(pdf_buffer)
    img_page = img_pdf.pages[0]

    if not hasattr(input_pdf, "_image_cache"):
        input_pdf._image_cache = []
    input_pdf._image_cache.append(img_pdf)

    rect = pikepdf.Rectangle(phys_x, phys_y, phys_x + phys_w, phys_y + phys_h)

    old_xobjs = (
        set(page.Resources.XObject.keys())
        if "/Resources" in page and "/XObject" in page.Resources
        else set()
    )

    page.add_overlay(img_page, rect)

    if ocg and "/Resources" in page and "/XObject" in page.Resources:
        new_keys = set(page.Resources.XObject.keys()) - old_xobjs
        for key in new_keys:
            page.Resources.XObject[key].OC = ocg


def _get_preset_x(pos: str, page_width: float) -> float:
    """Calculates the exact edge X coordinate of the anchor point."""
    if "left" in pos:
        return 0.0
    if "right" in pos:
        return page_width
    if "center" in pos:
        return page_width / 2.0
    return 0.0


def _get_preset_y(pos: str, page_height: float) -> float:
    """Calculates the exact edge Y coordinate of the anchor point."""
    if "top" in pos:
        return page_height
    if "bottom" in pos:
        return 0.0
    if "mid" in pos:
        return page_height / 2.0
    return 0.0


def _get_anchor_coordinates(rule: dict, page_w: float, page_h: float) -> tuple[float, float, str]:
    """Calculates base anchor coordinates, mimicking add_text behavior."""
    pos = rule.get("position") or ""

    if pos:
        anchor_x = _get_preset_x(pos, page_w)
        anchor_y = _get_preset_y(pos, page_h)
        return anchor_x, anchor_y, pos

    anchor_x = dim_str_to_pts(str(rule.get("x", "0pt")), total_dimension=page_w, axis="width")
    anchor_y = dim_str_to_pts(str(rule.get("y", "0pt")), total_dimension=page_h, axis="height")
    return anchor_x, anchor_y, ""


def _calculate_alignment_geometry(
    rule: dict, pil_image: "Image.Image", page_w: float, page_h: float
) -> tuple[float, float, float, float]:
    """Computes layout-boundary shift offsets while keeping the matrix aspect ratio exact."""
    resolved_w = dim_str_to_pts(
        str(rule.get("width", "72pt")), total_dimension=page_w, axis="width"
    )
    aspect_ratio = pil_image.height / pil_image.width
    resolved_h = resolved_w * aspect_ratio
    pos = rule.get("position") or ""
    draw_x = -resolved_w if "right" in pos else (-resolved_w / 2 if "center" in pos else 0.0)
    draw_y = -resolved_h if "top" in pos else (-resolved_h / 2 if "mid" in pos else 0.0)
    return draw_x, draw_y, resolved_w, resolved_h


def _process_single_rule(
    input_pdf: "pikepdf.Pdf",
    page: "pikepdf.Page",
    rule: dict,
    page_context: dict,
    raw_dims: tuple[float, float, float, float],
    rotation: int,
    ocg: Any,
) -> None:
    """Runs a single rule operation inside isolated execution boundaries."""
    import pikepdf
    from PIL import Image

    x0, y0, w_phys, h_phys = raw_dims

    # 1. Update transient sequence counters
    page_context["count"] = rule.get("count", rule.get("n", 1))
    page_context["n"] = rule.get("n", 1)

    # 2. Extract context evaluation strings
    runs = rule["text_renderer"](page_context)
    text_content = "".join(text for text, _ in runs)
    if not text_content:
        return

    # 3. Guard matrix asset rendering
    try:
        pil_image = generate_barcode(
            text=text_content,
            format_name=rule.get("format", "QRCode"),
            scale=rule.get("scale", 10),
        )
    except (ValueError, KeyError, OSError) as exc:
        raise OperationError(f"Barcode image matrix layout generation failed: {exc}") from exc

    # 4. Pure linear calculations mapped against native rotation
    w_vis, h_vis = (h_phys, w_phys) if rotation in (90, 270) else (w_phys, h_phys)

    anchor_x, anchor_y, pos = _get_anchor_coordinates(rule, w_vis, h_vis)
    offset_x = dim_str_to_pts(str(rule.get("offset-x", "0")), total_dimension=w_vis, axis="width")
    offset_y = dim_str_to_pts(str(rule.get("offset-y", "0")), total_dimension=h_vis, axis="height")

    rule_ctx = {**rule, "position": pos} if pos else rule
    draw_x, draw_y, resolved_w, resolved_h = _calculate_alignment_geometry(
        rule_ctx, pil_image, w_vis, h_vis
    )

    vis_x = anchor_x + offset_x + draw_x
    vis_y = anchor_y + offset_y + draw_y

    if rotation == 90:
        phys_x = x0 + w_phys - vis_y - resolved_h
        phys_y = y0 + vis_x
        phys_w, phys_h = resolved_h, resolved_w
        pil_image = pil_image.transpose(Image.Transpose.ROTATE_90)
    elif rotation == 180:
        phys_x = x0 + w_phys - vis_x - resolved_w
        phys_y = y0 + h_phys - vis_y - resolved_h
        phys_w, phys_h = resolved_w, resolved_h
        pil_image = pil_image.transpose(Image.Transpose.ROTATE_180)
    elif rotation == 270:
        phys_x = x0 + vis_y
        phys_y = y0 + h_phys - vis_x - resolved_w
        phys_w, phys_h = resolved_h, resolved_w
        pil_image = pil_image.transpose(Image.Transpose.ROTATE_270)
    else:
        phys_x = x0 + vis_x
        phys_y = y0 + vis_y
        phys_w, phys_h = resolved_w, resolved_h

    # 5. Guard drawing injection pipeline
    try:
        _stamp_image_on_page(input_pdf, page, pil_image, phys_x, phys_y, phys_w, phys_h, ocg)
    except pikepdf.PdfError as exc:
        raise OperationError(f"PDF canvas overlay stream assembly failed: {exc}") from exc


@register_operation(
    name="barcode",
    desc="Generate and add a barcode to pages",
    usage="<input> barcode <specs>... [layer_name <name>] output <file>",
    long_desc=_BARCODE_LONG_DESC,
    examples=[
        {
            "cmd": "in.pdf barcode '!https://github.com/pdftl/pdftl!' output out.pdf",
            "desc": "Basic barcode",
        },
    ],
    tags=["in_place", "overlay", "layer", "barcode"],
    type="single input operation",
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}, {}),
)
def barcode_pdf(input_pdf: "pikepdf.Pdf", operation_args: list[str]) -> OpResult:
    """Main entry point for the barcode operation."""
    specs, layer_name = _parse_barcode_args(operation_args)
    ocg = create_layer(input_pdf, layer_name) if layer_name else None

    total_pages = len(input_pdf.pages)
    page_rules = parse_barcode_specs_to_rules(specs, total_pages)

    logger.info("Evaluating barcode rules across %d page(s)...", total_pages)
    static_context = build_static_context(input_pdf)

    for page_idx, page in enumerate(input_pdf.pages):
        rules = page_rules.get(page_idx, [])
        if not rules:
            continue

        # Pull raw bounding coordinates without native rotation applied, we will apply it
        dims = get_visible_page_dimensions(page, box="cropbox", apply_rotate=False)
        raw_dims = dims if dims is not None else (0.0, 0.0, 612.0, 792.0)
        rotation = int(page.get("/Rotate", 0)) % 360

        page_context = build_page_context(static_context, page, page_idx + 1)

        for rule in rules:
            _process_single_rule(input_pdf, page, rule, page_context, raw_dims, rotation, ocg)

    return OpResult(success=True, pdf=input_pdf)
