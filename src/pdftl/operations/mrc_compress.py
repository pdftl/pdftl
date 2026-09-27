# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/mrc_compress.py

"""Separate scanned pages into MRC (Mixed Raster Content) layers.

A qualifying page (see `pdftl.utils.mrc.classify.classify_page`) has its
scanned image split into a 1-bit stencil at full resolution, a
low-resolution ink-colour foreground drawn through that stencil, and a
low-resolution paper background. Other pages are left alone.
"""

from __future__ import annotations

import logging
import typing

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.dependencies import ensure_dependencies
from pdftl.utils.keyval_parser import parse_keyval_list

# numpy and PIL are imported inside functions: every operation module is
# imported at CLI startup.
if typing.TYPE_CHECKING:
    import numpy as np
    from PIL import Image

    from pdftl.utils.mrc import classify

logger = logging.getLogger(__name__)

DEFAULT_BG_DIV = 3
DEFAULT_FG_DIV = 4
SAUVOLA_K = 0.20
# Supersampling smooths stencil edges on clean sources but bolds real scans (~12% ink area).
DEFAULT_STENCIL_SUPERSAMPLE = 1
# Background exclusion halo around text, in source pixels: 1 still left a faint text ghost.
HALO_RADIUS = 3
GRAY_SPREAD = 6  # max-min channel spread (99.9th percentile) still treated as gray
# Worst-tile edge loss above which the layers smear detail the stencil missed
# (camera photos of pages); clean scans stay below ~0.3.
MAX_EDGE_LOSS = 0.4

_MRC_COMPRESS_LONG_DESC = """
The `mrc_compress` operation separates each scanned page into Mixed Raster
Content (MRC) layers: a 1-bit text/line-art stencil at full resolution, a
small ink-colour foreground, and a small paper background, each compressed
with the codec that suits it. Text stays sharp while the colour layers
only carry colour.

Only pages that are a single upright full-page scan (invisible OCR text is
fine) are changed, and only when the layers come out smaller than the
original image and keep its detail: a page where text the stencil misses
would smear into the background, as in camera photos of pages, is left
alone. Grayscale scans get grayscale layers. A scan that would
decode to more than `PDFTL_MAX_DECODED_MB` (default 512) is left alone.

Arguments:
  * `bg_div=<n>`: background downsample divisor, 1-12 (default: 3).
  * `fg_div=<n>`: foreground downsample divisor, 1-12 (default: 4).
  * `bg_quality=<n>`: background JPEG quality, 1-100 (default: 75).
  * `fg_quality=<n>`: foreground JPEG quality, 1-100 (default: 45); the
    foreground is near-flat colour, or a single swatch.
  * `ink_percentile=<n>`: on a page printed in a single ink colour, average
    only the darkest `n`% of the ink's stroke cores (default: 100, all of
    them). Lower values give darker ink. It has no effect on pages with
    several ink colours, whose ink colour varies across the page.
  * `supersample=<n>`: threshold a bicubic `n`-times upsampled copy for a
    smoother stencil, 1-4 (default: 1, off). Only for clean sources, such
    as pages rendered from vector PDFs: on real scans it makes text bolder.
"""

_MRC_COMPRESS_EXAMPLES = [
    {
        "cmd": "in.pdf mrc_compress output out.pdf",
        "desc": "Separate every eligible scanned page into MRC layers.",
    },
    {
        "cmd": "in.pdf mrc_compress bg_div=4 fg_div=6 output out.pdf",
        "desc": "Use coarser background/foreground downsampling for a smaller file.",
    },
    {
        "cmd": "in.pdf mrc_compress bg_quality=90 fg_quality=70 output out.pdf",
        "desc": "Use higher JPEG quality for the colour layers, at a larger file size.",
    },
    {
        "cmd": "in.pdf mrc_compress ink_percentile=10 output out.pdf",
        "desc": "Darker ink colour, for a document whose ink looks too pale.",
    },
    {
        "cmd": "in.pdf mrc_compress supersample=2 output out.pdf",
        "desc": "Smoother stencil edges, for a clean, low-noise source.",
    },
]

_ALLOWED_ARGS = ["bg_div", "fg_div", "bg_quality", "fg_quality", "ink_percentile", "supersample"]


