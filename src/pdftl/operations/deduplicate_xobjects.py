# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/deduplicate_xobjects.py

"""Merge duplicate form XObjects drawn from page content into a single shared copy."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.core_types import HelpExample, OpResult
from pdftl.core.registry import register_operation
from pdftl.operations.helpers.dedupe_xobjects_core import deduplicate_form_xobjects
from pdftl.utils.arg_helpers import parse_size_to_bytes
from pdftl.utils.keyval_parser import parse_keyval_list

if TYPE_CHECKING:
    from pikepdf import Pdf

logger = logging.getLogger(__name__)

_DEDUPLICATE_XOBJECTS_LONG_DESC = """
The `deduplicate_xobjects` operation finds form XObjects (reusable pieces of page
content such as a letterhead, a logo or a repeated diagram) that are stored as
separate but identical objects, and merges them into a single shared copy,
rewriting every reference to point at it. PDFs assembled from many smaller
PDFs often carry one copy per source page.

### What is merged

Two XObjects are merged only if their stream dictionaries are equivalent,
`/Resources` and `/Group` included, and their raw stream bytes are identical.
The obsolete `/Name` key, which readers ignore, is left out of the comparison.

Only form XObjects drawn with `Do` from page content or from other such
XObjects, and link appearances, are considered. An XObject is left alone if
it is used anywhere else: as the appearance of any other annotation
(interactive form fields especially, whose appearances viewers regenerate),
inside one, as a soft mask, from a pattern or a Type3 glyph, or from the
structure tree. XObjects with `/StructParent` or `/StructParents` are left
alone too, and image XObjects are left to `deduplicate_images`.

This is an **in-place, lossless** operation: nothing is re-encoded, and pages
render exactly as before.

### Parameters

* `min_bytes=<n>` (default: 0) -- Skip XObjects whose stream is smaller than this
  many bytes. Accepts a plain byte count or a size with a `KB`/`MB`/`GB`
  suffix (e.g. `min_bytes=4KB`).
"""

_DEDUPLICATE_XOBJECTS_EXAMPLES = [
    HelpExample(
        desc="Merge all duplicate drawn form XObjects in the document.",
        cmd="in.pdf deduplicate_xobjects output out.pdf",
    ),
    HelpExample(
        desc="Only consider XObjects of at least 4KB.",
        cmd="in.pdf deduplicate_xobjects min_bytes=4KB output out.pdf",
    ),
]


def _parse_deduplicate_xobjects_args(args: list[str]) -> int:
    """Return min_bytes from `deduplicate_xobjects` keyword arguments."""
    parsed = parse_keyval_list(args, allowed_keys=["min_bytes"], context="deduplicate_xobjects")
    min_bytes_raw = parsed.get("min_bytes")
    if min_bytes_raw is None:
        return 0
    return parse_size_to_bytes(min_bytes_raw, context="deduplicate_xobjects: min_bytes")


@register_operation(
    "deduplicate_xobjects",
    tags=["in_place", "optimize"],
    type="single input operation",
    desc="Merge duplicate drawn form XObjects into a single shared copy",
    long_desc=_DEDUPLICATE_XOBJECTS_LONG_DESC,
    usage="<input> deduplicate_xobjects [min_bytes=<size>] output <output>",
    examples=_DEDUPLICATE_XOBJECTS_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def deduplicate_xobjects(pdf: Pdf, args: list[str]) -> OpResult:
    """Merge equivalent drawn-only form XObjects, in place."""
    min_bytes = _parse_deduplicate_xobjects_args(args or [])
    result = deduplicate_form_xobjects(pdf, threshold=min_bytes)
    if result["merged"]:
        logger.info(
            "deduplicate_xobjects: merged %d duplicate form XObject(s), "
            "saving approximately %d bytes of stream data.",
            result["merged"],
            result["bytes_saved"],
        )
    else:
        logger.info("deduplicate_xobjects: no duplicate drawn form XObjects found.")
    return OpResult(success=True, pdf=pdf)
