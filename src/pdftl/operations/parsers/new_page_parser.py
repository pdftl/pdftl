# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/parsers/new_page_parser.py

import re


def _is_page_definition(
    g_str: str | None, remaining_args: list[str], accept_standalone: bool
) -> bool:
    """Helper to evaluate if the parsed token represents a valid new page definition."""
    if g_str is not None:
        return True

    if accept_standalone:
        return True

    # If there's no geometry, it's a pure number. It's a definition if:
    # 'insert' handles it followed by a positional keyword
    if len(remaining_args) > 1 and remaining_args[1].lower() in ("after", "before"):
        return True

    return False


def parse_new_page_args(
    remaining_args: list[str], accept_standalone_number: bool = False
) -> tuple[int, str | None, list[str]]:
    """
    Parses a leading new page definition component of the form [N][(geometry)].

    Returns:
        tuple: (count, geometry_spec, remaining_arguments)
    """
    insert_count = 1
    geometry_spec = None

    if not remaining_args:
        return insert_count, geometry_spec, remaining_args

    first_arg = remaining_args[0]

    # 1. (?=\d|\() ensures we don't match arbitrary words
    # 2. (.+) ensures we don't accidentally match empty parentheses like '()'
    match = re.match(r"^(?=\d|\()(\d+)?(?:\((.+)\))?$", first_arg)

    if not match:
        return insert_count, geometry_spec, remaining_args

    c_str, g_str = match.groups()

    if not _is_page_definition(g_str, remaining_args, accept_standalone_number):
        return insert_count, geometry_spec, remaining_args

    if c_str:
        insert_count = int(c_str)
    if g_str:
        geometry_spec = g_str.lstrip("(").rstrip(")")

    return insert_count, geometry_spec, remaining_args[1:]
