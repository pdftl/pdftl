# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/parsers/insert_parser.py

"""Parser for the insert operation"""

from pdftl.operations.parsers.new_page_parser import parse_new_page_args
from pdftl.operations.types.insert_types import InsertSpec


def parse_insert_args(args: list[str]) -> InsertSpec:
    """
    Parses arguments for the insert command with smart defaults.

    Syntax: [N][(spec)] [{after|before} <range>]

    Defaults:
      - If range is omitted: defaults to '1-end'
      - If mode is omitted: defaults to 'after'
      - If both omitted: defaults to 'after 1-end'

    Examples:
      - `insert`: Insert 1 blank page after 1-end.
      - `insert (A4)`: Insert 1 A4 page after 1-end.
      - `insert 5`: Insert 1 page after page 5.
      - `insert 2 after 5`: Insert 2 pages after page 5.
    """
    # 1. Core structural parse using the shared utility.
    # For 'insert', accept_standalone_number=False because a standalone number
    # without a lookahead keyword signifies the target range, not the count.
    insert_count, geometry_spec, remaining_args = parse_new_page_args(
        args, accept_standalone_number=False
    )

    mode = "after"
    target_page_spec = "1-end"

    # 2. Parse Mode and Range
    if remaining_args:
        if remaining_args[0].lower() in ("after", "before"):
            mode = remaining_args[0].lower()
            remaining_args = remaining_args[1:]

            if remaining_args:
                target_page_spec = " ".join(remaining_args)
        else:
            # Implicit "after" (default), rest is range.
            target_page_spec = " ".join(remaining_args)

    return InsertSpec(insert_count, geometry_spec, mode, target_page_spec)
