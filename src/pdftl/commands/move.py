# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/move.py

"""Move pages in a PDF"""

import logging
from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.commands.parsers.move_parser import parse_move_args
from pdftl.commands.types.move_types import MoveSpec
from pdftl.core.registry import register_operation
from pdftl.core.types import HelpExample, OpResult
from pdftl.exceptions import UserCommandLineError
from pdftl.utils.arg_helpers import resolve_operation_spec
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

_MOVE_EXAMPLES = [
    HelpExample(
        desc="Simple Single Page: Move page 1 to the very end of a 10-page document.",
        cmd="in.pdf move 1 after 10 output out.pdf",
    ),
    HelpExample(
        desc="Moving a Block (Range): Move the first 5 pages to appear after page 8.",
        cmd="in.pdf move 1-5 after 8 output out.pdf",
    ),
    HelpExample(
        desc=(
            "Moving a Discontinuous List: "
            "Gather pages 1, 3, and 5 and place them before page 10."
        ),
        cmd="in.pdf move 1,3,5 before 10 output out.pdf",
    ),
    HelpExample(
        desc=(
            "Reordering to the Front: "
            "Take the last page (e.g., page 10) and make it the cover page."
        ),
        cmd="in.pdf move 10 before 1 output out.pdf",
    ),
    HelpExample(
        desc=(
            "'Pulling' content back: "
            "Take page 20 and insert it in the middle of the document (after page 5)."
        ),
        cmd="in.pdf move 20 after 5 output out.pdf",
    ),
]


@register_operation(
    "move",
    tags=["pages", "organization"],
    type="single input operation",
    desc="Move pages to a new location",
    long_desc=_MOVE_LONG_DESC,
    examples=_MOVE_EXAMPLES,
    usage="<input> move <source> {before|after} <target>",
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def move_pages(pdf: "Pdf", args: list) -> OpResult:
    """
    CLI Adapter: Parses string arguments into a spec, then runs logic.
    """
    spec = resolve_operation_spec(args, parse_move_args, MoveSpec)
    return execute_move(pdf, spec)


def execute_move(pdf: "Pdf", spec: MoveSpec) -> OpResult:
    """
    Core Logic: Executes a move based on a structured MoveSpec.
    This can be called by CLI, JSON, or other Python code.
    """
    total_pages = len(pdf.pages)

    # 1. Resolve Source Indices
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

    # Anchor Resolution Rule
    if spec.mode == "before":
        anchor_orig = target_indices[0]
    else:  # after
        anchor_orig = target_indices[-1] + 1

    # 3. Calculate Adjustment
    adjustment = sum(1 for idx in source_indices if idx < anchor_orig)
    anchor_final = anchor_orig - adjustment

    # 4. Perform Move
    pages_to_move = [pdf.pages[i] for i in source_indices]

    for i in reversed(source_indices):
        del pdf.pages[i]

    pdf.pages[anchor_final:anchor_final] = pages_to_move

    logger.info("Moved %d pages %s page %d.", len(pages_to_move), spec.mode, target_indices[0] + 1)

    return OpResult(success=True, pdf=pdf)
