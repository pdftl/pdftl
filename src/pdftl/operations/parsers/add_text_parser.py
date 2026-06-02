# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/parsers/add_text_parser.py

"""Parser for add_text spec strings.

This module is responsible for parsing the add_text spec format:

    [page_range]<delim><text><delim>[(options)]

and resolving page ranges to per-page rule dicts. Variable tokenisation,
evaluation, and rendering have moved to pdftl.utils.text_templates.
"""

import logging
from collections import defaultdict

from pdftl.core.constants import UNITS
from pdftl.utils.page_specs import page_numbers_matching_page_spec
from pdftl.utils.string_utils import split_string_respecting_quotes
from pdftl.utils.text_templates import (
    compile_text_renderer,
)

logger = logging.getLogger(__name__)

# Set of valid, case-insensitive preset position keywords
PRESET_POSITIONS = {
    "top-left",
    "top-center",
    "top-right",
    "mid-left",
    "center",
    "mid-center",
    "mid-right",
    "bottom-left",
    "bottom-center",
    "bottom-right",
}


def parse_add_text_specs_to_rules(specs: list[str], total_pages: int):
    """
    Parse a list of add_text specifications into a dictionary of rules
    mapping page indices to their specific text-addition instructions.

    A page can have multiple add_text operations, so the dict maps:
        page_index (int, 0-based) -> list[rule_dict]

    Each rule_dict includes a "n" key with the 1-based ordinal of
    that page within the matched pages of its spec, and "count" as a
    backward-compat alias.
    """
    page_rules = defaultdict(list)

    for spec in specs:
        try:
            page_range_part, text_string, options_part = _split_spec_string(spec)

            logger.debug(
                "page_range_part='%s', text_string='%s', options_part='%s'",
                page_range_part,
                text_string,
                options_part,
            )

            rule_dict = _parse_add_text_op(text_string, options_part)

            matched_pages = page_numbers_matching_page_spec(page_range_part, total_pages)
            for count, p_num in enumerate(matched_pages, 1):
                copied_rule = rule_dict.copy()
                copied_rule["n"] = count
                page_rules[p_num - 1].append(copied_rule)

        except ValueError as exc:
            raise ValueError(f"Invalid add_text spec '{spec}': {exc}") from exc

    return dict(page_rules)


# ---------------------------------------------------------------------------
# Spec parsing helpers
# ---------------------------------------------------------------------------


def _find_options_part(s):
    """Find the trailing (options) block in a spec string, if present."""
    options_part = ""
    rest_of_spec = s
    if not s.endswith(")"):
        return options_part, rest_of_spec

    nest_level = 0
    split_pos = -1
    for i in range(len(s) - 1, -1, -1):
        char = s[i]
        if char == ")":
            nest_level += 1
        elif char == "(":
            nest_level -= 1
        if nest_level == 0 and char == "(":
            split_pos = i
            break

    if split_pos != -1:
        options_part = s[split_pos:].strip()
        rest_of_spec = s[:split_pos].strip()

    return options_part, rest_of_spec


def _split_spec_string(spec_str: str):
    """
    Split a raw add_text spec string into (page_range_part, text_string, options_part).

    Syntax: [<page range>]<delimiter><text-string><delimiter>[<options>]
    """
    s = spec_str.strip()
    if not s:
        raise ValueError("Empty add_text spec")

    options_part, rest_of_spec = _find_options_part(s)

    if not rest_of_spec:
        raise ValueError("Missing text string component")

    delimiter = rest_of_spec[-1]
    if delimiter.isalnum() or delimiter in "()":
        raise ValueError(
            f"Invalid text delimiter '{delimiter}'. "
            "Delimiter must be a non-alphanumeric character."
        )
    logger.debug("Found delimiter: '%s'", delimiter)

    first_delim_pos = rest_of_spec.find(delimiter)
    last_delim_pos = len(rest_of_spec) - 1

    if first_delim_pos == last_delim_pos:
        raise ValueError(f"Unmatched text delimiter '{delimiter}'")

    page_range_part = rest_of_spec[:first_delim_pos].strip()
    text_string = rest_of_spec[first_delim_pos + 1 : last_delim_pos]

    if not page_range_part:
        page_range_part = "1-end"

    return page_range_part, text_string, options_part


def _parse_add_text_op(text_string: str, options_part: str):
    """Parse the text string and options part into a structured rule dict."""
    rule = {"text": compile_text_renderer(text_string)}
    options = _parse_options_string(options_part)
    rule.update(options)
    return rule


def _parse_options_string(options_part: str):
    """Parse the (key=value, ...) string into a normalised dictionary."""
    if not options_part:
        return {}

    if not (options_part.startswith("(") and options_part.endswith(")")):
        raise ValueError(
            f"Options block must be enclosed in parentheses, e.g., (...), but got: {options_part}"
        )

    content = options_part[1:-1].strip()
    return _parse_options_content(content)


