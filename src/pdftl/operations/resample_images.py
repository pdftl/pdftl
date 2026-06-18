# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/resample_images.py

"""Resample images in a PDF."""

from __future__ import annotations

import io
import logging
import os
import zlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.helpers.image_processor import (
    ImageContext,
    ensure_thread_safe,
    get_orig_stream_size,
    run_parallel_image_job,
)
from pdftl.utils.images import extract_pdf_images
from pdftl.utils.keyval_parser import parse_keyval_list
from pdftl.utils.page_specs import page_numbers_matching_page_specs

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)

_RESAMPLE_IMAGES_LONG_DESC = """
The `resample_images` operation permanently resizes images in the PDF
that exceed a specified DPI (Dots Per Inch) threshold. The heavy image
processing is multithreaded for performance.

Arguments:
  * `<specs>`: Optional page ranges to limit the operation.

  * `dpi=<n>`: The target DPI. By default, images above this are resampled. (Default: 150)

  * `quality=<q>`: The JPEG compression quality (1-100) used when writing back lossy
    images. (Default: 75)

  * `threads=<n>`: The number of worker threads to use for image processing.
    (Default: Automatic based on CPU cores)

  * `allow_upscale=<b>`: If true, allows upscaling, so that all images are targeted.
    If false, downsample only, that is: only target images whose horizontal or vertical
    resolution exceeds the `dpi` value.

  * `allow_growth=<b>`: If true, allows images to replace originals even if the compressed
    stream size grows (useful for strict pre-press/compliance). (Default: same as `allow_upscale`)

  * `force=<b>`: If true, resample images with complex colorspaces (/Indexed,
    /Separation, /DeviceN, etc.) by converting them to a standard
    colorspace. The colorspace declaration in the PDF will be updated
    to match. This may alter colors in palette or spot-color images.
    (Default: false)

**Image types supported:**

  * Grayscale (/DeviceGray) and color (/DeviceRGB, /DeviceCMYK) images, including
    those with embedded ICC profiles (/ICCBased) — color profiles are preserved exactly.
  * Transparency-masked images (soft mask / SMask) — the mask is resized alongside
    the parent image.
  * Bitonal (1-bit) images — resampled with nearest-neighbor to avoid introducing
    gray pixels.

**Images that will be skipped:**

  * Images whose colorspace cannot be safely round-tripped after resize. This includes
    /Indexed (palette), /Separation (spot color), /DeviceN, /Lab, and other exotic
    colorspaces. Modifying these would require rewriting the colorspace declaration,
    which risks breaking color-managed and prepress workflows. Use `force=true` to
    resample these anyway — the colorspace will be converted and the declaration
    updated. A debug log entry is emitted for each skipped image.
  * Images where the recompressed stream would be larger than the original
    (unless `allow_growth=true` is set).

**How DPI is determined:**

* DPI is calculated from the image's rendered size on the page, not from
  metadata embedded in the image. An image is only resampled when its
  effective horizontal or vertical DPI exceeds the target threshold.

* For image objects that are used only once, resampling is based on that
  placement's effective DPI.

* PDF image objects may be shared and reused at multiple sizes throughout
  a document. Shared image objects are processed only once. When the same
  image appears at multiple scales, the first placement encountered is used
  to determine the target resolution, and the resulting effective DPI may
  differ between placements.

**Behavior and guarantees:**

  * Shared image XObjects are processed only once, even if they appear on
    multiple pages. This avoids repeatedly recompressing the same PDF object.
  * Embedded ICC color profiles are preserved whenever the image colorspace
    can be safely round-tripped. This helps maintain color fidelity in
    color-managed workflows.
  * Soft masks (/SMask) are resized alongside their parent images so
    transparency remains aligned.
  * Bitonal (1-bit) images are resized using nearest-neighbor resampling to
    avoid introducing gray pixels into black-and-white scans, text, or line art.
  * By default, images are only replaced if the resulting compressed stream is
    smaller than the original. Use `allow_growth=true` to prioritize DPI
    compliance over file size.

**Limitations:**

  * A single PDF image object may be reused at different sizes throughout the
    document. Because PDF image objects are shared resources, the operation
    can only store one pixel resolution for that image. When a shared image
    appears at multiple scales, the resulting effective DPI may differ between
    placements after resampling.
  * JPEG images are decoded, resized, and re-encoded when resampling is
    required. Original JPEG quantization tables, chroma subsampling settings,
    and progressive encoding are not preserved.
  * Inline images embedded directly inside page content streams are not
    currently targeted.
"""

