# src/pdftl/operations/parsers/new_page_parser.py

import re


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

    if remaining_args:
        first_arg = remaining_args[0]
        # 1. (?=\d|\() ensures we don't match arbitrary words
        # 2. (.+) ensures we don't accidentally match empty parentheses like '()'
        match = re.match(r"^(?=\d|\()(\d+)?(?:\((.+)\))?$", first_arg)

        if match:
            c_str, g_str = match.groups()

            # Determine if this token is actually a page definition
            if g_str is not None:
                is_def = True
            else:
                # If there's no geometry, it's a pure number. It's a definition if:
                # - 'create' handles it (accept_standalone_number=True)
                # - 'insert' handles it followed by a positional keyword
                has_keyword_lookahead = len(remaining_args) > 1 and remaining_args[1].lower() in (
                    "after",
                    "before",
                )
                is_def = accept_standalone_number or has_keyword_lookahead

            if is_def:
                if c_str:
                    insert_count = int(c_str)
                if g_str:
                    geometry_spec = g_str.lstrip("(").rstrip(")")
                remaining_args = remaining_args[1:]

    return insert_count, geometry_spec, remaining_args
