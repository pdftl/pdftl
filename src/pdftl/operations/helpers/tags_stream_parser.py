# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/helpers/tags_stream_parser.py

"""Low-level PDF content stream tokenization and MCID coordinate tracking."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from pdftl.utils.pdf_resources import get_resources

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stream Operator Handlers
# ---------------------------------------------------------------------------


def _handle_bdc(stripped: str, parts: list[str], line_num: int, stack: list) -> None:
    """Process a BDC (Begin Dictionary-Driven Property List) operator."""
    tag = parts[0] if len(parts) >= 2 else None
    m = re.search(r"/MCID\s+(\d+)", stripped)
    mcid = int(m.group(1)) if m else None
    stack.append((mcid, tag, line_num))


def _handle_bmc(parts: list[str], line_num: int, stack: list) -> None:
    """Process a BMC (Begin Marked Content) operator."""
    tag = parts[0] if parts else None
    stack.append((None, tag, line_num))


def _handle_emc(line_num: int, lines: list[str], stack: list, mcid_blocks: dict) -> None:
    """Process an EMC (End Marked Content) operator and record MCID blocks."""
    if stack:
        mcid, tag, start = stack.pop()
        if mcid is not None:
            mcid_blocks[mcid] = {
                "tag": tag,
                "start_line": start,
                "end_line": line_num,
                "lines": lines[start - 1 : line_num],
            }


def _process_stream_line(
    stripped: str,
    parts: list[str],
    line_num: int,
    lines: list[str],
    stack: list,
    mcid_blocks: dict,
) -> None:
    """Dispatch parsed stream lines to their respective operator handlers."""
    op = parts[-1]
    if op == "BDC":
        _handle_bdc(stripped, parts, line_num, stack)
    elif op == "BMC":
        _handle_bmc(parts, line_num, stack)
    elif op == "EMC":
        _handle_emc(line_num, lines, stack, mcid_blocks)


# ---------------------------------------------------------------------------
# Main Stream Parser Hook
# ---------------------------------------------------------------------------


def parse_stream_bytes_for_mcids(stream_bytes: bytes) -> tuple[dict[int, dict], list[str]]:
    """
    Parse normalized stream bytes and return MCID blocks and all lines.
    """
    lines = stream_bytes.decode("latin-1").splitlines()

    stack: list[tuple[int | None, str | None, int]] = []
    mcid_blocks: dict[int, dict] = {}

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue

        parts = stripped.split()
        _process_stream_line(stripped, parts, i, lines, stack, mcid_blocks)

    if stack:
        logger.debug(
            "%d unclosed BDC/BMC operators at end of stream",
            len(stack),
        )

    return mcid_blocks, lines


def _build_mcid_stream_map(
    pdf: pikepdf.Pdf,
    page_num: int,
    annotate: bool = False,
) -> tuple[dict[int, dict], list[str]]:
    """
    Parse the normalized content stream for *page_num* (1-based).

    Returns:
      mcid_blocks: mcid -> {tag, start_line, end_line, lines, page}
      all_lines:   normalized stream as a list of strings (1-indexed externally)
    """
    import pikepdf

    page = pdf.pages[page_num - 1]

    # Normalize exactly as dump_streams / replace do
    try:
        stream_bytes = pikepdf.unparse_content_stream(pikepdf.parse_content_stream(page))
    except (pikepdf.PdfError, ValueError) as exc:
        logger.warning("Page %d: could not parse content stream: %s", page_num, exc)
        return {}, []

    # IMPORTANT: MCID blocks must be parsed from the *unannotated* stream.
    # annotate_stream() appends trailing "% ..." comment text to lines, which
    # shifts the last whitespace-separated token away from BDC/BMC/EMC and
    # causes _process_stream_line() (which keys off parts[-1]) to silently
    # miss every operator. Parse first, annotate only for display after.
    mcid_blocks, lines = parse_stream_bytes_for_mcids(stream_bytes)

    if annotate:
        # Re-use the existing stream annotator if available. This only
        # changes the *display* text of each line; it must not feed back
        # into MCID block detection above. We rely on annotate_stream()
        # preserving line count/order (appending comments in place rather
        # than inserting/removing lines) so the start_line/end_line indices
        # recorded above still line up against the annotated lines.
        try:
            from pdftl.operations.helpers.stream_annotator import annotate_stream

            annotated_bytes = annotate_stream(
                stream_bytes,
                get_resources(page),
                40,
                80,
            )
            annotated_lines = annotated_bytes.decode("latin-1").splitlines()
            if len(annotated_lines) == len(lines):
                lines = annotated_lines
                for block in mcid_blocks.values():
                    block["lines"] = lines[block["start_line"] - 1 : block["end_line"]]
            else:
                logger.debug(
                    "Page %d: annotated line count (%d) != normalized line count "
                    "(%d); skipping annotation to avoid misaligned MCID blocks",
                    page_num,
                    len(annotated_lines),
                    len(lines),
                )
        except (AttributeError, ValueError, TypeError, pikepdf.PdfError) as exc:
            logger.debug("Stream annotation failed: %s", exc)

    # Attach page context to the blocks for downstream consumers
    for block in mcid_blocks.values():
        block["page"] = page_num

    return mcid_blocks, lines