_RESAMPLE_IMAGES_EXAMPLES = [
    {
        "cmd": "in.pdf resample_images output out.pdf",
        "desc": "Resample all eligible images globally to max 150 DPI using multithreading.",
    },
    {
        "cmd": "in.pdf resample_images 1-5 dpi=72 quality=60 threads=2 output out.pdf",
        "desc": "Resample images on pages 1-5 to 72 DPI using 2 threads.",
    },
    {
        "cmd": "in.pdf resample_images force=true output out.pdf",
        "desc": "Resample all images including indexed and spot-color, converting colorspaces.",
    },
]

# PIL modes that round-trip cleanly back to their PDF colorspace after resize.
_SAFE_PIL_MODES = frozenset({"RGB", "L", "CMYK", "1"})

# Mapping from exotic PIL modes to the nearest safe equivalent used when force=true.
_FORCE_CONVERT_MODES: dict[str, str] = {"P": "RGB", "PA": "RGBA", "LA": "L"}


# --- Dataclasses for Thread Decoupling ---


@dataclass
class ExtractionPayload:
    """Pure Python data extracted from PDF, ready to be sent to a worker thread."""

    pil_img: Image.Image
    smask_pil: Image.Image | None
    new_width: int
    new_height: int
    is_jpeg: bool
    quality: int
    is_bitonal: bool
    force: bool


@dataclass
class ProcessedPayload:
    """The result of the worker thread's computation, ready to be written to PDF."""

    new_bytes: bytes
    filter_name: str
    est_size: int
    mode: str
    smask_bytes: bytes | None


# --- Argument Validation and Parsing ---


def _validate_int(val_str: str, name: str, min_val: int, max_val: int | None = None) -> int:
    """Helper to validate integer boundaries."""
    try:
        val = int(val_str)
        if val < min_val or (max_val is not None and val > max_val):
            raise ValueError
        return val
    except ValueError as exc:
        bounds = f"{min_val} to {max_val}" if max_val else f"at least {min_val}"
        raise InvalidArgumentError(
            f"resample_images: Invalid value for {name}: '{val_str}'. Must be an integer {bounds}."
        ) from exc


def _parse_args(args: list) -> tuple[int, int, int, bool, bool, bool, list]:
    """Parses incoming arguments via the shared keyval_parser."""
    page_specs = []
    kv = parse_keyval_list(
        args or [],
        bare_tokens=page_specs,
        allowed_keys=["dpi", "quality", "allow_growth", "allow_upscale", "force", "threads"],
        context="resample_images",
    )

    dpi = _validate_int(kv["dpi"], "dpi", 1) if "dpi" in kv else 150
    quality = _validate_int(kv["quality"], "quality", 1, 100) if "quality" in kv else 75

    default_threads = os.cpu_count() or 4
    threads = _validate_int(kv["threads"], "threads", 1) if "threads" in kv else default_threads

    allow_upscale = kv.get("allow_upscale", "").lower() in ("true", "1", "yes")
    allow_growth = (
        kv["allow_growth"].lower() in ("true", "1", "yes")
        if "allow_growth" in kv
        else allow_upscale
    )
    force = kv.get("force", "").lower() in ("true", "1", "yes")

    return dpi, quality, threads, allow_upscale, allow_growth, force, page_specs


# --- Extraction (Main Thread) ---


def _get_resample_dims(img: dict, dpi: int, allow_upscale: bool) -> tuple[int, int] | None:
    """Calculates target dimensions, or returns None if the image does not qualify."""
    bbox = img["bbox"]
    new_width = int(round(((bbox[2] - bbox[0]) / 72.0) * dpi))
    new_height = int(round(((bbox[3] - bbox[1]) / 72.0) * dpi))

    if new_width == 0 or new_height == 0:
        return None
    if new_width == img["width_px"] and new_height == img["height_px"]:
        return None
    if not allow_upscale and (new_width > img["width_px"] or new_height > img["height_px"]):
        return None

    return new_width, new_height


