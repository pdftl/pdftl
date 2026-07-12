# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/deskew.py

"""Automatically detect and correct text skew in PDF pages with deep diagnostics."""

import logging
from typing import TYPE_CHECKING, Any

import pdftl.core.constants as c
from pdftl.core.core_types import HelpExample, OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError

if TYPE_CHECKING:
    from pikepdf import Pdf, Matrix

logger = logging.getLogger(__name__)

_DESKEW_LONG_DESC = """
The `deskew` operation automatically detects rotational misalignment (skew)
commonly found in scanned documents and aligns the text horizontally.

### How it works
This operation runs a page-level hybrid vector/raster deskew:
  1. The page is rendered at a low resolution to analyze its layout.
  2. A projection profile variance analysis (Radon-style line search)
     detects the dominant text angle within the specified `max_skew` range.
  3. If the active text region or page size is too small for a high-fidelity line search,
     the page is dynamically re-rendered at a higher resolution (up to 600 DPI) to prevent
     coarse-grain estimation and boundary errors.
  4. The entire page coordinate space is rotated around its visual center
     to correct the alignment.
  5. Vector content, high-resolution image assets, and text rendering remains
     lossless because the coordinate grid itself is rotated.
  6. Clickable hyperlinks, highlights, and annotations are transformed
     automatically to match their new visual locations.

### Parameters
* `max_skew=<val>` (default: 10.0) — The maximum search limit for skew detection,
  in degrees. Restricting this window speeds up analysis and avoids false-positive
  rotations on non-standard layouts.
* `dpi=<val>` (default: 75) — Resolution of the rasterized page used for
  skew analysis. 75 DPI is generally optimal for fast, robust line detection.
* `max_render_dpi=<val>` (default: 600) — Ceiling on the resolution used when
  a small text region is re-rendered for closer analysis. This controls how
  much detail is *captured*; it is independent of `coarse_res`/`fine_res`
  below, which control how much of that captured detail is *analyzed*.
* `coarse_res=<val>` (default: 300) — Target pixel footprint (long edge) used
  for the initial coarse angle search. Lower values are faster but risk
  missing the correct angle's neighborhood on very fine or sparse text.
* `fine_res=<val>` (default: 600) — Target pixel footprint (long edge) used
  for the fine, sub-degree angle search. Higher values improve precision
  (reducing residual jitter of a few tenths of a degree) at the cost of
  slower analysis; lower values trade some precision for speed.
"""

_DESKEW_EXAMPLES = [
    HelpExample(
        desc="Automatically detect and correct skew on all pages.",
        cmd="in.pdf deskew output out.pdf",
    ),
    HelpExample(
        desc="Automatically deskew only the even pages, limiting search to 5 degrees.",
        cmd="in.pdf deskew even max_skew=5 output out.pdf",
    ),
    HelpExample(
        desc="Deskew pages 1 to 3 with a higher analysis resolution of 150 DPI.",
        cmd="in.pdf deskew 1-3 dpi=150 output out.pdf",
    ),
    HelpExample(
        desc="Prioritize speed over precision by using smaller search footprints.",
        cmd="in.pdf deskew coarse_res=150 fine_res=300 output out.pdf",
    ),
]


def _parse_positive_float_arg(parsed: dict, key: str, default: float) -> float:
    """Parses a single positive-float keyword argument, raising a uniform error on failure."""
    if key not in parsed:
        return default
    val_str = parsed[key]
    try:
        value = float(val_str)
        if value <= 0:
            raise ValueError
    except ValueError as exc:
        raise InvalidArgumentError(
            f"'deskew': invalid {key} '{val_str}'. Should be a positive number."
        ) from exc
    return value


