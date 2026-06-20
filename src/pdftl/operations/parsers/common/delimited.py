# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/parsers/common/delimited.py

"""
Shared parsing utilities for page-range delimited operation rules.

This module provides common parsing mechanisms for the pdftl DSL architecture,
reducing duplication across operations (like add_text and add_images).
"""

from __future__ import annotations

import logging
from pdftl.utils.string_utils import split_string_respecting_quotes

logger = logging.getLogger(__name__)


def extract_trailing_options(raw_rule_str: str) -> tuple[str, str]:
    """Finds and extracts the trailing parentheses (options) block if present."""
    options_part = ""
    remaining_str = raw_rule_str
    if not raw_rule_str.endswith(")"):
        return options_part, remaining_str

    nest_level = 0
    split_pos = -1
    for i in range(len(raw_rule_str) - 1, -1, -1):
        char = raw_rule_str[i]
        if char == ")":
            nest_level += 1
        elif char == "(":
            nest_level -= 1
        if nest_level == 0 and char == "(":
            split_pos = i
            break

    if split_pos != -1:
        options_part = raw_rule_str[split_pos:].strip()
        remaining_str = raw_rule_str[:split_pos].strip()

    return options_part, remaining_str


def split_delimited_rule(raw_rule_str: str, label: str = "component") -> tuple[str, str, str]:
    """
    Splits a raw rule string into (page_range, main_body, options).

    Syntax: [<page range>]<delimiter><main_body><delimiter>[<options>]
    """
    cleaned_str = raw_rule_str.strip()
    if not cleaned_str:
        raise ValueError(f"Empty raw rule for {label}")

    options_part, remaining_str = extract_trailing_options(cleaned_str)

    if not remaining_str:
        raise ValueError(f"Missing {label} body component")

    delimiter = remaining_str[-1]
    if delimiter.isalnum() or delimiter in "()":
        raise ValueError(
            f"Invalid {label} delimiter '{delimiter}'. "
            "Delimiter must be a single non-alphanumeric character."
        )

    first_delim_pos = remaining_str.find(delimiter)
    last_delim_pos = len(remaining_str) - 1

    if first_delim_pos == last_delim_pos:
        raise ValueError(f"Unmatched {label} delimiter '{delimiter}'")

    page_range_part = remaining_str[:first_delim_pos].strip()
    main_body_part = remaining_str[first_delim_pos + 1 : last_delim_pos].strip()

    if not page_range_part:
        page_range_part = "1-end"

    return page_range_part, main_body_part, options_part


def parse_options_string(options_part: str) -> dict[str, str]:
    """Parses the outer '(key=val, ...)' string into a key-value dict."""
    if not options_part:
        return {}

    if not (options_part.startswith("(") and options_part.endswith(")")):
        raise ValueError(
            f"Options block must be enclosed in parentheses, e.g., (...), but got: {options_part}"
        )

    content = options_part[1:-1].strip()
    if not content:
        return {}

    options_dict = {}
    try:
        parts = split_string_respecting_quotes(content, delimiter=",")
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"Could not parse options block: {content}") from exc

    for part in parts:
        part = part.strip()
        if not part:
            continue
        key_val = part.split("=", 1)
        if len(key_val) != 2:
            raise ValueError(f"Invalid option format: '{part}'")
        key, value = key_val
        key = key.strip().lower()
        value = value.strip().strip("'\"")
        if not key:
            raise ValueError(f"Option missing key in: '{part}'")
        options_dict[key] = value

    return options_dict
