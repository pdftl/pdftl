# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/deduplicate_images.py

"""Merge duplicate image XObjects that share identical content into a
single shared copy, shrinking the file without touching visual output.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.core_types import HelpExample, OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.helpers.dedupe_images_core import deduplicate_image_xobjects
from pdftl.utils.keyval_parser import parse_keyval_list

if TYPE_CHECKING:
    from pikepdf import Pdf

logger = logging.getLogger(__name__)

_DEDUPLICATE_IMAGES_LONG_DESC = """
The `deduplicate_images` operation finds image XObjects with identical content
-- even if they are stored as separate, distinct PDF objects -- and merges them
into a single shared copy, rewriting every reference in the document to point
at it. This can significantly shrink a PDF assembled or edited by tools that
re-embed the same image (a logo, a background, a repeated stamp) once per use
rather than sharing a single copy.

### How it works

Two image XObjects are considered duplicates if they are structurally
equivalent per PDF Annex J: their dictionaries have the same keys with
equivalent values (recursively, so an identical `/SMask` referenced by both
images still counts as equivalent even if it's stored as two separate
objects), and their raw, undecoded stream bytes are identical. Only image
XObjects are considered -- fonts, form XObjects, annotation appearances, and
page objects are left untouched.

This is an **in-place, lossless** operation: no image is re-encoded,
downsampled, or otherwise modified. It only removes redundant *copies*.

### Parameters

* `min_bytes=<n>` (default: 0) -- Skip images smaller than this many bytes.
  Comparing (and later saving) very small images usually isn't worth the
  overhead. Accepts a plain byte count or a size with a `KB`/`MB`/`GB`
  suffix (e.g. `min_bytes=64KB`).
"""

_DEDUPLICATE_IMAGES_EXAMPLES = [
    HelpExample(
        desc="Merge all duplicate images in the document.",
        cmd="in.pdf deduplicate_images output out.pdf",
    ),
    HelpExample(
        desc="Only consider images of at least 64KB for deduplication.",
        cmd="in.pdf deduplicate_images min_bytes=64KB output out.pdf",
    ),
]

_SIZE_SUFFIXES = {
    "": 1,
    "B": 1,
    "KB": 1024,
    "MB": 1024**2,
    "GB": 1024**3,
}

_SIZE_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]*)\s*$")


def _parse_byte_size(raw: str) -> int:
    """Parses a plain byte count or a `<number><KB|MB|GB>` size string.

    NOTE: this is a small local parser, not a shared codebase utility --
    I couldn't confirm whether pdftl already has a canonical
    string-to-bytes size parser elsewhere (the `usage` operation's
    `_human_bytes` only goes the other direction: bytes to display
    string). If one already exists, prefer it over this and delete this
    function instead of maintaining two.
    """
    match = _SIZE_RE.match(raw)
    if not match:
        raise InvalidArgumentError(
            f"'deduplicate_images': invalid min_bytes value '{raw}'. "
            "Expected a byte count or a size like '64KB'."
        )
    number_str, suffix = match.groups()
    suffix = suffix.upper()
    if suffix not in _SIZE_SUFFIXES:
        raise InvalidArgumentError(
            f"'deduplicate_images': invalid size suffix '{suffix}' in min_bytes='{raw}'. "
            f"Expected one of: {', '.join(s for s in _SIZE_SUFFIXES if s)}."
        )
    return int(float(number_str) * _SIZE_SUFFIXES[suffix])


def _parse_deduplicate_images_args(args: list[str]) -> int:
    """Parses `deduplicate_images` keyword arguments, returning min_bytes."""
    parsed = parse_keyval_list(
        args,
        allowed_keys=["min_bytes"],
        context="deduplicate_images",
    )
    min_bytes_raw = parsed.get("min_bytes")
    if min_bytes_raw is None:
        return 0
    return _parse_byte_size(min_bytes_raw)


@register_operation(
    "deduplicate_images",
    tags=["in_place", "images", "optimize"],
    type="single input operation",
    desc="Merge duplicate image XObjects into a single shared copy",
    long_desc=_DEDUPLICATE_IMAGES_LONG_DESC,
    usage="<input> deduplicate_images [min_bytes=<size>] output <output>",
    examples=_DEDUPLICATE_IMAGES_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def deduplicate_images(pdf: Pdf, args: list[str]) -> OpResult:
    """Merge structurally-equivalent image XObjects, in place."""
    min_bytes = _parse_deduplicate_images_args(args or [])

    result = deduplicate_image_xobjects(pdf, threshold=min_bytes)

    if result["merged"]:
        logger.info(
            "deduplicate_images: merged %d duplicate image XObject(s), "
            "saving approximately %d bytes of stream data.",
            result["merged"],
            result["bytes_saved"],
        )
    else:
        logger.info("deduplicate_images: no duplicate image XObjects found.")

    return OpResult(success=True, pdf=pdf)
