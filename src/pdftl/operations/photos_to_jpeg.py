# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/photos_to_jpeg.py

"""Re-encode losslessly stored photographs as JPEG."""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Any

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
from pdftl.utils.images.finders import extract_pdf_images
from pdftl.utils.images.quality_search import lowest_passing
from pdftl.utils.images.similarity import faithful
from pdftl.utils.dependencies import ensure_dependencies
from pdftl.utils.keyval_parser import parse_keyval_list
from pdftl.utils.page_specs import page_numbers_matching_page_specs
from pdftl.utils.system_memory import IMAGE_MEMORY_HELP

logger = logging.getLogger(__name__)

DEFAULT_QUALITY = 75
MIN_PIXELS = 32 * 32
# Fewer colours suit a palette or Flate better than JPEG (a quarter of the
# pixels, for small images).
MIN_COLOURS = 1024
# Share of horizontally equal neighbours: screenshots, charts and text are flatter.
MAX_FLAT = 0.7
# A failing quality is retried up to this one, in steps of 5.
MAX_QUALITY = 95
# Lossy only for a large win.
MAX_RATIO = 0.5
_LOSSY_FILTERS = {"/DCTDecode", "/JPXDecode", "/JBIG2Decode", "/CCITTFaxDecode"}
_COLOUR_SPACES = {"/DeviceGray": 1, "/CalGray": 1, "/DeviceRGB": 3, "/CalRGB": 3}

_PHOTOS_TO_JPEG_LONG_DESC = """
The `photos_to_jpeg` operation re-encodes photographs that are stored
losslessly (Flate, LZW or uncompressed) as JPEG, which is usually far
smaller for them. Screenshots, charts, logos and scanned line art are left
as they are.

An image counts as a photograph when it is 8-bit gray or RGB, or a
palette image over gray or RGB, at least 32x32, has more than 1024
distinct colours (RGB only, not palette images; a quarter of its pixels if
fewer), no more than 70% of neighbouring pixels equal, and JPEG keeps it
above 30 dB PSNR (27 dB for palette images, whose dither JPEG smooths away)
and its local structure intact. If JPEG at `quality` falls short, higher
qualities up to 95 are searched, with and without chroma subsampling, for
the smallest JPEG that does not.
The image is replaced only if that JPEG is at most `max_ratio` of its size.
Masks and images with a `/Decode` array or colour-key mask are never
changed.

Arguments:
  * `<specs>`: Optional page ranges to limit the operation.
  * `quality=<q>`: JPEG quality, 1-100 (default: 75).
  * `max_ratio=<x>`: the largest JPEG, as a share of the stored image,
    worth the loss, over 0 and at most 1 (default: 0.5).
  * `threads=<n>`: Number of worker threads (default: CPU count).

{memory}
"""

_PHOTOS_TO_JPEG_EXAMPLES = [
    {
        "cmd": "in.pdf photos_to_jpeg output out.pdf",
        "desc": "Re-encode losslessly stored photographs as JPEG, quality 75.",
    },
    {
        "cmd": "in.pdf photos_to_jpeg 1-3 quality=85 output out.pdf",
        "desc": "Only pages 1-3, at JPEG quality 85.",
    },
]


@dataclass
class _Payload:
    pil_img: Any
    orig_size: int
    quality: int
    max_ratio: float = MAX_RATIO
    base: Any = None  # the palette's base colour space, for an /Indexed image
    guard_fidelity: bool = True


def _filters(xobj) -> list[str]:
    import pikepdf

    filt = xobj.get("/Filter")
    if filt is None:
        return []
    return [str(f) for f in filt] if isinstance(filt, pikepdf.Array) else [str(filt)]


def _components(colour_space) -> int | None:
    import pikepdf

    if isinstance(colour_space, pikepdf.Array):
        if len(colour_space) == 2 and str(colour_space[0]) == "/ICCBased":
            n = colour_space[1].get("/N")
            return int(n) if n in (1, 3) else None
        return None
    return _COLOUR_SPACES.get(str(colour_space))


def palette_base(colour_space):
    """The base colour space of an /Indexed space JPEG can carry, else None."""
    import pikepdf

    if (
        isinstance(colour_space, pikepdf.Array)
        and len(colour_space) == 4
        and str(colour_space[0]) == "/Indexed"
        and _components(colour_space[1]) is not None
    ):
        return colour_space[1]
    return None


