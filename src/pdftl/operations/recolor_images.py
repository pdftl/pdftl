# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/recolor_images.py

"""Operation to convert PDF bitmap images to grayscale using parallel execution."""

from __future__ import annotations

import logging

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.helpers.image_processor import run_parallel_image_job
from pdftl.utils.images.finders import extract_pdf_images
from pdftl.utils.images.grayscale import (
    prepare_recolor_payload,
    worker_recolor_pixels,
    commit_recolored_stream,
)
from pdftl.utils.keyval_parser import parse_keyval_list
from pdftl.utils.page_specs import page_numbers_matching_page_specs

logger = logging.getLogger(__name__)

_RECOLOR_IMAGES_LONG_DESC = """
The `recolor_images` operation walks targeted page content streams to locate
bitmap image XObjects, permanently transforming their pixel data and
colorspace entries to grayscale (/DeviceGray) using parallel processing.

Arguments:
  * `<specs>`: Optional page ranges to limit the operation.

  * `quality=<q>`: The JPEG compression quality (1-100) used when writing back originally lossy
    images. (Default: 75)

  * `threads=<n>`: Number of parallel worker threads to use for image processing. (Default:
    system CPU count)

 """

_RECOLOR_IMAGES_EXAMPLES = [
    {
        "cmd": "in.pdf recolor_images output out.pdf",
        "desc": "Convert all bitmap images to grayscale document-wide.",
    },
    {
        "cmd": "in.pdf recolor_images 1-5 quality=85 output out.pdf",
        "desc": "Convert images on pages 1-5 to grayscale using high-quality JPEG settings.",
    },
]


def _validate_int(val_str: str, name: str, min_val: int = 1, max_val: int | None = None) -> int:
    """Helper to validate integer boundaries for arguments."""
    try:
        val = int(val_str)
        if val < min_val or (max_val is not None and val > max_val):
            raise ValueError
        return val
    except ValueError as exc:
        limit_str = f"between {min_val} and {max_val}" if max_val else f"at least {min_val}"
        raise InvalidArgumentError(
            f"recolor_images: Invalid value for {name}: '{val_str}'. "
            f"Must be an integer {limit_str}."
        ) from exc


def _parse_args(args: list) -> tuple[int, int | None, list]:
    """Parses incoming arguments via the shared keyval_parser."""
    page_specs = []
    kv = parse_keyval_list(
        args or [],
        bare_tokens=page_specs,
        allowed_keys=["quality", "threads"],
        context="recolor_images",
    )

    quality = _validate_int(kv["quality"], "quality", 1, 100) if "quality" in kv else 75
    threads = _validate_int(kv["threads"], "threads", 1) if "threads" in kv else None

    return quality, threads, page_specs


@register_operation(
    "recolor_images",
    tags=["in_place", "images", "color"],
    type="single input operation",
    desc="Convert images to grayscale",
    long_desc=_RECOLOR_IMAGES_LONG_DESC,
    usage="<input> recolor_images [<spec>...] [quality=val] [threads=val] output <output>",
    examples=_RECOLOR_IMAGES_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def recolor_images(pdf, operation_args: list) -> OpResult:
    """Finds and grayscales color images on targeted pages using parallel tasks."""
    quality, threads, page_specs = _parse_args(operation_args)
    num_pages = len(pdf.pages)

    target_pages = (
        sorted(list(page_numbers_matching_page_specs(page_specs, num_pages)))
        if page_specs
        else list(range(1, num_pages + 1))
    )

    images = extract_pdf_images(pdf, target_pages)

    recolor_count = run_parallel_image_job(
        images=images,
        threads=threads,
        prepare_func=lambda img, seen: prepare_recolor_payload(img, quality, seen),
        worker_func=worker_recolor_pixels,
        commit_func=commit_recolored_stream,
    )

    logger.info("Recolored %d image asset(s) to grayscale.", recolor_count)
    return OpResult(success=True, pdf=pdf)
