# src/pdftl/operations/helpers/image_processor.py

from __future__ import annotations

import io
import logging
import os
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
    import pikepdf

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
            try:
                result = future.result()
                if commit_func(ctx, result, payload):
                    success_count += 1
            except (
                pikepdf.PdfError,
                ValueError,
                TypeError,
                OSError,
                RuntimeError,
                zlib.error,
            ) as e:
                logger.debug(
                    "Page %s: Failed to process image %s: %s",
                    ctx.page_num,
                    ctx.img_dict.get("name", "?"),
                    e,
                )

    return success_count