def _validate_int_range(val_str: str, name: str, lo: int, hi: int) -> int:
    try:
        val = int(val_str)
        if not lo <= val <= hi:
            raise ValueError
        return val
    except ValueError as exc:
        raise InvalidArgumentError(
            f"mrc_compress: Invalid value for {name}: '{val_str}'. Must be an integer {lo}-{hi}."
        ) from exc


def _parse_args(args: list) -> tuple[int, int, int, int, int, int]:
    from pdftl.utils.mrc import codecs, segmentation

    kv = parse_keyval_list(
        args or [], bare_tokens=[], allowed_keys=_ALLOWED_ARGS, context="mrc_compress"
    )

    def _get(name: str, lo: int, hi: int, default: int) -> int:
        return _validate_int_range(kv[name], name, lo, hi) if name in kv else default

    return (
        _get("bg_div", 1, 12, DEFAULT_BG_DIV),
        _get("fg_div", 1, 12, DEFAULT_FG_DIV),
        _get("bg_quality", 1, 100, codecs.DEFAULT_BG_JPEG_QUALITY),
        _get("fg_quality", 1, 100, codecs.DEFAULT_FG_QUALITY),
        _get("ink_percentile", 1, 100, int(segmentation.DEFAULT_INK_PERCENTILE)),
        _get("supersample", 1, 4, DEFAULT_STENCIL_SUPERSAMPLE),
    )


def _lift_pixels(candidate: classify.Candidate) -> Image.Image | None:
    """The scan's own samples, or None if this runtime cannot decode them."""
    import pikepdf

    from pdftl.utils.pikepdf_compatibility_utils import image_decode_errors

    try:
        return pikepdf.PdfImage(candidate.xobj).as_pil_image()
    except image_decode_errors():  # the caller rasterizes instead
        return None


def _page_failures() -> tuple[type[Exception], ...]:
    """What a page's separation raises on data it cannot handle."""
    import pikepdf
    import pypdfium2

    from pdftl.utils.mrc import MrcError

    return (MrcError, pikepdf.PdfError, pikepdf.DataDecodingError, pypdfium2.PdfiumError, OSError)


def _rasterize_placement(pdf, page, page_index: int, candidate: classify.Candidate):
    """Render the page and crop the placement out of it."""
    from pdftl.utils.mrc import classify
    from pdftl.utils.page_images import render_page_region_to_pil

    px0, py0, px1, py1 = classify.page_box(page)
    rx0, ry0, rx1, ry1 = candidate.rect
    crop_pts = (rx0 - px0, ry0 - py0, px1 - rx1, py1 - ry1)
    dpi = min(max(candidate.source_dpi, 100), 600)
    return render_page_region_to_pil(pdf, page_index, dpi, crop_pts)


def _looks_gray(rgb: np.ndarray) -> bool:
    """Whether an RGB scan carries no real colour (JPEG leaves a little chroma noise)."""
    import numpy as np

    from pdftl.utils.mrc.segmentation import STRIP_ROWS

    spread = np.empty(rgb.shape[:2], dtype=np.uint8)
    for top in range(0, rgb.shape[0], STRIP_ROWS):
        rows = rgb[top : top + STRIP_ROWS]
        np.subtract(rows.max(axis=2), rows.min(axis=2), out=spread[top : top + STRIP_ROWS])
    return float(np.percentile(spread, 99.9)) <= GRAY_SPREAD