def is_candidate(xobj) -> bool:
    """Whether this image's dictionary allows a JPEG re-encode at all."""
    import pikepdf

    try:
        colour_space = xobj.get("/ColorSpace")
        indexed = palette_base(colour_space) is not None
        bits = int(xobj.get("/BitsPerComponent", 0))
        if bits not in ((1, 2, 4, 8) if indexed else (8,)) or xobj.get("/ImageMask", False):
            return False
        if "/Decode" in xobj or isinstance(xobj.get("/Mask"), pikepdf.Array):
            return False
        if _LOSSY_FILTERS & set(_filters(xobj)):
            return False
        if int(xobj.Width) * int(xobj.Height) < MIN_PIXELS:
            return False
        return indexed or _components(colour_space) is not None
    except (AttributeError, TypeError, ValueError):
        return False


def _flat_share(gray) -> float:
    from PIL import ImageChops

    left = gray.crop((0, 0, gray.width - 1, gray.height))
    right = gray.crop((1, 0, gray.width, gray.height))
    hist = ImageChops.difference(left, right).histogram()
    return hist[0] / sum(hist)


def _encode(pil_img, quality: int, subsampling: int | None = None) -> bytes:
    options = {} if subsampling is None else {"subsampling": subsampling}
    buf = io.BytesIO()
    pil_img.save(buf, format="JPEG", quality=quality, optimize=True, **options)
    return buf.getvalue()