def _parse_deskew_args(args: list) -> tuple[list[str], dict[str, float]]:
    """Parses and validates all `deskew` keyword arguments, returning page specs and settings."""
    from pdftl.utils.keyval_parser import parse_keyval_list

    page_specs: list[str] = []
    try:
        parsed = parse_keyval_list(
            args,
            allowed_keys=["dpi", "max_skew", "coarse_res", "fine_res", "max_render_dpi"],
            bare_tokens=page_specs,
        )
    except InvalidArgumentError as exc:
        raise InvalidArgumentError(f"Could not parse `deskew` arguments {args}: {exc}")

    settings = {
        "dpi": _parse_positive_float_arg(parsed, "dpi", 75.0),
        "max_skew": _parse_positive_float_arg(parsed, "max_skew", 10.0),
        "coarse_res": _parse_positive_float_arg(parsed, "coarse_res", 300.0),
        "fine_res": _parse_positive_float_arg(parsed, "fine_res", 600.0),
        "max_render_dpi": _parse_positive_float_arg(parsed, "max_render_dpi", 600.0),
    }
    return page_specs, settings


def _resolve_target_pages(pdf: "Pdf", page_specs: list[str]) -> list[int]:
    """Resolves page specs to a sorted, de-duplicated list of 1-based page numbers."""
    from pdftl.utils.page_specs import page_numbers_matching_page_spec

    if not page_specs:
        page_specs = ["1-end"]

    total_pages = len(pdf.pages)
    target_pages_set: set[int] = set()
    for spec in page_specs:
        target_pages_set.update(page_numbers_matching_page_spec(spec, total_pages))
    return sorted(target_pages_set)


def _detect_angle_for_page(
    pdf: "Pdf", idx: int, pil_img: Any, settings: dict[str, float]
) -> float:
    """Detects the skew angle for a single page, re-rendering a high-res crop if needed."""
    page_pts_w = float(pdf.pages[idx].mediabox[2] - pdf.pages[idx].mediabox[0])
    page_pts_h = float(pdf.pages[idx].mediabox[3] - pdf.pages[idx].mediabox[1])

    angle, revised_dpi, crop_box_pts = determine_skew_angle(
        pil_img,
        settings["max_skew"],
        current_dpi=settings["dpi"],
        return_revised_dpi=True,
        page_pts=(page_pts_w, page_pts_h),
        max_render_dpi=settings["max_render_dpi"],
        coarse_target=settings["coarse_res"],
        fine_target=settings["fine_res"],
    )

    if revised_dpi is None or crop_box_pts is None:
        return angle

    logger.debug(
        "Page %d: Active foreground area is too small for accurate deskew at %.1f DPI. "
        "Re-rendering regional crop to capture more detail (%.1f DPI render)...",
        idx + 1,
        settings["dpi"],
        revised_dpi,
    )
    try:
        from pdftl.utils.page_images import render_page_region_to_pil

        high_res_crop = render_page_region_to_pil(pdf, idx, revised_dpi, crop_box_pts)
        angle, _, _ = determine_skew_angle(
            high_res_crop,
            settings["max_skew"],
            current_dpi=revised_dpi,
            return_revised_dpi=True,
            skip_downscale=True,
            coarse_target=settings["coarse_res"],
            fine_target=settings["fine_res"],
        )
    except (ValueError, TypeError, AttributeError, OSError, RuntimeError) as exc:
        # Fallback to the low-resolution angle if regional rendering fails unexpectedly
        logger.warning(
            "Page %d: Failed to re-render page region at %.1f DPI: %s. "
            "Reverting to low-resolution angle.",
            idx + 1,
            revised_dpi,
            exc,
        )
    return angle


def iter_pages_as_pil(pdf: "Pdf", dpi: float, page_indices: list[int] | None = None):
    """Module-level indirection so tests can patch this name; delegates lazily."""
    from pdftl.utils.page_images import iter_pages_as_pil as _iter_pages_as_pil

    return _iter_pages_as_pil(pdf, dpi, page_indices=page_indices)