def _prepare_image_for_worker(
    img: dict,
    dpi: int,
    quality: int,
    allow_upscale: bool,
    allow_growth: bool,
    force: bool,
    seen_objgens: set,
) -> tuple[ExtractionPayload, ImageContext] | None:
    """Extracts PIL data and metadata from pikepdf objects safely on the main thread."""
    import pikepdf
    from pikepdf.models import PdfImage

    dims = _get_resample_dims(img, dpi, allow_upscale)
    if not dims:
        return None
    new_width, new_height = dims

    xobj = img["xobj"]
    if xobj.objgen in seen_objgens:
        return None
    seen_objgens.add(xobj.objgen)

    page_num = img.get("page", "?")

    try:
        is_bitonal = bool(xobj.get("/ImageMask") or int(xobj.get("/BitsPerComponent", 8)) == 1)
        pdf_img = PdfImage(xobj)
        pil_img = pdf_img.as_pil_image()
        ensure_thread_safe(pil_img)

        if pil_img.mode not in _SAFE_PIL_MODES and not force:
            logger.debug(
                "Skipping resample: PIL decoded to mode '%s'. Cannot round-trip safely. "
                "Use force=true to resample anyway.",
                pil_img.mode,
            )
            return None

        # Extract soft mask
        smask_xobj = xobj.get("/SMask")
        smask_pil = None
        if smask_xobj and isinstance(smask_xobj, pikepdf.Stream):
            smask_pil = PdfImage(smask_xobj).as_pil_image().convert("L")
            ensure_thread_safe(smask_pil)
        else:
            smask_xobj = None

        # Determine limits for the growth guard
        orig_size = get_orig_stream_size(xobj)
        if smask_xobj and not allow_growth:
            orig_size += get_orig_stream_size(smask_xobj)

        is_jpeg = img.get("format") == "dctdecode" and not is_bitonal

        payload = ExtractionPayload(
            pil_img=pil_img,
            smask_pil=smask_pil,
            new_width=new_width,
            new_height=new_height,
            is_jpeg=is_jpeg,
            quality=quality,
            is_bitonal=is_bitonal,
            force=force,
        )

        ctx = ImageContext(
            xobj=xobj, smask_xobj=smask_xobj, orig_size=orig_size, img_dict=img, page_num=page_num
        )

        return payload, ctx

    except (pikepdf.PdfError, ValueError, TypeError, OSError, RuntimeError) as e:
        logger.debug(
            "Page %s: Failed to extract image %s for resample: %s",
            page_num,
            img.get("name", "?"),
            e,
        )
        return None


# --- Processing (Worker Threads) ---


def _worker_compute_resample(payload: ExtractionPayload) -> ProcessedPayload:
    """Heavy lifting function. Runs entirely outside the GIL, with no pikepdf references."""
    from PIL import Image

    pil_img = payload.pil_img

    pil_img.load()
    if payload.smask_pil:
        payload.smask_pil.load()

    # Handle Colorspace conversions if forced
    if pil_img.mode not in _SAFE_PIL_MODES:
        target = _FORCE_CONVERT_MODES.get(pil_img.mode, "RGB")
        logger.info(
            "force=true: converting mode '%s' to '%s' — colorspace metadata will be updated.",
            pil_img.mode,
            target,
        )
        pil_img = pil_img.convert(target)

    # Resize main image
    if payload.is_bitonal:
        pil_img = pil_img.resize(
            (payload.new_width, payload.new_height), Image.Resampling.NEAREST
        ).convert("1")
    else:
        pil_img = pil_img.resize((payload.new_width, payload.new_height), Image.Resampling.LANCZOS)

    # Encode main image
    if payload.is_jpeg:
        out_buf = io.BytesIO()
        pil_img.save(out_buf, format="JPEG", quality=payload.quality)
        new_bytes = out_buf.getvalue()
        filter_name = "/DCTDecode"
    else:
        raw_bytes = pil_img.tobytes()
        new_bytes = zlib.compress(raw_bytes)
        filter_name = "/FlateDecode"

    est_size = len(new_bytes)

    # Resize and encode soft mask
    smask_bytes = None
    if payload.smask_pil:
        smask_pil_resized = payload.smask_pil.resize(
            (payload.new_width, payload.new_height), Image.Resampling.LANCZOS
        )
        smask_bytes = zlib.compress(smask_pil_resized.tobytes())
        est_size += len(smask_bytes)

    return ProcessedPayload(
        new_bytes=new_bytes,
        filter_name=filter_name,
        est_size=est_size,
        mode=pil_img.mode,
        smask_bytes=smask_bytes,
    )


