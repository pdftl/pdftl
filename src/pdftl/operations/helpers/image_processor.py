# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/helpers/image_processor.py

from __future__ import annotations

import io
import logging
import os
import struct
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, TypeVar
from collections.abc import Callable

logger = logging.getLogger(__name__)

TPayload = TypeVar("TPayload")
TResult = TypeVar("TResult")


@dataclass
class ImageContext:
    """Main thread context keeping track of pikepdf objects for a given task."""

    xobj: Any
    smask_xobj: Any | None
    orig_size: int
    img_dict: dict
    page_num: int


def ensure_thread_safe(pil_img: Any) -> None:
    """Forces an image to load on the main thread if backed by an unsafe file pointer."""
    if not hasattr(pil_img, "fp") or pil_img.fp is None:
        return

    if not isinstance(pil_img.fp, io.BytesIO):
        logger.debug("Forcing image decode on main thread for pil_img: %s", pil_img)
        pil_img.load()


def get_orig_stream_size(stream_obj: Any) -> int:
    """Returns the compressed stream size in bytes, as stored in the PDF."""
    import pikepdf

    try:
        return len(stream_obj.read_raw_bytes())
    except (pikepdf.PdfError, AttributeError):
        return 999_999_999


def run_parallel_image_job(
    images: list[dict],
    threads: int | None,
    prepare_func: Callable[[dict, set], tuple[TPayload, ImageContext] | None],
    worker_func: Callable[[TPayload], TResult],
    commit_func: Callable[[ImageContext, TResult, TPayload], bool],
) -> int:
    """Orchestrator for parallel PDF image extraction, computation, and mutation."""

    if not threads or threads < 1:
        threads = os.cpu_count() or 4

    seen_objgens: set[str] = set()
    success_count = 0
    future_to_task = {}

    with ThreadPoolExecutor(max_workers=threads) as executor:
        for img in images:
            task = prepare_func(img, seen_objgens)
            if task is not None:
                payload, ctx = task
                future = executor.submit(worker_func, payload)
                future_to_task[future] = (payload, ctx)

        for future in as_completed(future_to_task):
            payload, ctx = future_to_task[future]
            result = future.result()
            if commit_func(ctx, result, payload):
                success_count += 1

    return success_count


# --- REUSABLE BOILERPLATE BOOTSTRAPS ---


def encode_and_update_pdf_image(
    ctx: Any, pil_img: Any, quality: int, forced_codec: str | None = None
) -> None:
    """Encodes a PIL image and updates the PDF XObject stream by matching the
    original image's specific PDF filter and structural characteristics exactly.

    TODO: Future Enhancement - Lossy Passthrough
    Consider adding an optional `original_bytes: bytes | None = None` parameter.
    If provided (and the caller determines the image was ultimately unmodified),
    we can inject these raw bytes directly for /DCTDecode and /JPXDecode to
    completely bypass PIL's re-encoding step and avoid generation loss. Alternatively,
    enforce that callers completely skip calling this function for unmodified images.

    This function routes the encoding process based on the image's
    properties: it intercepts 1-bit images for optimal CCITT/Flate compression,
    attempts to maintain lossy /DCTDecode or /JPXDecode where applicable, and
    safely falls back to lossless /FlateDecode for everything else.
    """
    from PIL import Image
    from pdftl.utils.images.pil_to_pdf import get_colorspace_dict

    # 1a. Quantize BEFORE colorspace/bpc are derived, so ColorSpace correctly
    # reflects the resulting Indexed palette rather than the pre-quantization mode.
    if forced_codec == "png8" and pil_img.mode != "1":
        pil_img = pil_img.convert("P", palette=Image.ADAPTIVE)

    # 1. Update basic structural dimensions
    ctx.xobj.Width = pil_img.width
    ctx.xobj.Height = pil_img.height

    # 2. Synchronize ColorSpace & BitsPerComponent
    cs, bpc = get_colorspace_dict(pil_img)
    ctx.xobj.ColorSpace = cs
    ctx.xobj.BitsPerComponent = bpc

    # 3. Try an explicit override first; otherwise fall through to the
    # original mode/filter-based heuristic.
    if _try_forced_codec(ctx, pil_img, quality, forced_codec):
        return

    _encode_via_heuristic(ctx, pil_img, quality)