def get_visible_page_dimensions(page: Any, apply_rotate: bool = False):
    """Module-level indirection so tests can patch this name; delegates lazily."""
    from pdftl.utils.dimensions import get_visible_page_dimensions as _get_visible_page_dimensions

    return _get_visible_page_dimensions(page, apply_rotate=apply_rotate)


def _apply_deskew_angle(pdf: "Pdf", idx: int, angle: float) -> None:
    """Applies the computed deskew rotation matrix to a single page's content and annotations."""
    import pikepdf

    from pdftl.utils.affix_content import apply_content_matrix
    from pdftl.utils.geometry import update_annotations_for_matrix

    page = pdf.pages[idx]
    matrix = _calculate_deskew_matrix(page, angle)

    if matrix == pikepdf.Matrix():
        return

    logger.info("Page %d: Deskewing by %.2f degrees.", idx + 1, angle)
    logger.debug("Page %d: Applying content matrix.", idx + 1)
    apply_content_matrix(page, matrix)
    logger.debug(
        "Page %d: Content contains annotations, re-transforming boundaries if present.", idx + 1
    )
    update_annotations_for_matrix(page, matrix)


@register_operation(
    "deskew",
    tags=["content_modification", "geometry"],
    type="single input operation",
    desc="Automatically detect and correct document skew",
    long_desc=_DESKEW_LONG_DESC,
    usage="<input> deskew [<page_specs>...] [max_skew=<val>] [dpi=<val>] output <file>",
    examples=_DESKEW_EXAMPLES,
    args=(
        [c.INPUT_PDF, c.OPERATION_ARGS],
        {},
    ),
)
def deskew_pages(pdf: "Pdf", args: list) -> OpResult:
    """CLI Adapter for `deskew`: Parses arguments, detects text skew, and rotates pages."""
    from pdftl.utils.dependencies import ensure_dependencies

    ensure_dependencies("deskew", ["pypdfium2", "PIL", "numpy"], "render")

    page_specs, settings = _parse_deskew_args(args)
    target_pages = _resolve_target_pages(pdf, page_specs)
    if not target_pages:
        return OpResult(success=True, pdf=pdf)

    logger.debug("Starting page render loop for deskewing: %s pages", len(target_pages))
    page_indices = [p - 1 for p in target_pages]
    page_iterator = iter_pages_as_pil(pdf, settings["dpi"], page_indices=page_indices)

    angles_to_apply = {}
    for idx, pil_img in page_iterator:
        logger.debug("--- [Page %d Diagnostics] ---", idx + 1)
        angle = _detect_angle_for_page(pdf, idx, pil_img, settings)

        if abs(angle) > 0.05:
            angles_to_apply[idx] = angle
        else:
            logger.debug(
                "Page %d: No significant skew detected (%.2f degrees). Skipping.", idx + 1, angle
            )

    for idx, angle in angles_to_apply.items():
        _apply_deskew_angle(pdf, idx, angle)

    return OpResult(success=True, pdf=pdf)


def _binarize_page_image(pil_img: Any, skip_downscale: bool) -> tuple[Any, float]:
    """Downscales (if needed) and binarizes a page image against its background color.

    Returns (binarized PIL image, scale factor applied). Scale is 1.0 if no
    downscaling occurred.
    """
    import numpy as np
    from PIL import Image

    logger.debug(
        "Image incoming size: w=%d, h=%d, mode=%s", pil_img.width, pil_img.height, pil_img.mode
    )

    target_size = 1200
    scale = 1.0
    if not skip_downscale and (pil_img.width > target_size or pil_img.height > target_size):
        scale = target_size / max(pil_img.width, pil_img.height)
        new_w, new_h = int(pil_img.width * scale), int(pil_img.height * scale)
        logger.debug("Resizing down to target footprint: w=%d, h=%d", new_w, new_h)
        img_small = pil_img.resize((new_w, new_h), resample=Image.Resampling.BILINEAR)
    else:
        img_small = pil_img.copy()

    img_gray = img_small.convert("L")
    arr_gray = np.array(img_gray)

    bg_color = float(np.median(arr_gray))
    logger.debug("Grayscale conversion complete. Median background color value = %.2f", bg_color)

    # Threshold with dynamic contrast window
    if bg_color > 127:
        arr_bin = (arr_gray < (bg_color - 30)).astype(np.uint8) * 255
    else:
        arr_bin = (arr_gray > (bg_color + 30)).astype(np.uint8) * 255

    logger.debug("Binarization complete. Active foreground density = %d px", int(np.sum(arr_bin)))
    bin_img = Image.fromarray(arr_bin)
    return bin_img, scale


