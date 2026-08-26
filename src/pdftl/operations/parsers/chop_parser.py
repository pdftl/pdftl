# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/parsers/chop_parser.py

"""Parser for chop arguments"""

import logging
import math
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Array

from pdftl.core.constants import UNITS
from pdftl.utils.page_specs import page_numbers_matching_page_spec

logger = logging.getLogger(__name__)

MAX_PIECES = 10_000


def parse_chop_spec(spec_str: str, page_rect: "Array"):
    """
    Parses a chop spec string with flexible syntax into a list of
    pikepdf.Rectangle objects representing the desired chops.
    """
    content, direction, overlap_str = _parse_chop_spec_prep(spec_str)

    page_width = abs(float(page_rect[2]) - float(page_rect[0]))
    page_height = abs(float(page_rect[3]) - float(page_rect[1]))
    total_dim = page_width if direction == "cols" else page_height

    overlap = _parse_overlap_value(overlap_str, total_dim)

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
    return _build_rects(final_sizes, delete_flags, direction, page_width, page_height, overlap)


def parse_chop_specs_to_rules(specs, total_pages):
    """
    Parses a list of chop specifications into a dictionary of rules mapping
    page indices to their specific chop instructions.
    """
    page_rules = {}

    for spec in specs:
        # 2. Split the spec into its two main parts.
        #    e.g. "1-5rows2" -> "1-5", "rows2"
        #    e.g. "1,3rows2" -> "1,3", "rows2"
        page_range_part, chop_part = _split_spec_string(spec)

        # 5. Apply the chop rule to the generated pages.
        for p_num in page_numbers_matching_page_spec(page_range_part, total_pages):
            # Convert from 1-based page number to 0-based index.
            page_rules[p_num - 1] = chop_part

    return page_rules


##################################################


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

    # Extract an optional trailing overlap modifier, e.g. "cols3+10pt" or
    # "rows(1:2)+5%". The '+' is unambiguous: it never appears in the
    # integer/ratio/comma-list size formats.
    overlap_str = None
    plus_idx = content.find("+")
    if plus_idx != -1:
        overlap_str = content[plus_idx + 1 :]
        content = content[:plus_idx] or "2"

        # Guard against a '+' accidentally placed inside the parens,
        # e.g. "cols(1:2+10pt)" instead of "cols(1:2)+10pt".
        if content.count("(") != content.count(")"):
            raise ValueError(
                f"Unbalanced parentheses around overlap in chop spec: '{spec_str}'. "
                "Did you mean to put '+<overlap>' outside the parentheses?"
            )

    # Strip outer parentheses if present
    if content.startswith("(") and content.endswith(")"):
        content = content[1:-1]

    return content, direction, overlap_str


def _parse_overlap_value(overlap_str, total_dim):
    """
    Parses the '+<overlap>' modifier into an absolute size (points).

    Accepts the same unit formats as a single comma-list entry: a bare
    number (points), a '%' of total_dim, or a named unit like 'cm'.
    Returns 0.0 if no overlap was specified.
    """
    if overlap_str is None or overlap_str == "":
        return 0.0

    if overlap_str.endswith("%"):
        try:
            value = total_dim * (float(overlap_str[:-1]) / 100.0)
        except ValueError as exc:
            raise ValueError(f"Invalid overlap value: '{overlap_str}'") from exc
    elif unit_name := _find_unit(overlap_str):
        n = len(unit_name)
        try:
            value = float(overlap_str[:-n]) * UNITS[unit_name]
        except ValueError as exc:
            raise ValueError(f"Invalid overlap value: '{overlap_str}'") from exc
    else:
        try:
            value = float(overlap_str)
        except ValueError as exc:
            raise ValueError(f"Invalid overlap value: '{overlap_str}'") from exc

    if math.isnan(value) or math.isinf(value):
        raise ValueError(f"Overlap must be a finite number: '{overlap_str}'")

    if value < 0:
        raise ValueError(f"Overlap must be non-negative: '{overlap_str}'")

    return value


def _build_rects(final_sizes, delete_flags, direction, page_width, page_height, overlap=0.0):
    """
    Builds a list of pikepdf.Array rectangles from calculated sizes.

    `overlap` (points) is added to each internal seam between two pieces
    that are BOTH kept (not flagged for deletion), split evenly: the
    earlier piece grows by overlap/2 into the later one, and the later
    piece grows by overlap/2 into the earlier one. Outer page edges and
    seams touching a discarded piece are never expanded.
    """
    from pikepdf import Array

    rects = []
    current_offset = 0
    n = len(final_sizes)
    for i, size in enumerate(final_sizes):
        if not delete_flags[i]:
            start_offset = current_offset
            end_offset = current_offset + size
            if i > 0 and not delete_flags[i - 1]:
                start_offset -= overlap / 2
            if i < n - 1 and not delete_flags[i + 1]:
                end_offset += overlap / 2
            if direction == "cols":
                x0, y0 = start_offset, 0
                x1, y1 = end_offset, page_height
                rects.append(Array([x0, y0, x1, y1]))
            else:  # direction == "rows"
                x0, y0 = 0, page_height - end_offset
                x1, y1 = page_width, page_height - start_offset
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
