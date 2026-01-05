# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/parsers/chop_parser.py

"""Parser for chop arguments"""

import logging
import re

logger = logging.getLogger(__name__)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Array

from pdftl.core.constants import UNITS
from pdftl.utils.page_specs import parse_specs

MAX_PIECES = 10_000


def parse_chop_spec(spec_str: str, page_rect: "Array"):
    """
    Parses a chop spec string with flexible syntax into a list of
    pikepdf.Rectangle objects representing the desired chops.
    """
    content, direction = _parse_chop_spec_prep(spec_str)

    page_width = abs(float(page_rect[2]) - float(page_rect[0]))
    page_height = abs(float(page_rect[3]) - float(page_rect[1]))
    total_dim = page_width if direction == "cols" else page_height

    # now try each parsing strategy in turn
    try:
        # Strategy 1: Try parsing as a simple integer (e.g., "rows3")
        final_sizes, delete_flags = _parse_integer_spec(content, total_dim)
    except ValueError:
        # If it's not an integer, it must be a ratio or comma spec.
        # Let these functions raise their *own* specific errors.
        if ":" in content and "," not in content:
            # Strategy 2: Try parsing as a ratio (e.g., "cols(1:3)")
            final_sizes, delete_flags = _parse_ratio_spec(content, total_dim)
        # Strategy 3: Parse as a comma-separated list (the most complex case)
        else:
            content_parts = [s.strip() for s in content.split(",")]
            final_sizes, delete_flags = _parse_comma_spec(content_parts, total_dim)

    # final geometry construction
    return _build_rects(final_sizes, delete_flags, direction, page_width, page_height)


def parse_chop_specs_to_rules(specs, total_pages):
    """
    Parses a list of chop specifications into a dictionary of rules mapping
    page indices to their specific chop instructions.
    """
    page_rules = {}

    # 1. Pre-process specs to handle legacy 'even'/'odd' keywords.
    #    e.g. "odd 1-5rows2".
    #    New syntax like "[1-5]oddrows2" is handled by parse_specs naturally.
    grouped_specs = _group_specs_with_qualifiers(specs)

    for spec_str, keyword_qualifier in grouped_specs:

        # 2. Split the spec into its two main parts.
        #    e.g. "1-5rows2" -> "1-5", "rows2"
        #    e.g. "1,3rows2" -> "1,3", "rows2"
        page_range_part, chop_part = _split_spec_string(spec_str)

        # 3. Use the central parser to resolve the page selection.
        #    We pass the page_range_part as a single-element list.
        for page_spec in parse_specs([page_range_part], total_pages):

            # 4. Generate the list of affected page numbers from the PageSpec
            step = 1 if page_spec.start <= page_spec.end else -1
            page_numbers = list(range(page_spec.start, page_spec.end + step, step))

            # Filter: Internal Qualifiers (from [1-5]even syntax)
            if "even" in page_spec.qualifiers:
                page_numbers = [p for p in page_numbers if p % 2 == 0]
            if "odd" in page_spec.qualifiers:
                page_numbers = [p for p in page_numbers if p % 2 != 0]

            # Filter: External Keyword Qualifier (legacy "even 1-5..." syntax)
            if keyword_qualifier == "even":
                page_numbers = [p for p in page_numbers if p % 2 == 0]
            elif keyword_qualifier == "odd":
                page_numbers = [p for p in page_numbers if p % 2 != 0]

            # Filter: Omissions
            for om_start, om_end in page_spec.omissions:
                page_numbers = [p for p in page_numbers if not om_start <= p <= om_end]

            # 5. Apply the chop rule to the generated pages.
            for p_num in page_numbers:
                # Convert from 1-based page number to 0-based index.
                page_rules[p_num - 1] = chop_part

    return page_rules


##################################################


def _group_specs_with_qualifiers(specs):
    """
    Pre-processes the specs list to pair qualifiers ('even', 'odd')
    with the spec string that follows them.
    Returns a list of tuples: [(spec_str, qualifier_keyword), ...].
    """
    logger.debug("got specs=%s", specs)
    grouped_specs = []
    specs_iterator = iter(specs)
    for spec in specs_iterator:
        is_qualifier = spec.lower() in ("even", "odd")
        if is_qualifier:
            try:
                # The qualifier applies to the *next* spec string.
                next_spec = next(specs_iterator)
                grouped_specs.append((next_spec, spec.lower()))
            except StopIteration as exc:
                raise ValueError(f"Missing chop spec after '{spec}' keyword.") from exc
        else:
            # This spec has no preceding keyword qualifier.
            grouped_specs.append((spec, None))
    logger.debug("returning grouped_specs=%s", grouped_specs)
    return grouped_specs


def _split_spec_string(spec_str):
    """
    Splits a raw spec string (e.g., "1-5v") into its page-range and chop parts.
    Returns a tuple: (page_range_part, chop_part).
    """
    match = re.search(r"(cols|rows)", spec_str)
    if not match:
        raise ValueError(f"Invalid chop spec, missing 'cols' or 'rows': {spec_str}")

    split_point = match.start()
    page_range_part = spec_str[:split_point] or "1-end"
    chop_part = spec_str[split_point:]
    return page_range_part, chop_part