def _crop_margins_to_pdf_points(
    padded_bbox: tuple[int, int, int, int],
    img_size: tuple[int, int],
    page_pts: tuple[float, float],
) -> tuple[float, float, float, float]:
    """Converts a pixel-space padded bbox into pypdfium2 crop margins, in PDF points.

    pypdfium2's `crop` parameter is margins to trim from each side —
    (left, bottom, right, top) — NOT an absolute bounding box.
    """
    w, h = img_size
    pts_w, pts_h = page_pts

    left_margin = padded_bbox[0] * (pts_w / w)
    right_margin = (w - padded_bbox[2]) * (pts_w / w)
    top_margin = padded_bbox[1] * (pts_h / h)
    bottom_margin = (h - padded_bbox[3]) * (pts_h / h)
    return left_margin, bottom_margin, right_margin, top_margin


def _compute_padded_crop(
    bin_img: Any, bbox: tuple[int, int, int, int], max_skew: float
) -> tuple[Any, tuple[int, int, int, int]]:
    """Crops the binarized image around its foreground bbox with rotation-safe padding.

    Padding is calculated as (diagonal * sin(max_skew) + margin) so that
    rotating by up to max_skew degrees never clips foreground pixels.
    """
    import math

    w, h = bin_img.size
    w_box = bbox[2] - bbox[0]
    h_box = bbox[3] - bbox[1]

    diag = math.hypot(w_box, h_box)
    rot_pad = int(diag * math.sin(math.radians(max_skew))) + 15
    pad = max(20, rot_pad)

    padded_bbox = (
        max(0, bbox[0] - pad),
        max(0, bbox[1] - pad),
        min(w, bbox[2] + pad),
        min(h, bbox[3] + pad),
    )
    logger.debug("Applying crop padding (+%d px): cropped bbox = %s", pad, padded_bbox)
    return bin_img.crop(padded_bbox), padded_bbox


def _maybe_request_higher_res(
    max_dim: float, current_dpi: float, max_render_dpi: float
) -> float | None:
    """Returns a revised DPI to re-render at if the foreground area is too small, else None."""
    if not (max_dim < 600 and current_dpi < 450.0):
        return None

    target_dim = 1000.0
    ideal_dpi = current_dpi * (target_dim / max_dim)
    revised_dpi = min(max_render_dpi, ideal_dpi)
    if revised_dpi > current_dpi * 1.2:
        return revised_dpi
    return None


def _foreground_too_small_for_analysis(w_pt: float, h_pt: float) -> bool:
    """Guardrail: rejects near-degenerate foreground blobs (dust, a lone digit).

    A single line of body text can be well under an inch wide and only a
    few points tall, so these thresholds must stay small — this is not
    meant to reject small-but-legitimate text blocks.
    """
    if w_pt < 15.0 or h_pt < 6.0:
        logger.debug(
            "Active foreground physical area (w_pt=%.1f, h_pt=%.1f) is too small "
            "for a reliable skew analysis. Skipping deskew.",
            w_pt,
            h_pt,
        )
        return True
    return False


