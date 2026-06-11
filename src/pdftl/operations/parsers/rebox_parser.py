# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/parsers/rebox_parser.py

"""Parser for rebox arguments"""

import logging
import re

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.parsers.paper_parser import parse_paper_spec
from pdftl.utils.dimensions import dim_str_to_pts
from pdftl.utils.page_specs import page_numbers_matching_page_spec

logger = logging.getLogger(__name__)


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
        try:
            parsed = parse_rebox_content(content_str, 1000, 1000, operation)
        except (ValueError, TypeError, AttributeError, InvalidArgumentError) as e:
            raise ValueError(
                f"Error parsing {operation} content '{content_str}' in spec '{spec}': {e}"
            ) from e
        # -----------------------

        # A local 'preview' keyword inside the parentheses marks just these pages.
        if parsed.get("preview"):
            preview = True

        page_numbers = page_numbers_matching_page_spec(page_range_str, total_pages)
        for page_num in page_numbers:
            page_rules[page_num - 1] = _strip_preview_keyword(content_str)[0]
    return page_rules, preview


def parse_rebox_content(content_str, page_width, page_height, operation):
    """
    Master parser for the content string inside the parentheses.
    Dispatches to Smart rebox, Paper Size, or Margin parsers in order.

    Accepts an optional trailing ',preview' keyword in content_str.

    Returns a dict with a 'type' key and an optional 'preview' bool:
      - {'type': 'fit', 'mode': 'fit'|'fit-group', 'source': str|None,
         'padding': (l,t,r,b), 'preview': bool}
      - {'type': 'paper', 'size': (w, h), 'preview': bool}
      - {'type': 'margin', 'values': (l, t, r, b), 'preview': bool}
      - {'type': 'abs', 'values': (x0, y0, x1, y1), 'preview': bool}
    """
    content_str, local_preview = _strip_preview_keyword(content_str)

    # 1. Try fit/fit-group
    smart_rebox = parse_smart_rebox_spec(content_str, page_width, page_height, operation)
    if smart_rebox:
        smart_rebox["preview"] = local_preview
        return smart_rebox

    # 2. Try Paper Size (e.g. "a4", "a4_l")
    paper_size = parse_paper_spec(content_str)
    if paper_size:
        return {"type": "paper", "size": paper_size, "preview": local_preview}

    # 3. Try absolute box
    abs_box = parse_abs_box(content_str, page_width, page_height)
    if abs_box:
        return {"type": "abs", "values": abs_box, "preview": local_preview}

    # 4. Default: Margins (e.g. "10pt, 20pt")
    margins = parse_rebox_margins(content_str, page_width, page_height, operation)
    return {"type": "margin", "values": margins, "preview": local_preview}


def _strip_preview_keyword(content_str):
    """
    Remove a trailing ',preview' token from a content string (case-insensitive).

    Returns (cleaned_content_str, found_preview_bool).

    Only strips 'preview' when it appears as the final comma-separated token so
    that it can never be confused with a dimension value or paper-size name.
    """
    parts = [p.strip() for p in content_str.split(",")]
    if parts and parts[-1].lower() == "preview":
        return ",".join(parts[:-1]), True
    return content_str, False


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
    following the LTRB (Left, Top, Right, Bottom) convention.
    """
    parts = [p.strip() for p in spec_str.split(",")]
    num_parts = len(parts)

    if not 1 <= num_parts <= 4:
        raise ValueError(
            f"{operation} spec must have between 1 and 4 comma-separated values, "
            f"but found {num_parts}."
        )

    # 1. Map string specs using LTRB pair fallbacks
    # 1 part:  [L]       -> L=L, T=L, R=L, B=L
    # 2 parts: [L, T]    -> L=L, T=T, R=L, B=T
    # 3 parts: [L, T, R] -> L=L, T=T, R=R, B=T
    left_str = parts[0]
    top_str = parts[1] if num_parts >= 2 else parts[0]
    right_str = parts[2] if num_parts >= 3 else parts[0]
    bottom_str = parts[3] if num_parts >= 4 else (parts[1] if num_parts >= 2 else parts[0])

    # 2. Convert to points safely using the correct page dimension for each axis
    left = dim_str_to_pts(left_str, page_width)
    top = dim_str_to_pts(top_str, page_height)
    right = dim_str_to_pts(right_str, page_width)
    bottom = dim_str_to_pts(bottom_str, page_height)

    return left, top, right, bottom