# _get_qualified_page_numbers removed (logic moved to main loop)


##################################################


def _parse_chop_spec_prep(spec_str: str):
    if not spec_str.startswith(("cols", "rows")):
        raise ValueError(f"Chop spec must start with 'cols' or 'rows', not '{spec_str[0]}'")

    direction = spec_str[:4]

    # default to chopping into 2 equal pieces
    content = spec_str[4:] if len(spec_str) > 4 else "2"

    # Strip outer parentheses if present
    if content.startswith("(") and content.endswith(")"):
        content = content[1:-1]

    return content, direction


def _build_rects(final_sizes, delete_flags, direction, page_width, page_height):
    """Builds a list of pikepdf.Array rectangles from calculated sizes."""
    from pikepdf import Array

    rects = []
    current_offset = 0
    for i, size in enumerate(final_sizes):
        if not delete_flags[i]:
            if direction == "cols":
                x0, y0 = current_offset, 0
                x1, y1 = current_offset + size, page_height
                rects.append(Array([x0, y0, x1, y1]))
            else:  # direction == "rows"
                x0, y0 = 0, page_height - current_offset - size
                x1, y1 = page_width, page_height - current_offset
                rects.append(Array([x0, y0, x1, y1]))
        current_offset += size
    return rects


def _parse_integer_spec(content, total_dim):
    """
    Parses a simple integer spec (e.g., "3").
    Returns a tuple of (final_sizes, delete_flags).
    """
    try:
        pieces = int(content)
        if pieces <= 0:
            raise ValueError("Number of pieces must be positive.")
        if pieces > MAX_PIECES:
            raise ValueError(f"Number of pieces is larger than MAX_PIECES={MAX_PIECES}.")
        piece_size = total_dim / pieces
        final_sizes = [piece_size] * pieces
        delete_flags = [False] * pieces
        return final_sizes, delete_flags
    except (ValueError, ZeroDivisionError) as exc:
        # Let the main function handle this by trying other parsers.
        raise ValueError from exc


def _parse_ratio_spec(content, total_dim):
    """
    Parses a ratio-based spec (e.g., "1:2").
    Returns a tuple of (final_sizes, delete_flags).
    """
    try:
        ratios = [float(r) for r in content.split(":")]
        total_ratio = sum(ratios)
        final_sizes = [(r / total_ratio) * total_dim for r in ratios]
        delete_flags = [False] * len(ratios)
        return final_sizes, delete_flags
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"Invalid ratio format: '{content}'") from exc


def _parse_comma_spec(content_parts, total_dim):
    """
    Parses a comma-separated list of chop specifications (e.g., "5%,fill,10ptd").
    Returns a tuple of (final_sizes, delete_flags).
    """
    parsed_specs = []
    fixed_total = 0
    fill_count = 0
    delete_flags = []

    # First pass: Parse each part into a structured representation.
    for part in content_parts:
        parsed, is_fill, should_delete = _parse_comma_spec_part_first_pass(part)
        delete_flags.append(should_delete)
        if is_fill:
            fill_count += 1
        parsed_specs.append(parsed)

    # Second pass: Calculate absolute values for fixed sizes (pt and %).
    for spec in parsed_specs:
        if spec["type"] == "%":
            absolute_val = total_dim * (spec["value"] / 100.0)
            spec["value"] = absolute_val  # Convert from % to absolute
            fixed_total += absolute_val
        elif spec["type"] != "fill":
            fixed_total += spec["value"]

    if fixed_total > total_dim:
        raise ValueError("Sum of fixed sizes in chop spec exceeds page dimensions.")

    # Calculate the size for each "fill" part.
    remaining_dim = total_dim - fixed_total
    fill_size = remaining_dim / fill_count if fill_count > 0 else 0

    # Final pass: Create the list of final sizes.
    final_sizes = [spec.get("value", fill_size) for spec in parsed_specs]

    return final_sizes, delete_flags


def _parse_comma_spec_part_first_pass(part):
    should_delete = part.endswith("d")
    size_str = part[:-1] if should_delete else part
    is_fill = False

    if size_str.lower() == "fill":
        parsed = {"type": "fill"}
        is_fill = True
    elif size_str.endswith("%"):
        value = float(size_str[:-1])
        parsed = {"type": "%", "value": value}
    elif unit_name := _find_unit(size_str):
        n = len(unit_name)
        value = float(size_str[:-n])
        parsed = {"type": unit_name, "value": value * UNITS[unit_name]}
    else:
        try:
            value = float(size_str)
            parsed = {"type": "pt", "value": value}
        except ValueError as exc:
            raise ValueError(f"Invalid size unit in chop spec: '{part}'") from exc

    return parsed, is_fill, should_delete


def _find_unit(input_str):
    """Find a unit in the UNITS data"""
    for unit_name in UNITS:
        if input_str.endswith(unit_name):
            return unit_name
    return None
