# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/parsers/crop_parser.py

"""Parser for crop arguments"""

import logging
import re

logger = logging.getLogger(__name__)
from pdftl.core.constants import PAPER_SIZES
from pdftl.utils.page_specs import page_numbers_matching_page_spec


def specs_to_page_rules(specs, total_pages):
    """Generate "page rules" for crop from a user-supplied string"""
    page_rules = {}
    spec_pattern = re.compile(r"^([^(]*)?\((.*?)\)$")
    preview = False

    for spec in specs:
        if spec == "preview":
            preview = True
            continue
        if not (match := spec_pattern.match(spec)):
            raise ValueError(
                f"Invalid crop specification format: '{spec}'. "
                "Expected a format like '1-5(10pt)'."
            )
        page_range_str, margin_str = match.groups()
        logger.debug("page_range_str=%s, margin_str=%s", page_range_str, margin_str)
        page_numbers = page_numbers_matching_page_spec(page_range_str, total_pages)
        for page_num in page_numbers:
            # Page numbers from the parser are 1-based; list indices are 0-based
            page_rules[page_num - 1] = margin_str
    return page_rules, preview


def parse_paper_spec(spec_str):
    """
    Parses a spec string to determine if it's a paper size (e.g., 'a4', 'a4_l', '4x6').
    Returns a (width, height) tuple in points, or None if not a paper spec.
    """
    spec_lower = spec_str.lower()
    landscape = False
    if spec_lower.endswith("_l"):
        landscape = True
        spec_lower = spec_lower[:-2]

    paper_size = PAPER_SIZES.get(spec_lower)
    if not paper_size:
        # Try parsing custom inch dimensions like "4x6"
        match = re.match(r"^(\d*\.?\d+)x(\d*\.?\d+)$", spec_lower)
        if match:
            width_in, height_in = float(match.group(1)), float(match.group(2))
            paper_size = (width_in * 72, height_in * 72)

    if paper_size and landscape:
        return paper_size[1], paper_size[0]  # Swap width and height

    return paper_size


def parse_crop_margins(spec_str, page_width, page_height):
    """
    Parses a comma-separated crop spec string into four point values
    for left, top, right, and bottom margins.

    The shorthand logic is as follows:
    - 1 value:  [all sides]
    - 2 values: [left] [top]      (implies right=left, bottom=top)
    - 3 values: [left] [top] [right] (implies bottom=top)
    - 4 values: [left] [top] [right] [bottom]

    Note: `page_width` is used for left, top, and right calculations.
          `page_height` is used ONLY for the bottom calculation if specified.
    """
    parts = [p.strip() for p in spec_str.split(",")]
    num_parts = len(parts)

    if not 1 <= num_parts <= 4:
        raise ValueError(
            "Crop spec must have between 1 and 4 comma-separated values, "
            f"but found {num_parts}."
        )

    # The logic cascades based on the number of parts provided.
    left = _parse_single_margin_value(parts[0], page_width)

    top = _parse_single_margin_value(parts[1], page_width) if num_parts >= 2 else left

    right = _parse_single_margin_value(parts[2], page_width) if num_parts >= 3 else left

    # Bottom defaults to top's value but uses page_height for its own calculation
    # only when a fourth value is explicitly provided.
    if num_parts >= 4:
        bottom = _parse_single_margin_value(parts[3], page_height)
    else:
        bottom = top

    return left, top, right, bottom


##################################################


def _parse_single_margin_value(val_str, total_dimension):
    """
    Parses a single crop dimension string (e.g., '10%', '2in', '50pt')
    and converts it into points.
    """
    val_str = val_str.lower().strip()
    if not val_str:
        return 0.0

    # Using a dictionary lookup is cleaner than a long if/elif chain.
    unit_multipliers = {
        "in": 72.0,
        "cm": 28.35,
        "mm": 2.835,
    }

    if val_str.endswith("%"):
        # Percentage is a special case that depends on the total dimension.
        numeric_part = val_str[:-1]
        return (float(numeric_part) / 100.0) * total_dimension

    for unit, multiplier in unit_multipliers.items():
        if val_str.endswith(unit):
            numeric_part = val_str[: -len(unit)]
            return float(numeric_part) * multiplier

    # Default to points, stripping an optional 'pt' suffix.
    numeric_part = re.sub(r"pt$", "", val_str)
    return float(numeric_part)
