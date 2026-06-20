# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/parsers/add_images_parser.py

"""Parser for add_images operation rule strings.

This module is responsible for parsing the add_images rule format:

    [page_range]<delim><image_paths><delim>[(options)]

Delimiter must be a single, non-alphanumeric character. Multiple image paths
can be separated by spaces or commas within the delimiters.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from pdftl.utils.page_specs import page_numbers_matching_page_spec
from pdftl.utils.string_utils import split_string_respecting_quotes
from pdftl.utils.keyval_parser import parse_keyval_list
from pdftl.operations.parsers.common.delimited import split_delimited_rule

logger = logging.getLogger(__name__)

# Define whitelisted option keys for validation
ALLOWED_KEYS = [
    "underlay",
    "scale_mode",
    "position",
    "width",
    "height",
    "offset-x",
    "offset-y",
    "opacity",
]


def parse_add_images_rules(rule_strings: list[str], total_pages: int) -> dict[int, list[dict]]:
    """
    Parse a list of add_images rule strings into a dictionary mapping page
    indices to their specific image-addition instructions.

    Returns:
        dict: page_index (int, 0-based) -> list[rule_dict]
    """
    page_rules = defaultdict(list)

    for raw_rule in rule_strings:
        try:
            page_range_part, images_part, options_part = split_delimited_rule(
                raw_rule, label="images"
            )

            logger.debug(
                "page_range_part='%s', images_part='%s', options_part='%s'",
                page_range_part,
                images_part,
                options_part,
            )

            rule_dict = _parse_add_images_op(images_part, options_part)

            matched_pages = page_numbers_matching_page_spec(page_range_part, total_pages)
            for count, p_num in enumerate(matched_pages, 1):
                copied_rule = rule_dict.copy()
                copied_rule["n"] = count
                page_rules[p_num - 1].append(copied_rule)

        except ValueError as exc:
            raise ValueError(f"Invalid add_images rule '{raw_rule}': {exc}") from exc

    return dict(page_rules)


def _append_sub_parts(images: list[str], p: str) -> None:
    """Helper to split and append valid stripped image paths."""
    for sub_p in split_string_respecting_quotes(p, delimiter=","):
        sub_p_clean = sub_p.strip().strip("'\"")
        if sub_p_clean:
            images.append(sub_p_clean)


def _extract_images_from_part(images_part: str) -> list[str]:
    """Extracts list of valid image paths from raw images segment."""
    if not images_part:
        raise ValueError("At least one image path must be provided within the delimiters.")

    images = []
    try:
        parts = split_string_respecting_quotes(images_part, delimiter=" ")
        for p in parts:
            _append_sub_parts(images, p)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"Could not parse image paths: {images_part}") from exc

    if not images:
        raise ValueError("No valid image file paths found.")
    return images


def _parse_raw_options(options_part: str) -> dict[str, str]:
    """Parses raw parenthetical options safely."""
    if not options_part:
        return {}
    if not (options_part.startswith("(") and options_part.endswith(")")):
        raise ValueError(
            f"Options block must be enclosed in parentheses, e.g., (...), but got: {options_part}"
        )
    content = options_part[1:-1].strip()
    tokens = split_string_respecting_quotes(content, delimiter=",")
    return parse_keyval_list(tokens, allowed_keys=ALLOWED_KEYS, context="add_images")


def _parse_add_images_op(images_part: str, options_part: str) -> dict:
    """Parse the images list and options part into a structured rule dict."""
    images = _extract_images_from_part(images_part)
    raw_opts = _parse_raw_options(options_part)
    rule = {"images": images}
    rule.update(_normalize_options(raw_opts))
    return rule


def _normalize_options(raw_options: dict[str, str]) -> dict[str, Any]:
    """Convert string option values into parsed and validated types."""
    normalized = {}
    opts_copy = raw_options.copy()

    # Layout Options
    normalized["underlay"] = opts_copy.pop("underlay", "false").lower() in (
        "true",
        "yes",
        "1",
        "on",
    )
    normalized["scale_mode"] = opts_copy.pop("scale_mode", "none").lower().strip("'\"")

    # Handle 'position' keyword (matches add_text 'position')
    pos_val = opts_copy.pop("position", "bottom-left").lower().strip("'\"")
    if pos_val == "center":
        pos_val = "mid-center"
    normalized["position"] = pos_val

    width_val = opts_copy.pop("width", None)
    normalized["width"] = width_val.strip("'\"") if width_val is not None else None

    height_val = opts_copy.pop("height", None)
    normalized["height"] = height_val.strip("'\"") if height_val is not None else None

    # Handle separate offset-x and offset-y (matches add_text design)
    offset_x_val = opts_copy.pop("offset-x", "0")
    normalized["offset-x"] = offset_x_val.strip("'\"")

    offset_y_val = opts_copy.pop("offset-y", "0")
    normalized["offset-y"] = offset_y_val.strip("'\"")

    # Opacity Options
    opacity_val = opts_copy.pop("opacity", "1.0").strip("'\"")
    try:
        normalized["opacity"] = float(opacity_val)
    except ValueError as exc:
        raise ValueError(f"Invalid opacity value: '{opacity_val}'") from exc

    return normalized