def _try_forced_codec(ctx: Any, pil_img: Any, quality: int, forced_codec: str | None) -> bool:
    """Applies an explicit format= override if one applies to this image.
    Returns True if encoding was handled, False to fall through to the
    default heuristic."""
    if forced_codec == "png":
        _handle_flate_fallback(ctx, pil_img)
        return True
    if forced_codec == "png8" and pil_img.mode == "P":
        _handle_flate_fallback(ctx, pil_img)
        return True
    if forced_codec == "jpeg" and pil_img.mode != "1":
        _handle_dct_encode(ctx, pil_img, quality)
        return True
    return False


def _encode_via_heuristic(ctx: Any, pil_img: Any, quality: int) -> None:
    """Original mode/filter-based routing: matches the incoming PDF filter
    where possible, falls back to lossless Flate otherwise."""
    import pikepdf
    from PIL import Image

    filters = ctx.xobj.get("/Filter")
    filter_list = []
    if isinstance(filters, pikepdf.Name):
        filter_list = [str(filters)]
    elif isinstance(filters, pikepdf.Array):
        filter_list = [str(f) for f in filters]

    if pil_img.mode == "1":
        try:
            _handle_1bit_optimized_encode(ctx, pil_img)
            return
        except (OSError, struct.error) as err:
            logger.debug("1-bit optimized encoding failed, falling back to Flate: %s", err)

    if "/DCTDecode" in filter_list and pil_img.mode != "1":
        _handle_dct_encode(ctx, pil_img, quality)
        return

    if "/JPXDecode" in filter_list and "JPEG2000" in Image.SAVE:
        try:
            _handle_jpx_encode(ctx, pil_img)
            return
        except OSError as err:
            logger.debug("JPX target encoding failed, falling back to Flate: %s", err)

    # Clean fallback default for FlateDecode, LZWDecode, JBIG2, or changed pixel spaces
    _handle_flate_fallback(ctx, pil_img)


# --- FLATTENED ENCODING COMPARTMENTS ---


def _handle_1bit_optimized_encode(ctx: Any, pil_img: Any) -> None:
    from pikepdf import Name
    from pdftl.utils.images.pil_to_pdf import get_optimal_1bit_payload

    best_bytes, best_filter, decode_parms = get_optimal_1bit_payload(pil_img)

    # 3. Write the winning payload to the PDF
    ctx.xobj.write(best_bytes, filter=Name(best_filter))

    if decode_parms is not None:
        ctx.xobj.DecodeParms = decode_parms
    else:
        # Clean up DecodeParms if Flate won and previously had CCITT params
        if "/DecodeParms" in ctx.xobj:
            del ctx.xobj["/DecodeParms"]


def _handle_dct_encode(ctx: Any, pil_img: Any, quality: int) -> None:
    import io
    from pikepdf import Name

    out = io.BytesIO()
    pil_img.save(out, format="JPEG", quality=quality, optimize=True)
    ctx.xobj.write(out.getvalue(), filter=Name("/DCTDecode"))
    if "/DecodeParms" in ctx.xobj:
        del ctx.xobj["/DecodeParms"]


def _handle_jpx_encode(ctx: Any, pil_img: Any) -> None:
    import io
    from pikepdf import Name

    out = io.BytesIO()
    pil_img.save(out, format="JPEG2000")
    ctx.xobj.write(out.getvalue(), filter=Name("/JPXDecode"))
    if "/DecodeParms" in ctx.xobj:
        del ctx.xobj["/DecodeParms"]


def _handle_flate_fallback(ctx: Any, pil_img: Any) -> None:
    from pikepdf import Name

    # Pull unadorned sequential raster lines right from the pixel buffer
    raw_bytes = pil_img.tobytes()
    compressed_bytes = zlib.compress(raw_bytes, level=9)

    ctx.xobj.write(compressed_bytes, filter=Name("/FlateDecode"))
    if "/DecodeParms" in ctx.xobj:
        del ctx.xobj["/DecodeParms"]
