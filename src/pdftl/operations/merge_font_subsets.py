# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/merge_font_subsets.py

"""Merge embedded subsets of the same TrueType font into one program."""

from __future__ import annotations

import logging

import pdftl.core.constants as c
from pdftl.core.core_types import HelpExample, OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError

logger = logging.getLogger(__name__)

_LONG_DESC = """
The `merge_font_subsets` operation merges the separate subsets of one
font that some producers embed (browsers and office suites often write a
subset per page) into a single font program holding all their glyphs,
with each glyph stored once.

It applies to TrueType fonts. CID-keyed fonts drawn through `Identity-H`
or `Identity-V` keep their own character codes, widths and `ToUnicode`,
and a `CIDToGIDMap` points their codes into the shared program. Simple
TrueType fonts find glyphs through the program's own character map, so
their subsets are merged only if every character code draws the same
glyph in all of them (and glyph names, where present, agree). Page
content is not touched and pages render exactly as before. Subsets are
merged only if their programs agree on everything but the glyphs (units
per em and hinting programs), and only if the result is smaller.
"""

_EXAMPLES = [
    HelpExample(
        desc="Merge the per-page font subsets of a browser-printed PDF.",
        cmd="in.pdf merge_font_subsets output out.pdf",
    ),
]


@register_operation(
    "merge_font_subsets",
    tags=["in_place", "fonts", "optimization"],
    type="single input operation",
    desc="Merge embedded subsets of the same font into one",
    long_desc=_LONG_DESC,
    usage="<input> merge_font_subsets output <output>",
    examples=_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def merge_font_subsets_op(pdf, operation_args: list) -> OpResult:
    """Merge CID-keyed TrueType subsets of the same font into shared programs."""
    from pdftl.fonts.merge_subsets import merge_font_subsets

    if operation_args:
        raise InvalidArgumentError(
            f"merge_font_subsets: takes no arguments, got '{' '.join(operation_args)}'"
        )
    stats = merge_font_subsets(pdf)
    summary = (
        f"Merged {stats.programs_merged} font subset(s) into {stats.groups} program(s), "
        f"saving about {stats.bytes_saved} bytes"
    )
    logger.info(summary)
    return OpResult(
        success=True,
        pdf=pdf,
        data={
            "groups": stats.groups,
            "programs_merged": stats.programs_merged,
            "bytes_saved": stats.bytes_saved,
        },
        summary=summary,
    )