def _parse_options_content(content: str):
    """Parse the inner content of an options string: "key=val, key2=val2"."""
    if not content:
        return {}

    options_dict = {}

    try:
        parts = split_string_respecting_quotes(content, delimiter=",")
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"Could not parse options: {content}") from exc

    for part in parts:
        part = part.strip()
        if not part:
            continue
        key_val = part.split("=", 1)
        if len(key_val) != 2:
            raise ValueError(f"Invalid option format: '{part}'")
        key, value = key_val
        key = key.strip()
        value = value.strip().strip("'\"")
        if not key:
            raise ValueError(f"Option missing key: '{part}'")
        options_dict[key] = value

    return _normalize_options(options_dict)


def _normalize_options(options_dict: dict):
    """Convert string option values into parsed and validated types."""
    normalized = {}
    options_copy = options_dict.copy()

    if "format" in options_copy:
        normalized["format"] = options_copy.pop("format")
    if "start" in options_copy:
        try:
            normalized["start"] = int(options_copy.pop("start"))
        except ValueError as exc:
            raise ValueError("Variable parameter 'start' must be an integer") from exc

    _normalize_positioning(options_copy, normalized)
    _normalize_layout(options_copy, normalized)
    _normalize_formatting(options_copy, normalized)

    if options_copy:
        raise ValueError(f"Unknown options: {', '.join(options_copy.keys())}")

    return normalized


def _normalize_positioning(options: dict, normalized: dict):
    """Handle 'position', 'x', and 'y' options."""
    position = options.pop("position", None)
    x = options.pop("x", None)
    y = options.pop("y", None)

    if position and (x or y):
        raise ValueError("Cannot specify both 'position' and 'x'/'y' coordinates.")

    if position:
        pos_lower = position.lower()
        if pos_lower not in PRESET_POSITIONS:
            raise ValueError(f"Unknown position '{position}'. Must be one of {PRESET_POSITIONS}")
        if pos_lower == "center":
            pos_lower = "mid-center"
        normalized["position"] = pos_lower

    if x:
        normalized["x"] = _parse_dimension(x)
    if y:
        normalized["y"] = _parse_dimension(y)


def _normalize_layout(options: dict, normalized: dict):
    """Handle 'offset-x', 'offset-y', and 'rotate' options."""
    if "offset-x" in options:
        normalized["offset-x"] = _parse_dimension(options.pop("offset-x"))
    if "offset-y" in options:
        normalized["offset-y"] = _parse_dimension(options.pop("offset-y"))
    if "rotate" in options:
        val = options.pop("rotate")
        try:
            normalized["rotate"] = float(val)
        except ValueError as exc:
            raise ValueError(f"Invalid rotate value: '{val}'") from exc


def _normalize_formatting(options: dict, normalized: dict):
    """Handle 'font', 'size', 'color', 'align', and 'linkcolor' options."""
    if "font" in options:
        normalized["font"] = options.pop("font")
    if "size" in options:
        val = options.pop("size")
        try:
            normalized["size"] = float(val)
        except ValueError as exc:
            raise ValueError(f"Invalid size value: '{val}'") from exc
    if "color" in options:
        normalized["color"] = _parse_color(options.pop("color"))
    if "bgcolor" in options:
        normalized["bgcolor"] = _parse_color(options.pop("bgcolor"))
    if "padding" in options:
        normalized["padding"] = _parse_dimension(options.pop("padding"))
    if "align" in options:
        align_lower = options.pop("align").lower()
        if align_lower not in ("left", "center", "right"):
            raise ValueError(f"Invalid align value: '{align_lower}'")
        normalized["align"] = align_lower
    if "linkcolor" in options:
        normalized["linkcolor"] = _parse_color(options.pop("linkcolor"))


def _find_unit(input_str: str):
    """Find a unit from UNITS in the string."""
    for unit_name in UNITS:
        if input_str.endswith(unit_name):
            return unit_name
    return None


def _parse_dimension(size_str: str):
    """Parse a size string into {'type': 'pt'|'%', 'value': float}."""
    if not isinstance(size_str, str):
        return size_str

    size_str = size_str.strip()
    if size_str.endswith("%"):
        try:
            return {"type": "%", "value": float(size_str[:-1])}
        except ValueError as exc:
            raise ValueError(f"Invalid percentage value: '{size_str}'") from exc

    if unit_name := _find_unit(size_str):
        n = len(unit_name)
        try:
            value = float(size_str[:-n])
            return {"type": "pt", "value": value * UNITS[unit_name]}
        except ValueError as exc:
            raise ValueError(f"Invalid size value: '{size_str}'") from exc
    else:
        try:
            return {"type": "pt", "value": float(size_str)}
        except ValueError as exc:
            raise ValueError(f"Invalid size or unit in dimension: '{size_str}'") from exc


def _parse_color(color_str: str):
    """Parse a space-separated color string into a list of floats."""
    color_str = color_str.strip()
    try:
        parts = [float(c) for c in color_str.split()]
    except ValueError as exc:
        raise ValueError(f"Invalid characters in color string: '{color_str}'") from exc

    num_parts = len(parts)
    if num_parts == 1:
        gray = parts[0]
        return [gray, gray, gray, 1]
    if num_parts == 3:
        parts.append(1)
        return parts
    if num_parts == 4:
        return parts

    raise ValueError(
        f"Color string '{color_str}' must have 1, 3, or 4 space-separated numbers. "
        f"Got {num_parts}."
    )