def _retry_qualities(quality: int) -> list[int]:
    return [quality, *range(quality // 5 * 5 + 5, MAX_QUALITY + 1, 5)]


def _searched_jpeg(pil_img, quality: int, palette: bool) -> bytes | None:
    """The smallest faithful JPEG above `quality`, 4:2:0 or 4:4:4, or None."""
    from PIL import Image

    def attempt(setting):
        try:
            encoded = _encode(pil_img, *setting)
        except OSError:  # Pillow's buffer overflows on some optimized near-lossless encodes
            return None
        decoded = Image.open(io.BytesIO(encoded)).convert(pil_img.mode)
        return encoded if faithful(pil_img, decoded, palette, structural=True) else None

    qualities = _retry_qualities(quality)
    chains = [[(q, 2) for q in qualities[1:]]]
    if pil_img.mode == "RGB":
        chains.append([(q, 0) for q in qualities])  # colour noise needs full-resolution chroma
    found = [r for r in (lowest_passing(chain, attempt) for chain in chains) if r is not None]
    return min(found, key=len, default=None)


def photo_jpeg(
    pil_img,
    quality: int,
    orig_size: int,
    max_ratio: float = MAX_RATIO,
    palette: bool = False,
    guard_fidelity: bool = True,
) -> bytes | None:
    """JPEG bytes if this looks like a photograph and JPEG pays off, else None.

    A `palette` image was quantized, so its colour count says nothing. Without
    `guard_fidelity`, the JPEG is kept at `quality` once it fits the budget,
    with no PSNR check and no retry at a higher quality.
    """
    from PIL import Image

    if pil_img.mode not in ("L", "RGB"):
        return None
    colours = min(MIN_COLOURS, pil_img.width * pil_img.height // 4)
    if not palette and pil_img.mode == "RGB" and pil_img.getcolors(colours) is not None:
        return None
    if _flat_share(pil_img.convert("L")) > MAX_FLAT:
        return None
    budget = orig_size * max_ratio
    encoded = _encode(pil_img, quality)
    if len(encoded) > budget:  # higher qualities are larger still
        return None
    if not guard_fidelity:
        return encoded
    if faithful(
        pil_img, Image.open(io.BytesIO(encoded)).convert(pil_img.mode), palette, structural=True
    ):
        return encoded
    searched = _searched_jpeg(pil_img, quality, palette)
    return searched if searched is not None and len(searched) <= budget else None


def _prepare(
    img: dict,
    seen: set,
    quality: int,
    max_ratio: float = MAX_RATIO,
    guard_fidelity: bool = True,
) -> tuple[_Payload, ImageContext] | None:
    import pikepdf

    from pdftl.utils.pikepdf_compatibility_utils import (
        as_pil_image_compat,
        image_extraction_errors,
    )

    xobj = img.get("xobj")
    if img.get("inline") or xobj is None or xobj.objgen in seen or not is_candidate(xobj):
        return None
    seen.add(xobj.objgen)
    base = palette_base(xobj.get("/ColorSpace"))
    try:
        pil_img = as_pil_image_compat(pikepdf.PdfImage(xobj))
        ensure_thread_safe(pil_img)
        if base is not None:
            pil_img = pil_img.convert("L" if _components(base) == 1 else "RGB")
    except (
        pikepdf.PdfError,
        pikepdf.DataDecodingError,
        ValueError,
        *image_extraction_errors(),
    ) as err:
        logger.debug("photos_to_jpeg: cannot decode an image on page %s: %s", img.get("page"), err)
        return None
    orig_size = get_orig_stream_size(xobj)
    ctx = ImageContext(
        xobj=xobj, smask_xobj=None, orig_size=orig_size, img_dict=img, page_num=img.get("page")
    )
    return _Payload(pil_img, orig_size, quality, max_ratio, base, guard_fidelity), ctx


def _worker(payload: _Payload) -> bytes | None:
    return photo_jpeg(
        payload.pil_img,
        payload.quality,
        payload.orig_size,
        payload.max_ratio,
        palette=payload.base is not None,
        guard_fidelity=payload.guard_fidelity,
    )


def _commit(ctx: ImageContext, encoded: bytes | None, payload: _Payload) -> bool:
    import pikepdf

    if encoded is None:
        return False
    ctx.xobj.write(encoded, filter=pikepdf.Name.DCTDecode)  # drops /DecodeParms
    if payload.base is not None:
        ctx.xobj.ColorSpace = payload.base
        ctx.xobj.BitsPerComponent = 8
    return True


def _parse_int(kv: dict, name: str, lo: int, hi: int | None, default: int | None):
    if name not in kv:
        return default
    try:
        val = int(kv[name])
        if val < lo or (hi is not None and val > hi):
            raise ValueError
    except ValueError as exc:
        limit = f"{lo}-{hi}" if hi is not None else f"at least {lo}"
        raise InvalidArgumentError(
            f"photos_to_jpeg: Invalid value for {name}: '{kv[name]}'. Must be an integer {limit}."
        ) from exc
    return val


def _parse_ratio(kv: dict) -> float:
    if "max_ratio" not in kv:
        return MAX_RATIO
    try:
        value = float(kv["max_ratio"])
        if not 0 < value <= 1:
            raise ValueError
    except ValueError as exc:
        raise InvalidArgumentError(
            f"photos_to_jpeg: Invalid value for max_ratio: '{kv['max_ratio']}'. "
            "Must be a number over 0 and at most 1."
        ) from exc
    return value


@register_operation(
    "photos_to_jpeg",
    tags=["in_place", "images", "optimization"],
    type="single input operation",
    desc="Re-encode losslessly stored photographs as JPEG",
    long_desc=_PHOTOS_TO_JPEG_LONG_DESC.replace("{memory}", IMAGE_MEMORY_HELP),
    usage=(
        "<input> photos_to_jpeg [<spec>...] [quality=<q>] [max_ratio=<x>] [threads=<n>] "
        "output <output>"
    ),
    examples=_PHOTOS_TO_JPEG_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def photos_to_jpeg(pdf, operation_args: list, guard_fidelity: bool = True) -> OpResult:
    """Re-encode the photographs among the targeted pages' images as JPEG.

    Without `guard_fidelity`, a re-encode is kept once it fits `max_ratio`,
    with no PSNR check or quality retry.
    """
    ensure_dependencies(
        feature_name="photos_to_jpeg", dependencies=["numpy"], extra_tag="photos-to-jpeg"
    )
    specs: list[str] = []
    kv = parse_keyval_list(
        operation_args or [],
        bare_tokens=specs,
        allowed_keys=["quality", "threads", "max_ratio"],
        context="photos_to_jpeg",
    )
    quality = _parse_int(kv, "quality", 1, 100, DEFAULT_QUALITY)
    threads = _parse_int(kv, "threads", 1, None, None)
    max_ratio = _parse_ratio(kv)
    num_pages = len(pdf.pages)
    pages = (
        sorted(page_numbers_matching_page_specs(specs, num_pages))
        if specs
        else list(range(1, num_pages + 1))
    )
    count = run_parallel_image_job(
        images=extract_pdf_images(pdf, pages),
        threads=threads,
        prepare_func=lambda img, seen: _prepare(img, seen, quality, max_ratio, guard_fidelity),
        worker_func=_worker,
        commit_func=_commit,
    )
    summary = f"Re-encoded {count} photograph(s) as JPEG"
    logger.info(summary)
    return OpResult(success=True, pdf=pdf, data={"images": count}, summary=summary)
