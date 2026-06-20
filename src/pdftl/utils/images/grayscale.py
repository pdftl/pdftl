# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/images/grayscale.py

"""Utilities for converting PDF image stream assets to grayscale layouts."""

from __future__ import annotations

import io
import logging
import zlib
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from pdftl.operations.helpers.image_processor import ImageContext, ensure_thread_safe
from pdftl.utils.images.selectors import extract_to_pil

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)


@dataclass
class RecolorPayload:
    """Encapsulates image elements passed safely across thread boundaries."""

    pil_img: Any
    fmt: str
    quality: int


@dataclass
class RecolorResult:
    """Contains raw compressed data produced off-thread."""

    compressed_bytes: bytes


def _is_eligible_for_recolor(xobj: pikepdf.Stream, seen_objgens: set[str]) -> bool:
    """Runs structural validation guards to determine if an image can be recolored."""
    if xobj.objgen in seen_objgens:
        return False
    seen_objgens.add(xobj.objgen)

    # 1. Structural Validation Guards (strict)
    if xobj.get("/ImageMask") or int(xobj.get("/BitsPerComponent", 8)) == 1:
        return False

    # 2. MUST have ColorSpace (strict rule from tests)
    if xobj.get("/ColorSpace") is None:
        return False

    return True


def _extract_and_stage_pil(xobj: pikepdf.Stream) -> Any | None:
    """Decodes the stream to a PIL image and applies thread-safety staging."""
    pil_img = extract_to_pil(xobj)
    if pil_img is None:
        logger.debug("Skipping recolor: could not decode pixel data for %s", xobj.objgen)
        return None
    if pil_img.mode in ("L", "1"):
        logger.debug("Skipping recolor: already grayscale (%s)", pil_img.mode)
        return None
    if "/ColorSpace" not in xobj:
        logger.debug("Skipping recolor: no /ColorSpace entry")
        return None
    # Lock the file pointer safely into memory to prevent thread access faults
    ensure_thread_safe(pil_img)
    return pil_img


def prepare_recolor_payload(
    img: dict, quality: int, seen_objgens: set[str]
) -> tuple[RecolorPayload, ImageContext] | None:
    """Evaluates an image dictionary on the main thread to confirm it is eligible

    for grayscale conversion, then extracts and safelines its PIL representation.
    """
    xobj = img["xobj"]

    if not _is_eligible_for_recolor(xobj, seen_objgens):
        return None

    pil_img = _extract_and_stage_pil(xobj)
    if pil_img is None:
        return None

    fmt = img.get("format", "dctdecode").lower()

    ctx = ImageContext(
        xobj=xobj,
        smask_xobj=None,
        orig_size=0,
        img_dict=img,
        page_num=img.get("page", 0),
    )

    return RecolorPayload(pil_img=pil_img, fmt=fmt, quality=quality), ctx


def worker_recolor_pixels(payload: RecolorPayload) -> RecolorResult:
    """Pure CPU-bound operation executed in a worker thread.

    Performs the color matrix conversion and compresses the resulting bytes.
    """
    try:
        gray_pil = payload.pil_img.convert("L")
    except ValueError as exc:
        raise RuntimeError("PIL failed downsampling image channels to monochrome.") from exc

    if payload.fmt in ("flatedecode", "png"):
        compressed_bytes = zlib.compress(gray_pil.tobytes(), level=6)
    else:
        output_io = io.BytesIO()
        gray_pil.save(output_io, format="JPEG", quality=payload.quality)
        compressed_bytes = output_io.getvalue()

    return RecolorResult(compressed_bytes=compressed_bytes)


def commit_recolored_stream(
    ctx: ImageContext, result: RecolorResult, payload: RecolorPayload
) -> bool:
    """Main thread callback to mutate the original PDF stream and update colorspace metadata."""
    import pikepdf

    logger = logging.getLogger(__name__)
    xobj = ctx.xobj

    try:
        # 6. Clean transparency artifacts
        if "/SMask" in xobj:
            smask = xobj["/SMask"]
            if isinstance(smask, pikepdf.Stream) and "/ColorSpace" in smask:
                smask.ColorSpace = pikepdf.Name("/DeviceGray")

        # Clean PDF layout tables using strict pikepdf.Name references
        for key_str in ("ColorSpace", "Intent", "DecodeParms"):
            name_key = pikepdf.Name(f"/{key_str}")
            if name_key in xobj:
                del xobj[name_key]

        xobj[pikepdf.Name("/ColorSpace")] = pikepdf.Name("/DeviceGray")

        # 7. Write grayscale stream
        if payload.fmt in ("flatedecode", "png"):
            xobj.write(result.compressed_bytes, filter=pikepdf.Name("/FlateDecode"))
            xobj.ColorSpace = pikepdf.Name("/DeviceGray")
        else:
            xobj.write(result.compressed_bytes, filter=pikepdf.Name("/DCTDecode"))

        return True

    except pikepdf.PdfError as exc:
        logger.error("Failed to commit recolored stream layout modifications: %s", exc)
        return False
