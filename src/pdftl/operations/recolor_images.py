# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/recolor_images.py

"""Operation to convert PDF bitmap images to grayscale."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.images import extract_pdf_images, convert_image_dict_to_grayscale
from pdftl.utils.keyval_parser import parse_keyval_list
from pdftl.utils.page_specs import page_numbers_matching_page_specs

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_RECOLOR_IMAGES_LONG_DESC = """
The `recolor_images` operation walks targeted page content streams to locate
bitmap image XObjects, permanently transforming their pixel data and
colorspace entries to grayscale (/DeviceGray).

Arguments:
  * `<specs>`: Optional page ranges to limit the operation.

  * `quality=<q>`: The JPEG compression quality (1-100) used when writing back
    originally lossy images. (Default: 75)
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


def _validate_quality(val_str: str) -> int:
    """Helper to validate quality boundaries."""
    try:
        val = int(val_str)
        if not (1 <= val <= 100):
            raise ValueError
        return val
    except ValueError as exc:
        raise InvalidArgumentError(
            f"recolor_images: Invalid value for quality: '{val_str}'. "
            "Must be an integer between 1 and 100."
        ) from exc


def _parse_args(args: list) -> tuple[int, list]:
    """Parses incoming arguments via the shared keyval_parser."""
    page_specs = []
    kv = parse_keyval_list(
        args or [],
        bare_tokens=page_specs,
        allowed_keys=["quality"],
        context="recolor_images",
    )

    quality = _validate_quality(kv["quality"]) if "quality" in kv else 75

    return quality, page_specs


@register_operation(
    "recolor_images",
    tags=["in_place", "images", "color"],
    type="single input operation",
    desc="Convert images to grayscale",
    long_desc=_RECOLOR_IMAGES_LONG_DESC,
    usage="<input> recolor_images [<spec>...] [quality=val] output <output>",
    examples=_RECOLOR_IMAGES_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def recolor_images(pdf, operation_args: list) -> OpResult:
    """Finds and grayscales color images on targeted pages."""
    quality, page_specs = _parse_args(operation_args)
    num_pages = len(pdf.pages)

    target_pages = (
        sorted(list(page_numbers_matching_page_specs(page_specs, num_pages)))
        if page_specs
        else list(range(1, num_pages + 1))
    )

    # Crawl target streams via the existing xobject-walking infrastructure
    images = extract_pdf_images(pdf, target_pages)
    seen_objgens = set()
    recolor_count = 0

    for img in images:
        xobj = img["xobj"]

        # Prevent redundant processing of shared assets
        if xobj.objgen in seen_objgens:
            continue
        seen_objgens.add(xobj.objgen)

        # Delegate mutation directly to the utility function
        if convert_image_dict_to_grayscale(img, quality):
            recolor_count += 1

    logger.info("Recolored %d image asset(s) to grayscale.", recolor_count)
    return OpResult(success=True, pdf=pdf)
