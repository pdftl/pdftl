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


def encode_and_update_pdf_image(ctx: Any, pil_img: Any, quality: int) -> None:
    """Encodes a PIL image and updates the PDF XObject stream by matching the
    original image's specific PDF filter and structural characteristics exactly.

    Execution Routing Architecture:
    ─────────────────────────────────────────────────────────────────────────────
    Is Filter /DCTDecode?  ───> YES (and mode != "1") ───> Encode as JPEG
           │
           ▼ NO
    Is Filter /JPXDecode?  ───> YES ───> Has OpenJPEG? ───> YES ───> Encode JPEG2000
           │                                                 │
           ▼ NO                                              ▼ NO (Catch/Fallback)
    Is Filter /CCITTFax?   ───> YES ───> Is 1-Bit? ────> YES ───> Hack TIFF/CCITT
           │                               │
           ▼ NO                            ▼ NO (Catch/Fallback)
           └───────────> [ FALLBACK TO /FLATEDECODE ] <───────────────────────┘
                         (Guarantees 100% lossless output safety for everything else)
    ─────────────────────────────────────────────────────────────────────────────
    """
    import pikepdf
    from pikepdf import Name
    from PIL import Image

    # 1. Update basic structural dimensions
    ctx.xobj.Width = pil_img.width
    ctx.xobj.Height = pil_img.height

    # 2. Synchronize ColorSpace & BitsPerComponent
    mode_map = {
        "1": (Name("/DeviceGray"), 1),
        "L": (Name("/DeviceGray"), 8),
        "RGB": (Name("/DeviceRGB"), 8),
        "CMYK": (Name("/DeviceCMYK"), 8),
    }
    logger.debug("pik_img.mode=%s", pil_img.mode)
    if pil_img.mode in mode_map:
        cs, bpc = mode_map[pil_img.mode]
        ctx.xobj.ColorSpace = cs
        ctx.xobj.BitsPerComponent = bpc
    elif pil_img.mode == "P":
        # Extract the flat RGB palette list [r, g, b, r, g, b...]
        palette_data = pil_img.getpalette() or []

        # Calculate the highest valid index (e.g., 255 for a 256-color palette)
        max_index = max(0, (len(palette_data) // 3) - 1)

        # Build the exact /Indexed array PDF requires
        ctx.xobj.ColorSpace = pikepdf.Array(
            [
                Name("/Indexed"),
                Name("/DeviceRGB"),
                max_index,
                bytes(palette_data),  # pikepdf natively translates bytes to PDF string/hex
            ]
        )
        ctx.xobj.BitsPerComponent = 8
        logger.debug("mode P, ctx=%s", ctx)

    # 3. Read the original filter schema
    filters = ctx.xobj.get("/Filter")
    filter_list = []
    if isinstance(filters, pikepdf.Name):
        filter_list = [str(filters)]
    elif isinstance(filters, pikepdf.Array):
        filter_list = [str(f) for f in filters]

    # 4. Route serialization with precise exception boundaries
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


def _get_ccitt_bytes(pil_img, invert=False):
    img = pil_img
    if invert:
        from PIL import ImageOps

        img = ImageOps.invert(pil_img)

    # Strategy B: Generate CCITT Group 4 Payload
    # Highly efficient for crisp text, line art, and solid geometric paths
    ccitt_buf = io.BytesIO()
    img.save(
        ccitt_buf,
        format="TIFF",
        compression="group4",
        tiffinfo={278: img.height},  # Tag 278: RowsPerStrip
    )
    return _extract_raw_ccitt_from_tiff(ccitt_buf.getvalue())


def _needs_inversion(pil_img):
    dominant_color_value = max(pil_img.getcolors(2), key=lambda x: x[0])[1]
    # In Pillow mode '1', 0 is Black, 255 (or 1) is White
    # If the dominant color is White, we WANT to invert it before encoding
    # so that libtiff applies the shorter 0-bit Huffman codes to the massive background.
    return dominant_color_value != 0


def _handle_1bit_optimized_encode(ctx: Any, pil_img: Any) -> None:
    import pikepdf
    from pikepdf import Name

    raw_pixel_bytes = pil_img.tobytes()
    raw_size = len(raw_pixel_bytes)

    # 1. Determine optimal polarity and get CCITT bytes
    invert = _needs_inversion(pil_img)
    ccitt_bytes = _get_ccitt_bytes(pil_img, invert=invert)
    ccitt_size = len(ccitt_bytes)

    # 2. The Short-Circuit Heuristic
    # If CCITT compresses the image to less than 25% of its raw size, it's a clean
    # line-art/text page. Flate is extremely unlikely to beat this, so skip it.
    best_bytes, best_filter = ccitt_bytes, "/CCITTFaxDecode"
    if ccitt_size < (raw_size * 0.25):
        logger.debug(
            "1-bit compression: CCITT G4 ratio is excellent (%.2f%%). Skipping Flate.",
            (ccitt_size / raw_size) * 100,
        )
    else:
        # The compression ratio is suspicious (noisy or dithered). Bring in Flate.
        import zlib

        flate_bytes = zlib.compress(raw_pixel_bytes, level=9)
        if len(flate_bytes) < ccitt_size:
            best_bytes, best_filter = flate_bytes, "/FlateDecode"
            logger.debug(
                "1-bit compression: Flate wins (%d vs %d bytes)", len(flate_bytes), ccitt_size
            )
        else:
            logger.debug(
                "1-bit compression: CCITT G4 barely wins (%d vs %d bytes)",
                ccitt_size,
                len(flate_bytes),
            )

    # 3. Write the winning payload to the PDF
    ctx.xobj.write(best_bytes, filter=Name(best_filter))

    if best_filter == "/CCITTFaxDecode":
        ctx.xobj.DecodeParms = pikepdf.Dictionary(
            K=-1, Columns=pil_img.width, Rows=pil_img.height, BlackIs1=not invert
        )
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


def _extract_raw_ccitt_from_tiff(tiff_bytes: bytes) -> bytes:
    """Isolates the raw CCITT fax bitstream data out of a standard TIFF byte array
    by safely concatenating multiple data strips.
    """
    if len(tiff_bytes) < 8:
        return tiff_bytes

    endian = "<" if tiff_bytes[:2] == b"II" else ">"
    magic = struct.unpack_from(f"{endian}H", tiff_bytes, 2)[0]
    if magic != 42:
        return tiff_bytes

    strip_offsets, strip_byte_counts = _get_tiff_strip_parameters(tiff_bytes, endian)
    if not strip_offsets or not strip_byte_counts:
        return tiff_bytes

    # Concatenate all horizontal strips into one continuous bitstream
    return b"".join(
        tiff_bytes[off : off + length] for off, length in zip(strip_offsets, strip_byte_counts)
    )


def _get_tiff_strip_parameters(tiff_bytes, endian):
    ifd_offset = struct.unpack_from(f"{endian}I", tiff_bytes, 4)[0]
    num_entries = struct.unpack_from(f"{endian}H", tiff_bytes, ifd_offset)[0]
    curr_pos = ifd_offset + 2
    strip_offsets = []
    strip_byte_counts = []

    for _ in range(num_entries):
        tag, tag_type, count, val_or_offset = struct.unpack_from(
            f"{endian}HHII", tiff_bytes, curr_pos
        )
        curr_pos += 12

        if tag not in (273, 279):
            continue

        # Type 3 is SHORT (2 bytes), Type 4 is LONG (4 bytes)
        fmt_char = "I" if tag_type == 4 else "H"
        type_size = 4 if tag_type == 4 else 2

        values = []
        if type_size * count <= 4:
            # Values are packed directly in the 4-byte val_or_offset field
            raw_4 = struct.pack(f"{endian}I", val_or_offset)
            values = list(struct.unpack(f"{endian}{count}{fmt_char}", raw_4[: type_size * count]))
        else:
            # val_or_offset is a pointer to the array; read 'count' values
            values = list(
                struct.unpack_from(f"{endian}{count}{fmt_char}", tiff_bytes, val_or_offset)
            )

        if tag == 273:
            strip_offsets = values
        elif tag == 279:
            strip_byte_counts = values

    return strip_offsets, strip_byte_counts