def _analyze_foreground_region(
    bin_img: Any,
    bbox: tuple[int, int, int, int],
    max_skew: float,
    current_dpi: float,
    scale: float,
    max_render_dpi: float,
    return_revised_dpi: bool,
    page_pts: tuple[float, float] | None,
) -> tuple[Any, float | None, tuple[float, float, float, float] | None] | None:
    """Handles crop padding, re-render decisions, and the minimum-size guardrail.

    Returns None if the caller should proceed straight to angle search using
    the full (uncropped) image. Otherwise returns a 3-tuple:
      (cropped image, revised_dpi_or_None, crop_box_pts_or_None)
    A non-None revised_dpi signals the caller must re-render and retry rather
    than proceeding to angle search on this pass.
    """
    w, h = bin_img.size
    w_box = bbox[2] - bbox[0]
    h_box = bbox[3] - bbox[1]

    w_box_full = w_box / scale
    h_box_full = h_box / scale
    max_dim = max(w_box_full, h_box_full)

    w_pt = w_box_full * (72.0 / current_dpi)
    h_pt = h_box_full * (72.0 / current_dpi)

    bin_img_cropped, padded_bbox = _compute_padded_crop(bin_img, bbox, max_skew)

    if page_pts is not None:
        pts_w, pts_h = page_pts
    else:
        pts_w = w * (72.0 / (current_dpi * scale))
        pts_h = h * (72.0 / (current_dpi * scale))
    crop_box_pts = _crop_margins_to_pdf_points(padded_bbox, (w, h), (pts_w, pts_h))

    if return_revised_dpi:
        revised_dpi = _maybe_request_higher_res(max_dim, current_dpi, max_render_dpi)
        if revised_dpi is not None:
            logger.debug(
                "Active foreground area (w=%d, h=%d) is too small at %.1f DPI. "
                "Requesting re-render at %.1f DPI.",
                int(w_box_full),
                int(h_box_full),
                current_dpi,
                revised_dpi,
            )
            return bin_img_cropped, revised_dpi, crop_box_pts

    if _foreground_too_small_for_analysis(w_pt, h_pt):
        return bin_img_cropped, "SKIP", None  # sentinel: caller returns 0.0

    return bin_img_cropped, None, crop_box_pts


def _downscale_for_search(img: Any, target: float, resample) -> Any:
    """Downscales an image so its long edge is at most `target` pixels, if larger."""
    w, h = img.size
    if max(w, h) <= target:
        return img
    scale = target / float(max(w, h))
    return img.resize((max(1, int(w * scale)), max(1, int(h * scale))), resample=resample)


def _coarse_angle_search(bin_img_cropped: Any, max_skew: float, coarse_target: float) -> float:
    """Runs a 1-degree-step search over [-max_skew, max_skew] on a downscaled image.

    Downscaling here is safe because we only need the rough angle
    neighborhood — full resolution is wasted work at this stage and
    dominates runtime on large high-DPI crops.
    """
    import numpy as np
    from PIL import Image

    coarse_img = _downscale_for_search(bin_img_cropped, coarse_target, Image.Resampling.NEAREST)

    best_angle = 0.0
    max_variance = -1.0
    logger.debug(
        "Initiating coarse angle search... range=[-%.1f, %.1f] (1.0 deg steps)", max_skew, max_skew
    )
    for angle in np.arange(-max_skew, max_skew + 1.0, 1.0):
        rotated = coarse_img.rotate(float(angle), resample=Image.Resampling.NEAREST)
        variance = float(np.var(np.sum(np.array(rotated), axis=1)))
        logger.debug("Coarse Run: angle = %6.1f, variance = %12.2f", angle, variance)
        if variance > max_variance:
            max_variance = variance
            best_angle = float(angle)

    logger.debug(
        "Coarse search peak identified: angle = %.2f degrees (variance = %.2f)",
        best_angle,
        max_variance,
    )
    return best_angle


