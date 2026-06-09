# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/resample_images.py

"""Resample images in a PDF."""

from __future__ import annotations

import io
import logging
import zlib
from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.images import extract_pdf_images
from pdftl.utils.keyval_parser import parse_keyval_list
from pdftl.utils.page_specs import page_numbers_matching_page_specs

if TYPE_CHECKING:
    import pikepdf
    from PIL import Image


logger = logging.getLogger(__name__)

_RESAMPLE_IMAGES_LONG_DESC = """
The `resample_images` operation permanently resizes images in the PDF
that exceed a specified DPI (Dots Per Inch) threshold.

Arguments:
  * `<specs>`: Optional page ranges to limit the operation.

  * `dpi=<n>`: The target DPI. By default, images above this are resampled. (Default: 150)

  * `quality=<q>`: The JPEG compression quality (1-100) used when writing back lossy
    images. (Default: 75)

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
        "desc": "Resample all eligible images globally to max 150 DPI.",
    },
    {
        "cmd": "in.pdf resample_images 1-5 dpi=72 quality=60 output out.pdf",
        "desc": "Resample images on pages 1-5 to 72 DPI with aggressive compression.",
    },
    {
        "cmd": "in.pdf resample_images force=true output out.pdf",
        "desc": "Resample all images including indexed and spot-color, converting colorspaces.",
    },
]


def _validate_dpi(val_str: str) -> int:
    """Helper to validate dpi integer boundaries."""
    try:
        val = int(val_str)
        if val <= 0:
            raise ValueError
        return val
    except ValueError as exc:
        raise InvalidArgumentError(
            f"resample_images: Invalid value for dpi: '{val_str}'. Must be a positive integer."
        ) from exc


def _validate_quality(val_str: str) -> int:
    """Helper to validate quality boundaries."""
    try:
        val = int(val_str)
        if not (1 <= val <= 100):
            raise ValueError
        return val
    except ValueError as exc:
        raise InvalidArgumentError(
            f"resample_images: Invalid value for quality: '{val_str}'. "
            "Must be an integer between 1 and 100."
        ) from exc


def _parse_args(args: list) -> tuple[int, int, bool, bool, bool, list]:
    """Parses incoming arguments via the shared keyval_parser."""
    page_specs = []
    kv = parse_keyval_list(
        args or [],
        bare_tokens=page_specs,
        allowed_keys=["dpi", "quality", "allow_growth", "allow_upscale", "force"],
        context="resample_images",
    )

    dpi = _validate_dpi(kv["dpi"]) if "dpi" in kv else 150
    quality = _validate_quality(kv["quality"]) if "quality" in kv else 75
    allow_upscale = (
        kv["allow_upscale"].lower() in ("true", "1", "yes") if "allow_upscale" in kv else False
    )
    allow_growth = (
        kv["allow_growth"].lower() in ("true", "1", "yes")
        if "allow_growth" in kv
        else allow_upscale
    )
    force = kv["force"].lower() in ("true", "1", "yes") if "force" in kv else False

    return dpi, quality, allow_upscale, allow_growth, force, page_specs


def _get_resample_dims(img: dict, dpi: int, allow_upscale: bool) -> tuple[int, int] | None:
    """Calculates target dimensions, or returns None if the image does not qualify.

    An image qualifies if either axis exceeds dpi. Target dimensions are
    derived from the rendered bounding box, not the pixel dimensions, so the
    output resolution matches the requested dpi regardless of how the image
    was originally placed.
    """
    bbox = img["bbox"]

    # 1. Calculate exactly what pixel dimensions are required to hit target_dpi
    new_width = int(round(((bbox[2] - bbox[0]) / 72.0) * dpi))
    new_height = int(round(((bbox[3] - bbox[1]) / 72.0) * dpi))

    if new_width == 0 or new_height == 0:
        return None

    # 2. The Micro-pixel Guard (Tolerance)
    # If the math demands the exact same pixel dimensions the image already has,
    # resampling is a mathematical no-op. Skip to prevent JPEG generation loss.
    if new_width == img["width_px"] and new_height == img["height_px"]:
        return None

    # 3. The Upscale Guard
    # If we are strictly downsampling, abort if the math asks us to generate MORE
    # pixels on either axis than we currently have.
    if not allow_upscale and (new_width > img["width_px"] or new_height > img["height_px"]):
        return None

    return new_width, new_height


def _get_orig_stream_size(stream_obj) -> int:
    """Returns the compressed stream size in bytes, as stored in the PDF.

    Uses read_raw_bytes() so the result is comparable to the compressed size
    produced by the growth guard, not the decompressed pixel data.
    Falls back to a large sentinel so the growth guard passes on error.
    """
    import pikepdf

    try:
        return len(stream_obj.read_raw_bytes())
    except (pikepdf.PdfError, AttributeError, ValueError):
        return 999_999_999


# PIL modes that round-trip cleanly back to their PDF colorspace after resize.
# Any mode not in this set indicates a complex or exotic colorspace (ICCBased
# with unusual encoding, Separation, DeviceN, Lab, etc.) where we cannot
# guarantee that writing raw PIL bytes back will be consistent with the
# existing /ColorSpace entry. We skip those images rather than risk corrupting
# the colorspace metadata, which would break prepress workflows.
_SAFE_PIL_MODES = frozenset({"RGB", "L", "CMYK", "1"})

# Mapping from exotic PIL modes to the nearest safe equivalent used when
# force=true. The colorspace declaration in the PDF will be updated to match.
_FORCE_CONVERT_MODES: dict[str, str] = {"P": "RGB", "PA": "RGBA", "LA": "L"}


def _get_resized_pil_image(
    xobj, is_bitonal: bool, width: int, height: int, *, force: bool = False
) -> Image.Image | None:
    """Decodes the image, resizes it, and returns the PIL image.

    Returns None if the decoded PIL mode is not in the safe set and force is
    False, meaning the image has a complex colorspace that we cannot safely
    round-trip. When force is True, exotic modes are converted to the nearest
    safe equivalent and the caller is responsible for updating the colorspace
    declaration.
    """
    from PIL import Image
    from pikepdf.models import PdfImage

    pil_img = PdfImage(xobj).as_pil_image()

    if pil_img.mode not in _SAFE_PIL_MODES:
        if not force:
            logger.debug(
                "Skipping resample: PIL decoded to mode '%s', which cannot be safely "
                "round-tripped without modifying the colorspace metadata. "
                "Use force=true to resample anyway.",
                pil_img.mode,
            )
            return None
        target = _FORCE_CONVERT_MODES.get(pil_img.mode, "RGB")
        logger.info(
            "force=true: converting mode '%s' to '%s' — colorspace metadata will be updated.",
            pil_img.mode,
            target,
        )
        pil_img = pil_img.convert(target)

    if is_bitonal:
        return pil_img.resize((width, height), Image.Resampling.NEAREST).convert("1")

    return pil_img.resize((width, height), Image.Resampling.LANCZOS)


def _resize_soft_mask(xobj, width: int, height: int, page_num: int) -> tuple[any, bytes | None]:
    """Resizes the soft mask (SMask) sub-object to match the parent image dimensions."""
    import pikepdf
    from PIL import Image
    from pikepdf.models import PdfImage

    smask_xobj = xobj.get("/SMask")
    if not (smask_xobj and isinstance(smask_xobj, pikepdf.Stream)):
        return None, None
    try:
        smask_pil = PdfImage(smask_xobj).as_pil_image().convert("L")
        smask_pil = smask_pil.resize((width, height), Image.Resampling.LANCZOS)
        return smask_xobj, smask_pil.tobytes()
    except (pikepdf.PdfError, AttributeError, ValueError, OSError) as e:
        logger.debug("Page %s: Failed to extract soft mask: %s", page_num, e)
        return None, None


def _encode_image(
    pil_img: Image.Image, is_jpeg: bool, quality: int
) -> tuple[bytes, pikepdf.Name, int]:
    """Encodes the resized PIL image into bytes suitable for writing to a PDF stream.

    For JPEG images, encodes as DCT. For everything else, returns compressed pixel
    bytes with a FlateDecode filter. pikepdf.write() does not compress automatically,
    so we pre-compress with zlib.
    """
    import pikepdf

    # JPEG images must be decoded and re-encoded when their pixel dimensions
    # change. This preserves visual content but does not preserve original
    # JPEG encoding details such as quantization tables, chroma subsampling,
    # progressive encoding, or other encoder-specific metadata.
    #
    # Currently resampled JPEGs are written back as JPEG because that is
    # typically the smallest representation for photographic content.
    #
    # Future enhancement:
    #   Add an option allowing JPEG sources to be rewritten as lossless
    #   FlateDecode images after resampling, potentially gated on file-size
    #   reduction (e.g. only replace when the lossless representation is not
    #   larger than the JPEG result, or not larger than the original stream).
    #   This would allow users to trade file size for generation-loss avoidance.
    if is_jpeg:
        out_buf = io.BytesIO()
        pil_img.save(out_buf, format="JPEG", quality=quality)
        new_bytes = out_buf.getvalue()
        return new_bytes, pikepdf.Name("/DCTDecode"), len(new_bytes)

    raw_bytes = pil_img.tobytes()
    compressed = zlib.compress(raw_bytes)
    return compressed, pikepdf.Name("/FlateDecode"), len(compressed)


def _apply_metadata_updates(xobj, mode: str, is_bitonal: bool, *, force: bool = False) -> None:
    """Updates stream dictionary entries that must change after a resize.

    /ColorSpace is never overwritten unless force=True. Overwriting an existing
    /ColorSpace (e.g. an ICCBased profile or /DeviceCMYK declaration) would break
    colour-managed and prepress workflows. When force=True the declaration is
    updated to match the converted pixel data.
    """
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
    xobj,
    new_bytes: bytes,
    filter_name: pikepdf.Name,
    width: int,
    height: int,
    mode: str,
    is_bitonal: bool,
    smask_xobj,
    smask_bytes: bytes | None,
    *,
    force: bool = False,
) -> None:
    """Writes the new pixel data and updated dimensions to the PDF XObjects in place."""
    import pikepdf

    xobj.write(new_bytes, filter=filter_name)
    xobj.Width, xobj.Height = width, height
    _apply_metadata_updates(xobj, mode, is_bitonal, force=force)

    if smask_xobj and smask_bytes:
        smask_xobj.write(smask_bytes, filter=pikepdf.Name("/FlateDecode"))
        smask_xobj.Width, smask_xobj.Height = width, height
        _apply_metadata_updates(smask_xobj, "L", False)
        smask_xobj.BitsPerComponent = 8


def _execute_resample(
    img: dict,
    xobj,
    dims: tuple[int, int],
    quality: int,
    allow_growth: bool,
    page_num: int,
    *,
    force: bool = False,
) -> bool:
    """Validates and executes the resample for a single image, returning True on success."""
    new_width, new_height = dims
    is_bitonal = (
        xobj.get("/ImageMask")  # truthiness; avoid is True in case we get a pikepdf.Boolean
        or int(xobj.get("/BitsPerComponent", 8)) == 1
    )

    pil_img = _get_resized_pil_image(xobj, is_bitonal, new_width, new_height, force=force)
    if pil_img is None:
        return False

    smask_xobj, smask_bytes = _resize_soft_mask(xobj, new_width, new_height, page_num)

    orig_size = _get_orig_stream_size(xobj)
    if smask_bytes and not allow_growth:
        orig_size += _get_orig_stream_size(smask_xobj)

    is_jpeg = img.get("format") == "dctdecode" and not is_bitonal
    new_bytes, filter_name, est_size = _encode_image(pil_img, is_jpeg, quality)

    if smask_bytes:
        est_size += len(zlib.compress(smask_bytes))

    if not allow_growth and est_size >= orig_size:
        logger.debug(
            "Page %s: Skipping %s — compressed payload would grow.", page_num, img["name"]
        )
        return False

    _commit_resampled_data(
        xobj,
        new_bytes,
        filter_name,
        new_width,
        new_height,
        pil_img.mode,
        is_bitonal,
        smask_xobj,
        smask_bytes,
        force=force,
    )
    logger.info(
        "Page %s: Resampled %s from %dx%d to %dx%d",
        page_num,
        img["name"],
        img["width_px"],
        img["height_px"],
        new_width,
        new_height,
    )
    return True


def _resample_single_image(
    img: dict,
    dpi: int,
    quality: int,
    allow_upscale: bool,
    allow_growth: bool,
    seen_objgens: set,
    *,
    force: bool = False,
) -> bool:
    """Runs the full resample pipeline for one image entry.

    The seen_objgens set prevents processing the same underlying PDF object
    twice — the same XObject can be referenced from multiple pages or positions,
    and modifying it once is sufficient (and correct) for all placements.
    """
    import pikepdf

    dims = _get_resample_dims(img, dpi, allow_upscale)
    page_num = img.get("page", "?")
    if not dims:
        return False

    xobj = img["xobj"]
    if xobj.objgen in seen_objgens:
        return False
    seen_objgens.add(xobj.objgen)

    try:
        return _execute_resample(img, xobj, dims, quality, allow_growth, page_num, force=force)
    except (pikepdf.PdfError, ValueError, TypeError, OSError, RuntimeError) as e:
        logger.debug("Page %s: Failed to resample image %s: %s", page_num, img["name"], e)
        return False


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
    """Resample images exceeding the dpi threshold."""
    dpi, quality, allow_upscale, allow_growth, force, page_specs = _parse_args(operation_args)
    num_pages = len(pdf.pages)

    target_pages = (
        sorted(list(page_numbers_matching_page_specs(page_specs, num_pages)))
        if page_specs
        else list(range(1, num_pages + 1))
    )

    images = extract_pdf_images(pdf, target_pages)
    seen_objgens = set()
    resample_count = 0

    for img in images:
        if _resample_single_image(
            img, dpi, quality, allow_upscale, allow_growth, seen_objgens, force=force
        ):
            resample_count += 1

    logger.info(
        "Resampled %d image(s) to max %s DPI (JPEG Quality: %d, Guard Active: %s).",
        resample_count,
        dpi,
        quality,
        str(not allow_growth),
    )
    return OpResult(success=True, pdf=pdf)
