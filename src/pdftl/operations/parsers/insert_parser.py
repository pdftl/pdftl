# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/parsers/insert_parser.py

"""Parser for the insert operation"""

import re

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
    # Defaults
    insert_count = 1
    geometry_spec = None
    mode = "after"
    target_page_spec = "1-end"

    remaining_args = args

    # 1. Parse the Definition: [N][(geometry)]
    if remaining_args:
        first_arg = remaining_args[0]
        match = re.match(r"^(\d+)?(?:\((.*)\))?$", first_arg)

        if match:
            c_str, g_str = match.groups()

            is_definition = False

            # Case A: Contains geometry `(...)`. Always a definition.
            if g_str is not None:
                is_definition = True

            # Case B: Pure number `N`.
            # Ambiguous: could be count "2" or page "2".
            # Rule: It is a count ONLY if the NEXT word is a keyword.
            elif c_str is not None:
                if len(remaining_args) > 1 and remaining_args[1].lower() in ("after", "before"):
                    is_definition = True

            if is_definition:
                if c_str:
                    insert_count = int(c_str)
                if g_str:
                    geometry_spec = g_str
                remaining_args = remaining_args[1:]

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