def _fine_angle_search(
    bin_img_cropped: Any, coarse_best_angle: float, fine_target: float
) -> float:
    """Refines the coarse angle with a 0.1-degree-step search over a +/-1 degree window.

    A moderate downscale keeps precision while cutting the per-rotation
    cost, since BILINEAR rotation on the full-res crop otherwise dominates
    runtime even after the coarse pass is downscaled.
    """
    import numpy as np
    from PIL import Image

    fine_img = _downscale_for_search(bin_img_cropped, fine_target, Image.Resampling.BILINEAR)

    best_fine_angle = coarse_best_angle
    max_fine_variance = -1.0
    logger.debug(
        "Initiating fine bilinear search... range=[%.1f, %.1f] (0.1 deg steps)",
        coarse_best_angle - 1.0,
        coarse_best_angle + 1.0,
    )
    for angle in np.arange(coarse_best_angle - 1.0, coarse_best_angle + 1.0, 0.1):
        rotated = fine_img.rotate(float(angle), resample=Image.Resampling.BILINEAR)
        variance = float(np.var(np.sum(np.array(rotated), axis=1)))
        logger.debug("Fine Run:   angle = %6.2f, variance = %12.2f", angle, variance)
        if variance > max_fine_variance:
            max_fine_variance = variance
            best_fine_angle = float(angle)

    logger.debug("Fine search complete: peak angle = %.2f degrees", best_fine_angle)
    return best_fine_angle


def determine_skew_angle(
    pil_img: Any,
    max_skew: float,
    current_dpi: float = 75.0,
    return_revised_dpi: bool = False,
    page_pts: tuple[float, float] | None = None,
    skip_downscale: bool = False,
    max_render_dpi: float = 600.0,
    coarse_target: float = 300.0,
    fine_target: float = 600.0,
) -> Any:
    """Determines the text skew angle using horizontal projection profile variance."""

    def _result(angle: float, revised_dpi=None, crop_box_pts=None):
        if return_revised_dpi:
            return angle, revised_dpi, crop_box_pts
        return angle

    bin_img, scale = _binarize_page_image(pil_img, skip_downscale)

    bbox = bin_img.getbbox()
    logger.debug("Raw active foreground bounding box: %s", bbox)
    if bbox is None:
        logger.debug("Page appears blank or holds no detectable text. Returning 0.0 degrees.")
        return _result(0.0)

    analysis = _analyze_foreground_region(
        bin_img,
        bbox,
        max_skew,
        current_dpi,
        scale,
        max_render_dpi,
        return_revised_dpi,
        page_pts,
    )
    bin_img_cropped, revised_dpi_or_sentinel, crop_box_pts = analysis

    if revised_dpi_or_sentinel == "SKIP":
        return _result(0.0)
    if revised_dpi_or_sentinel is not None:
        return _result(0.0, revised_dpi_or_sentinel, crop_box_pts)

    best_angle = _coarse_angle_search(bin_img_cropped, max_skew, coarse_target)
    best_fine_angle = _fine_angle_search(bin_img_cropped, best_angle, fine_target)

    return _result(best_fine_angle)


def _calculate_deskew_matrix(page: Any, deg: float) -> "Matrix":
    """Calculates visual corrective rotation matrix for a page."""
    from pikepdf import Matrix

    from pdftl.utils.geometry import resolve_anchor, wrap_visual_matrix

    vis_dims = get_visible_page_dimensions(page, apply_rotate=True)
    if vis_dims is None:
        logger.debug("Unable to fetch visual dimensions.")
        return Matrix()
    v_x0, v_y0, v_w, v_h = vis_dims

    ax, ay = resolve_anchor("center", v_x0, v_y0, v_w, v_h)

    m1 = Matrix().translated(-ax, -ay)
    m3 = Matrix().translated(ax, ay)
    m2 = Matrix().rotated(deg)
    visual_matrix = m1 @ m2 @ m3

    wrapped = wrap_visual_matrix(page, visual_matrix)
    if wrapped is None:
        logger.debug("Unable to fetch unrotated visual dimensions.")
        return Matrix()
    return wrapped