def _build_foreground(
    rgb: np.ndarray,
    ink: np.ndarray,
    gray: np.ndarray,
    size: tuple[int, int],
    fg_div: int,
    ink_percentile: int,
    *,
    supersample: int = 1,
):
    import numpy as np
    from PIL import Image

    from pdftl.utils.mrc import segmentation

    if not ink.any():
        return Image.new("RGB", (1, 1), (0, 0, 0))
    fg_size = (max(size[0] // fg_div, 1), max(size[1] // fg_div, 1))

    def covers(mask):
        return segmentation.coverage(mask, fg_size) > segmentation._EPS

    # Colour comes from each stroke's core: Sauvola's ink includes the light
    # anti-aliased rim, whose average rendered text 12-18 levels too light.
    core = segmentation.dilate_bool(~ink, radius=supersample)
    np.logical_not(core, out=core)
    colour_ink = core if core.any() else ink
    fg_arr = segmentation.masked_mean(rgb, colour_ink, fg_size)
    # Blocks with only rim ink use it; blocks with no ink at all would get
    # masked_mean's coarse fallback, which at a photo edge is the photo's own
    # average rather than the paper beside it, so those use a dilated mask.
    # Dilating for every block would mix paper into real ink.
    fg_rim = segmentation.masked_mean(rgb, ink, fg_size)
    dilated_keep = segmentation.dilate_bool(ink, radius=HALO_RADIUS * supersample)
    fg_dilated = segmentation.masked_mean(rgb, dilated_keep, fg_size)
    del dilated_keep
    fg_arr = np.where(
        covers(colour_ink)[..., None],
        fg_arr,
        np.where(covers(ink)[..., None], fg_rim, fg_dilated),
    )
    if segmentation.flat_ink_chroma_variance(fg_arr) < segmentation.FLAT_INK_CHROMA_VARIANCE:
        fg_arr = segmentation.core_ink_rgb(rgb, colour_ink, gray, percentile=ink_percentile)
        fg_arr = fg_arr.reshape(1, 1, 3)
    return segmentation.to_image(fg_arr)


def _build_background(rgb, ink, stats, has_pictorial, size, bg_div, work_dpi, supersample):
    import numpy as np

    from pdftl.utils.mrc import segmentation

    # Exclude a halo around text-shaped ink: Sauvola leaves each glyph's
    # anti-aliased edge as "paper", which would ghost the text into the
    # background. Other ink (photo leakage, busy regions) excludes only
    # itself, as a halo there would punch holes into the photo.
    text_ink = segmentation.text_like_mask(ink, stats["text_height"])
    text_ink &= ~segmentation.busy_region_mask(ink, dpi=work_dpi)
    # The halo covers all text ink, so what is left to exclude is ink | halo.
    excluded = segmentation.dilate_bool(text_ink, radius=HALO_RADIUS * supersample)
    del text_ink
    excluded |= ink
    bg_keep = np.logical_not(excluded, out=excluded)
    page_bg_div = min(bg_div, 2) if has_pictorial else bg_div
    bg_size = (max(size[0] // page_bg_div, 1), max(size[1] // page_bg_div, 1))
    return segmentation.to_image(segmentation.masked_mean(rgb, bg_keep, bg_size))


def _process_candidate(
    pdf,
    page,
    page_index: int,
    candidate,
    bg_div: int,
    fg_div: int,
    bg_quality: int,
    fg_quality: int,
    ink_percentile: int,
    supersample: int,
    guard_fidelity: bool = True,
) -> dict:
    import numpy as np
    from PIL import Image

    from pdftl.utils.mrc import codecs, fidelity, segmentation
    from pdftl.utils.mrc.splice import splice_layers

    source = _lift_pixels(candidate)
    if source is None:
        source = _rasterize_placement(pdf, page, page_index, candidate)
    native_gray = source.mode in ("1", "L", "LA", "I", "I;16")
    source = source.convert("RGB")

    work, work_dpi = source, candidate.source_dpi
    if supersample > 1:
        work = source.resize(
            (source.width * supersample, source.height * supersample), Image.BICUBIC
        )
        work_dpi = candidate.source_dpi * supersample

    size = source.size
    gray = np.asarray(work.convert("L"), dtype=np.uint8)
    rgb = np.asarray(work, dtype=np.uint8)
    del source, work
    ink, pictorial, stats = segmentation.segment(gray, dpi=work_dpi, k=SAUVOLA_K)
    has_pictorial = bool(pictorial.any())
    del pictorial
    stencil = codecs.encode_stencil(segmentation.mask_image(ink))
    gray_layers = native_gray or _looks_gray(rgb)

    background = _build_background(
        rgb, ink, stats, has_pictorial, size, bg_div, work_dpi, supersample
    )
    bg_bytes, bg_filter, bg_extra = codecs.encode_background(
        background, quality=bg_quality, gray=gray_layers
    )
    foreground = _build_foreground(
        rgb, ink, gray, size, fg_div, ink_percentile, supersample=supersample
    )
    fg_bytes = codecs.encode_foreground_jpeg(foreground, quality=fg_quality, gray=gray_layers)

    loss = fidelity.edge_loss(gray, ink, foreground, background, work_dpi)
    if guard_fidelity and loss > MAX_EDGE_LOSS:
        return {
            "decision": "untouched",
            "reason": f"MRC would blur detail (edge loss {loss:.2f})",
            "edge_loss": round(loss, 3),
        }

    original_bytes = len(candidate.xobj.read_raw_bytes())
    new_bytes = len(stencil.data) + len(bg_bytes) + len(fg_bytes)
    if new_bytes >= original_bytes:
        return {
            "decision": "untouched",
            "reason": f"MRC layers not smaller ({new_bytes} >= {original_bytes} bytes)",
        }

    # /Interpolate asks viewers to smooth the upscaled low-resolution layers.
    stencil_xobj = codecs.make_stencil_xobject(pdf, stencil)
    bg_xobj = codecs.make_image_xobject(
        pdf, bg_bytes, background.width, background.height, bg_filter, Interpolate=True, **bg_extra
    )
    fg_xobj = codecs.make_image_xobject(
        pdf,
        fg_bytes,
        foreground.width,
        foreground.height,
        "/DCTDecode",
        ColorSpace=codecs.colour_space(gray_layers),
        BitsPerComponent=8,
        Mask=stencil_xobj,
        Interpolate=True,
    )
    splice_layers(pdf, page, candidate, bg_xobj, fg_xobj)
    return {
        "decision": "mrc",
        "gray": gray_layers,
        "original_bytes": original_bytes,
        "mask_codec": stencil.codec,
        "mask_bytes": len(stencil.data),
        "bg_bytes": len(bg_bytes),
        "fg_bytes": len(fg_bytes),
        "source_dpi": candidate.source_dpi,
        "text_height": stats["text_height"],
        "marks": stats["marks"],
        "edge_loss": round(loss, 3),
    }


@register_operation(
    "mrc_compress",
    tags=["in_place", "images", "compression"],
    type="single input operation",
    desc="Separate scanned pages into MRC (text/foreground/background) layers",
    long_desc=_MRC_COMPRESS_LONG_DESC,
    usage=(
        "<input> mrc_compress [bg_div=val] [fg_div=val] [bg_quality=val] "
        "[fg_quality=val] [ink_percentile=val] [supersample=val] output <output>"
    ),
    examples=_MRC_COMPRESS_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def mrc_compress(pdf, operation_args: list, guard_fidelity: bool = True) -> OpResult:
    """Splits each eligible scanned page's image into MRC layers, in place.

    Without `guard_fidelity`, a page is not left untouched for exceeding
    `MAX_EDGE_LOSS`; it is still left untouched when the layers are not
    smaller than the original image.
    """
    ensure_dependencies(
        feature_name="mrc_compress", dependencies=["numpy", "numba"], extra_tag="mrc-compress"
    )
    from pdftl.utils.mrc import classify
    from pdftl.utils.system_memory import too_large_to_decode

    settings = _parse_args(operation_args)
    pages_report = []
    applied = 0
    for page_index, page in enumerate(pdf.pages):
        page_number = page_index + 1
        candidate, reason = classify.classify_page(pdf, page, page_number)
        if candidate is not None and too_large_to_decode(
            candidate.xobj,
            "mrc_compress",
            scale=settings[-1] ** 2,  # supersample
        ):
            candidate, reason = None, "image too large to decode"
        if candidate is None:
            pages_report.append({"page": page_number, "decision": "untouched", "reason": reason})
            continue
        try:
            result = _process_candidate(
                pdf, page, page_index, candidate, *settings, guard_fidelity=guard_fidelity
            )
        except _page_failures() as exc:
            logger.warning(
                "mrc_compress: page %d failed MRC separation, leaving it untouched: %s",
                page_number,
                exc,
            )
            result = {"decision": "untouched", "reason": f"MRC failed: {exc}"}
        pages_report.append({"page": page_number, **result})
        applied += result["decision"] == "mrc"

    summary = f"MRC-compressed {applied} of {len(pdf.pages)} page(s)"
    logger.info(summary)
    return OpResult(
        success=True,
        pdf=pdf,
        data={
            "pages": pages_report,
            "pages_mrc": applied,
            "pages_untouched": len(pdf.pages) - applied,
        },
        summary=summary,
    )
