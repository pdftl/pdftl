# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/parsers/rebox_parser.py

"""Parser for rebox arguments"""

import logging
import re

logger = logging.getLogger(__name__)
from pdftl.operations.parsers.paper_parser import parse_paper_spec
from pdftl.utils.dimensions import dim_str_to_pts
from pdftl.utils.page_specs import page_numbers_matching_page_spec


def specs_to_page_rules(specs, total_pages, operation):
    """Generate "page rules" for a rebox operation from a user-supplied string"""
    page_rules = {}
    spec_pattern = re.compile(r"^([^(]*)?\((.*?)\)$")
    preview = False

    for spec in specs:
        if spec == "preview":
            preview = True
            continue
        if not (match := spec_pattern.match(spec)):
            raise ValueError(
                f"Invalid {operation} specification format: '{spec}'. "
                "Expected a format like '1-5(10pt)' or '1-end(fit)'."
            )
        page_range_str, content_str = match.groups()
        logger.debug("page_range_str=%s, content_str=%s", page_range_str, content_str)

        # --- VALIDATION STEP ---
        # We try to parse the content string immediately to catch typos like 'fit-groupp'.
        # We pass (0, 0) as dimensions because we don't care about the numeric result
        # of percentages here, only the structural validity.
        try:
            parse_rebox_content(content_str, 1000, 1000, "dummy_op")
        except (ValueError, TypeError, AttributeError) as e:
            raise ValueError(
                f"Error parsing {operation} content '{content_str}' in spec '{spec}': {e}"
            ) from e
        # -----------------------

        page_numbers = page_numbers_matching_page_spec(page_range_str, total_pages)
        for page_num in page_numbers:
            # Page numbers from the parser are 1-based; list indices are 0-based
            page_rules[page_num - 1] = content_str
    return page_rules, preview


def parse_rebox_content(content_str, page_width, page_height, operation):
    """
    Master parser for the content string inside the parentheses.
    Dispatches to Smart rebox, Paper Size, or Margin parsers in order.

    Returns a dict with a 'type' key:
      - {'type': 'fit', 'mode': 'fit'|'fit-group', 'source': str|None, 'padding': (l,t,r,b)}
      - {'type': 'paper', 'size': (w, h)}
      - {'type': 'margin', 'values': (l, t, r, b)}
      - {'type': 'abs', 'values': (x0, y0, x1, y1)}
    """
    # 1. Try fit/fit-group
    smart_rebox = parse_smart_rebox_spec(content_str, page_width, page_height, operation)
    if smart_rebox:
        return smart_rebox

    # 2. Try Paper Size (e.g. "a4", "a4_l")
    paper_size = parse_paper_spec(content_str)
    if paper_size:
        return {"type": "paper", "size": paper_size}

    # 3. Try absolute box
    abs_box = parse_abs_box(content_str, page_width, page_height)
    if abs_box:
        return {"type": "abs", "values": abs_box}

    # 4. Default: Margins (e.g. "10pt, 20pt")
    margins = parse_rebox_margins(content_str, page_width, page_height, operation)
    return {"type": "margin", "values": margins}


def parse_abs_box(spec_str, page_width, page_height):
    parts = [p.strip() for p in spec_str.split(",")]
    head = parts[0].lower()

    if not head.startswith("abs"):
        return None

    if not len(parts) == 5:
        raise ValueError(f"Should have 4 comma-separated values following `abs`, got {parts[1:]}")

    x0 = dim_str_to_pts(parts[1], page_width)
    y0 = dim_str_to_pts(parts[2], page_height)
    x1 = dim_str_to_pts(parts[3], page_width)
    y1 = dim_str_to_pts(parts[4], page_height)
    return x0, y0, x1, y1


def parse_smart_rebox_spec(spec_str, page_width, page_height, operation):
    """
    Parses 'fit' or 'fit-group' syntax.
    Format: mode[=source], [padding...]
    Example: fit-group=1-5, 10pt
    """
    parts = [p.strip() for p in spec_str.split(",")]
    head = parts[0].lower()

    if not head.startswith("fit"):
        return None

    mode = "fit"
    source_spec = None

    # Handle "fit-group" and optional "=source"
    if head.startswith("fit-group"):
        mode = "fit-group"
        if "=" in head:
            # e.g. "fit-group=1-5"
            _, source_spec = head.split("=", 1)
            source_spec = source_spec.strip()
    elif head != "fit":
        # If it starts with fit but isn't "fit" or "fit-group" (e.g. "fitting"),
        # return None to let downstream parsers fail or handle it.
        return None

    # Padding logic:
    # Everything after the first comma is treated as padding arguments
    padding_str = ",".join(parts[1:])

    if not padding_str:
        # Default: 0 padding
        padding = (0.0, 0.0, 0.0, 0.0)
    else:
        # Reuse the existing robust margin parser for padding
        padding = parse_rebox_margins(padding_str, page_width, page_height, operation)

    return {"type": "fit", "mode": mode, "source": source_spec, "padding": padding}



def parse_rebox_margins(spec_str, page_width, page_height, operation):
    """
    Parses a comma-separated rebox spec string into four point values
    for left, top, right, and bottom margins.
    """
    parts = [p.strip() for p in spec_str.split(",")]
    num_parts = len(parts)

    if not 1 <= num_parts <= 4:
        raise ValueError(
            "{operation} spec must have between 1 and 4 comma-separated values, "
            f"but found {num_parts}."
        )

    # The logic cascades based on the number of parts provided.
    left = dim_str_to_pts(parts[0], page_width)

    top = dim_str_to_pts(parts[1], page_width) if num_parts >= 2 else left

    right = dim_str_to_pts(parts[2], page_width) if num_parts >= 3 else left

    # Bottom defaults to top's value but uses page_height for its own calculation
    # only when a fourth value is explicitly provided.
    if num_parts >= 4:
        bottom = dim_str_to_pts(parts[3], page_height)
    else:
        bottom = top

    return left, top, right, bottom
