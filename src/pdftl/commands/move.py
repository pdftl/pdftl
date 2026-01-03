# src/pdftl/commands/move.py

import logging
from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.commands.parsers.move_parser import parse_move_args
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.exceptions import UserCommandLineError
from pdftl.utils.page_specs import page_numbers_matching_page_spec

if TYPE_CHECKING:
    from pikepdf import Pdf

logger = logging.getLogger(__name__)

_MOVE_LONG_DESC = """
Relocates pages within the document without duplication.

Semantics:
  - Pages matching `<source-spec>` are removed and reinserted at the target.
  - `<target>` is a page spec (target range) defining a single anchor position.
  - `before`: insert before the first page of the target range.
  - `after`: insert after the last page of the target range.
"""


@register_operation(
    "move",
    tags=["pages", "organization"],
    type="single input operation",
    desc="Move pages to a new location",
    long_desc=_MOVE_LONG_DESC,
    usage="<input> move <source> {before|after} <target>",
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def move_pages(pdf: "Pdf", args: list) -> OpResult:
    spec = parse_move_args(args)
    total_pages = len(pdf.pages)

    # 1. Resolve Source Indices
    # Note: page_numbers... returns 1-based numbers, we need 0-based indices
    source_nums = page_numbers_matching_page_spec(spec.source_spec, total_pages)
    if not source_nums:
        logger.warning("Move source '%s' matched no pages. No changes made.", spec.source_spec)
        return OpResult(success=True, pdf=pdf)

    source_indices = sorted([n - 1 for n in source_nums])

    # 2. Resolve Target Anchor
    target_nums = page_numbers_matching_page_spec(spec.target_spec, total_pages)
    if not target_nums:
        raise UserCommandLineError(f"Move target '{spec.target_spec}' matched no pages.")

    target_indices = sorted([n - 1 for n in target_nums])

    # Anchor Resolution Rule:
    # before -> anchor is index of FIRST target page
    # after  -> anchor is index of LAST target page + 1
    if spec.mode == "before":
        anchor_orig = target_indices[0]
    else:  # after
        anchor_orig = target_indices[-1] + 1

    # 3. Calculate Adjustment (Semantics #3 & #5)
    # We must determine where the anchor lands AFTER the source pages are removed.
    # Every source page that is strictly before the anchor reduces the anchor index by 1.
    adjustment = sum(1 for idx in source_indices if idx < anchor_orig)
    anchor_final = anchor_orig - adjustment

    # 4. Perform Move
    # Collect page objects to move
    pages_to_move = [pdf.pages[i] for i in source_indices]

    # Remove them (in reverse order to preserve indices of remaining items during deletion)
    for i in reversed(source_indices):
        del pdf.pages[i]

    # Insert them at the calculated position
    # We use slicing to insert the whole block at once,
    # preserving their internal order (Semantics #4)
    pdf.pages[anchor_final:anchor_final] = pages_to_move

    logger.info("Moved %d pages %s page %d.", len(pages_to_move), spec.mode, target_indices[0] + 1)

    return OpResult(success=True, pdf=pdf)