# --- Commit (Main Thread) ---


def _apply_metadata_updates(xobj, mode: str, is_bitonal: bool, *, force: bool = False) -> None:
    """Updates stream dictionary entries that must change after a resize."""
    import pikepdf

    if "/DecodeParms" in xobj:
        del xobj["/DecodeParms"]

    if is_bitonal:
        xobj.BitsPerComponent = 1
    elif force or "/ColorSpace" not in xobj:
        if mode == "RGB":
            xobj.ColorSpace = pikepdf.Name("/DeviceRGB")
        elif mode == "L":
            xobj.ColorSpace = pikepdf.Name("/DeviceGray")
        elif mode == "CMYK":
            xobj.ColorSpace = pikepdf.Name("/DeviceCMYK")


def _commit_resampled_data(
    ctx: ImageContext, result: ProcessedPayload, payload: ExtractionPayload, allow_growth: bool
) -> bool:
    """Evaluates constraints and writes the computed bytes back into the pikepdf objects."""
    import pikepdf

    if not allow_growth and result.est_size >= ctx.orig_size:
        logger.debug(
            "Page %s: Skipping %s — compressed payload would grow.",
            ctx.page_num,
            ctx.img_dict["name"],
        )
        return False

    ctx.xobj.write(result.new_bytes, filter=pikepdf.Name(result.filter_name))
    ctx.xobj.Width = payload.new_width
    ctx.xobj.Height = payload.new_height

    _apply_metadata_updates(ctx.xobj, result.mode, payload.is_bitonal, force=payload.force)

    if ctx.smask_xobj and result.smask_bytes:
        ctx.smask_xobj.write(result.smask_bytes, filter=pikepdf.Name("/FlateDecode"))
        ctx.smask_xobj.Width = payload.new_width
        ctx.smask_xobj.Height = payload.new_height
        _apply_metadata_updates(ctx.smask_xobj, "L", False)
        ctx.smask_xobj.BitsPerComponent = 8

    logger.info(
        "Page %s: Resampled %s from %dx%d to %dx%d",
        ctx.page_num,
        ctx.img_dict["name"],
        ctx.img_dict["width_px"],
        ctx.img_dict["height_px"],
        payload.new_width,
        payload.new_height,
    )
    return True


@register_operation(
    "resample_images",
    tags=["in_place", "images", "optimization"],
    type="single input operation",
    desc="Resample images",
    long_desc=_RESAMPLE_IMAGES_LONG_DESC,
    usage="<input> resample_images [<spec>...] [key=val...] output <output>",
    examples=_RESAMPLE_IMAGES_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def resample_images(pdf, operation_args: list) -> OpResult:
    """Resample images exceeding the dpi threshold using a ThreadPoolExecutor."""
    dpi, quality, threads, allow_upscale, allow_growth, force, page_specs = _parse_args(
        operation_args
    )
    num_pages = len(pdf.pages)

    target_pages = (
        sorted(list(page_numbers_matching_page_specs(page_specs, num_pages)))
        if page_specs
        else list(range(1, num_pages + 1))
    )

    images = extract_pdf_images(pdf, target_pages)

    def prepare_wrapper(
        img_dict: dict, seen_set: set
    ) -> tuple[ExtractionPayload, ImageContext] | None:
        return _prepare_image_for_worker(
            img_dict, dpi, quality, allow_upscale, allow_growth, force, seen_set
        )

    def commit_wrapper(
        ctx: ImageContext, result: ProcessedPayload, payload: ExtractionPayload
    ) -> bool:
        return _commit_resampled_data(ctx, result, payload, allow_growth)

    resample_count = run_parallel_image_job(
        images=images,
        threads=threads,
        prepare_func=prepare_wrapper,
        worker_func=_worker_compute_resample,
        commit_func=commit_wrapper,
    )

    logger.info(
        "Resampled %d image(s) to max %s DPI (JPEG Quality: %d, Guard Active: %s, Threads: %d).",
        resample_count,
        dpi,
        quality,
        str(not allow_growth),
        threads,
    )
    return OpResult(success=True, pdf=pdf)
