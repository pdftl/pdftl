# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/deduplicate_icc_profiles.py

"""Merge duplicate embedded ICC color-profile streams into a single
shared copy, shrinking the file without touching visual output.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.core_types import HelpExample, OpResult
from pdftl.core.registry import register_operation
from pdftl.operations.helpers.dedupe_icc_core import deduplicate_icc_profiles as _dedupe_icc
from pdftl.utils.arg_helpers import parse_size_to_bytes
from pdftl.utils.keyval_parser import parse_keyval_list

if TYPE_CHECKING:
    from pikepdf import Pdf

logger = logging.getLogger(__name__)

_DEDUPLICATE_ICC_PROFILES_LONG_DESC = """
The `deduplicate_icc_profiles` operation finds embedded ICC color-profile
streams (`/ICCBased`) with identical content -- even if stored as separate,
distinct PDF objects -- and merges them into a single shared copy, rewriting
every reference in the document to point at it.

ICC profiles are looked up wherever a `/ColorSpace` entry can appear: an
image XObject's own color space, and named color spaces in a `/Resources
/ColorSpace` dictionary. `/Separation` and `/DeviceN` color spaces are also
checked, since their alternate color space can itself be ICC-based.

Document-level output intents are not considered, since a single document
realistically has at most one or two and there is nothing to deduplicate
against within the same file.

Two profiles are only merged if their stream dictionary and raw bytes are
both fully identical -- a profile with an `/Alternate` fallback color space
will not be merged with an otherwise-identical one that lacks it.

This is most useful on PDFs assembled by concatenating other PDFs, where the
same color profile (e.g. a shared corporate CMYK or sRGB profile) is often
embedded once per source document or per image.

### Parameters

* `min_bytes=<n>` (default: 0) -- Skip profiles smaller than this many bytes.
  Accepts a plain byte count or a size with a `KB`/`MB`/`GB` suffix (e.g.
  `min_bytes=64KB`).
"""

_DEDUPLICATE_ICC_PROFILES_EXAMPLES = [
    HelpExample(
        desc="Merge all duplicate ICC profiles in the document.",
        cmd="in.pdf deduplicate_icc_profiles output out.pdf",
    ),
    HelpExample(
        desc="Only consider profiles of at least 64KB for deduplication.",
        cmd="in.pdf deduplicate_icc_profiles min_bytes=64KB output out.pdf",
    ),
]


def _parse_deduplicate_icc_profiles_args(args: list[str]) -> int:
    """Parses `deduplicate_icc_profiles` keyword arguments, returning min_bytes."""
    parsed = parse_keyval_list(
        args,
        allowed_keys=["min_bytes"],
        context="deduplicate_icc_profiles",
    )
    min_bytes_raw = parsed.get("min_bytes")
    if min_bytes_raw is None:
        return 0
    return parse_size_to_bytes(min_bytes_raw, context="deduplicate_icc_profiles: min_bytes")


@register_operation(
    "deduplicate_icc_profiles",
    tags=["in_place", "images", "optimize"],
    type="single input operation",
    desc="Merge duplicate embedded ICC color profiles into a single shared copy",
    long_desc=_DEDUPLICATE_ICC_PROFILES_LONG_DESC,
    usage="<input> deduplicate_icc_profiles [min_bytes=<size>] output <output>",
    examples=_DEDUPLICATE_ICC_PROFILES_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def deduplicate_icc_profiles(pdf: Pdf, args: list[str]) -> OpResult:
    """Merge structurally-equivalent ICC profile streams, in place."""
    min_bytes = _parse_deduplicate_icc_profiles_args(args or [])

    result = _dedupe_icc(pdf, threshold=min_bytes)

    if result["merged"]:
        logger.info(
            "deduplicate_icc_profiles: merged %d duplicate ICC profile(s), "
            "saving approximately %d bytes of stream data.",
            result["merged"],
            result["bytes_saved"],
        )
    else:
        logger.info("deduplicate_icc_profiles: no duplicate ICC profiles found.")

    return OpResult(success=True, pdf=pdf)
