# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/deduplicate_fonts.py

"""Merge duplicate fonts -- whole /Font dictionaries first, then any
remaining duplicate embedded font-program streams -- into shared
copies, shrinking the file without touching visual output.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.core_types import HelpExample, OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.helpers.dedupe_fonts_core import (
    deduplicate_font_dicts,
    deduplicate_font_files,
)
from pdftl.utils.arg_helpers import parse_size_to_bytes
from pdftl.utils.keyval_parser import parse_keyval_list

if TYPE_CHECKING:
    from pikepdf import Pdf

logger = logging.getLogger(__name__)

_DEDUPLICATE_FONTS_LONG_DESC = """
The `deduplicate_fonts` operation finds duplicate fonts and merges them
into a single shared copy, rewriting every reference in the document to
point at it.

By default this runs in two passes:

1. **Whole-font pass**: any two `/Font` dictionaries that are fully
   structurally equivalent -- identical encoding, widths, descriptor
   metadata, and embedded program -- are merged into one shared `/Font`
   object outright. This is the bigger win when it applies, but it only
   fires when literally everything about the two fonts matches.
2. **Font-program pass**: any remaining embedded font-program streams
   (`/FontFile`, `/FontFile2`, `/FontFile3`) that are byte-identical --
   even if the `/Font` dictionaries around them differ, e.g. in
   `/Widths` (producers commonly tune these deliberately, such as
   pdfTeX's microtype protrusion/expansion) or `/Encoding` -- are merged
   at the program level alone, leaving each `/Font` dictionary's own
   metadata untouched.

Only the embedded font *program* is ever compared/merged in the second
pass; a `/FontDescriptor`'s other metadata and a `/Font` dict's own
`/Encoding`/`/Widths`/`/ToUnicode` are never touched or compared by it.

This is most useful on PDFs assembled by concatenating other PDFs (via
`cat` or similar), where the same font is often embedded once per
source document.

### Parameters

* `mode=<full|fontfile_only>` (default: `full`) -- `full` runs both
  passes described above. `fontfile_only` skips the whole-font pass and
  only merges duplicate embedded font-program streams -- use this if
  you specifically don't want `/Font` dictionaries themselves merged
  (e.g. if you rely on their being distinct objects for some other
  purpose).
* `min_bytes=<n>` (default: 0) -- Applies to the font-program pass only.
  Font programs smaller than this are never merged. Accepts a plain
  byte count or a size with a `KB`/`MB`/`GB` suffix (e.g.
  `min_bytes=64KB`).
"""

_DEDUPLICATE_FONTS_EXAMPLES = [
    HelpExample(
        desc="Merge all duplicate fonts in the document (both passes).",
        cmd="in.pdf deduplicate_fonts output out.pdf",
    ),
    HelpExample(
        desc="Only merge duplicate embedded font programs, leaving /Font dicts untouched.",
        cmd="in.pdf deduplicate_fonts mode=fontfile_only output out.pdf",
    ),
    HelpExample(
        desc="Only consider font programs of at least 64KB for the program-level pass.",
        cmd="in.pdf deduplicate_fonts min_bytes=64KB output out.pdf",
    ),
]

_VALID_MODES = ("full", "fontfile_only")


def _parse_deduplicate_fonts_args(args: list[str]) -> tuple[str, int]:
    """Parses `deduplicate_fonts` keyword arguments, returning (mode, min_bytes)."""
    parsed = parse_keyval_list(
        args,
        allowed_keys=["mode", "min_bytes"],
        context="deduplicate_fonts",
    )

    mode = parsed.get("mode", "full")
    if mode not in _VALID_MODES:
        raise InvalidArgumentError(
            f"deduplicate_fonts: invalid mode '{mode}'. "
            f"Expected one of: {', '.join(_VALID_MODES)}."
        )

    min_bytes_raw = parsed.get("min_bytes")
    min_bytes = (
        0
        if min_bytes_raw is None
        else parse_size_to_bytes(min_bytes_raw, context="deduplicate_fonts: min_bytes")
    )

    return mode, min_bytes


@register_operation(
    "deduplicate_fonts",
    tags=["in_place", "fonts", "optimize"],
    type="single input operation",
    desc="Merge duplicate fonts into a single shared copy",
    long_desc=_DEDUPLICATE_FONTS_LONG_DESC,
    usage=(
        "<input> deduplicate_fonts [mode=<full|fontfile_only>] [min_bytes=<size>] output <output>"
    ),
    examples=_DEDUPLICATE_FONTS_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def deduplicate_fonts(pdf: Pdf, args: list[str]) -> OpResult:
    """Merge structurally-equivalent fonts, in place."""
    mode, min_bytes = _parse_deduplicate_fonts_args(args or [])

    dict_merged = 0
    if mode == "full":
        dict_result = deduplicate_font_dicts(pdf)
        dict_merged = dict_result["merged"]
        if dict_merged:
            logger.info("deduplicate_fonts: merged %d whole /Font object(s).", dict_merged)

    file_result = deduplicate_font_files(pdf, threshold=min_bytes)
    file_merged = file_result["merged"]

    if file_merged:
        logger.info(
            "deduplicate_fonts: merged %d duplicate font program(s), "
            "saving approximately %d bytes of stream data.",
            file_merged,
            file_result["bytes_saved"],
        )

    if not dict_merged and not file_merged:
        logger.info("deduplicate_fonts: no duplicate fonts found.")

    return OpResult(success=True, pdf=pdf)
