# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/parsers/modify_annots_parser.py

"""Parser for modify_annots arguments"""

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

from pdftl.utils.page_specs import page_numbers_matching_page_spec

# --- This logic is borrowed directly from add_text_parser.py ---
# We use it to parse the (modifications) string.

# Regex to split by commas, but not inside single or double quotes
# This regex should correctly handle balanced, unescaped quotes.
COMMA_SPLIT_REGEX = re.compile(r",(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)(?=(?:[^']*'[^']*')*[^']*$)")

# Regex to find the first unquoted/unescaped '='
# This regex should correctly handle balanced, unescaped quotes.
EQUALS_SPLIT_REGEX = re.compile(r"=(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)(?=(?:[^']*'[^']*')*[^']*$)")


def _unquote_string(val: str) -> str:
    """Helper to remove one layer of quotes from a string."""
    if (val.startswith("'") and val.endswith("'")) or (val.startswith('"') and val.endswith('"')):
        return val[1:-1]
    return val


def _parse_kv_pair(part: str) -> tuple[str, str]:
    """
    Parses a single 'Key=Value' string, respecting quotes in the value.
    (Adapted from add_text_parser._parse_kv_pair)
    """
    parts = EQUALS_SPLIT_REGEX.split(part, 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid modification: '{part}'. Expected format 'Key=Value'.")

    key = parts[0].strip()
    if not key:
        raise ValueError(f"Invalid modification: '{part}'. Key cannot be empty.")
    value = _unquote_string(parts[1].strip())
    return key, value


# --- Parsing logic for modify_annots ---

# This regex is the core pattern from crop_parser.py
# It matches 'selector(mods)'
# MOVED to module level for performance (compile once)
# and to allow tests to access it.
spec_pattern = re.compile(r"^([^(]*)?\((.*?)\)$")


@dataclass
class ModificationRule:
    """Dataclass to hold a single modification rule."""

    page_numbers: set[int]
    type_selector: str | None
    modifications: list[tuple[str, str]]


def _parse_modification_string(mod_str: str) -> list[tuple[str, str]]:
    """
    Parses the comma-separated key=value string from inside the parentheses.
    e.g., "Border=null, Foo=bar, 'T=(New Author Name)'"
    """
    if not mod_str:
        raise ValueError("Empty modification list '()'. Must specify modifications.")

    mod_parts = COMMA_SPLIT_REGEX.split(mod_str)
    modifications = []
    for part in mod_parts:
        if part.strip():
            modifications.append(_parse_kv_pair(part))
    return modifications


def _parse_selector_string(selector_str: str) -> tuple[str, str | None]:
    """
    Parses the selector part.
    e.g., "1-4/Link" -> ("1-4", "/Link")
    e.g., "/Text"    -> ("1-end", "/Text")
    e.g., "odd"      -> ("odd", None)
    """
    if not selector_str:
        # Default for empty selector, e.g., "(Border=null)"
        return "1-end", None

    # Regex to find the /Type selector, but not at the very start
    # if it's part of a page spec (e.g., "1-4/Link")
    type_match = re.search(r"(?<!^)(/\w+)", selector_str)

    if type_match:
        type_spec = type_match.group(1)
        page_spec = selector_str[: type_match.start()] or "1-end"
    elif selector_str.startswith("/"):
        type_spec = selector_str
        page_spec = "1-end"
    else:
        # No type selector found, must be just a page spec
        type_spec = None
        page_spec = selector_str

    return page_spec, type_spec


def specs_to_modification_rules(specs: list[str], total_pages: int) -> list[ModificationRule]:
    """
    Main parser for the modify_annots operation.
    Converts a list of spec strings into a list of ModificationRule objects.
    """
    rules = []

    for spec in specs:
        if not (match := spec_pattern.match(spec)):
            raise ValueError(
                f"Invalid modification spec format: '{spec}'. "
                "Expected a format like 'selector(Key=Value, ...)'."
            )

        selector_str, mod_str = match.groups()
        logger.debug(
            "Parsing modify_annots spec: selector='%s', modifications='%s'",
            selector_str,
            mod_str,
        )

        page_spec, type_selector = _parse_selector_string(selector_str.strip())
        modifications = _parse_modification_string(mod_str)

        page_numbers = page_numbers_matching_page_spec(page_spec, total_pages)

        rules.append(ModificationRule(page_numbers, type_selector, modifications))
        logger.debug(
            "Parsed rule: pages=%s, type=%s, mods=%s",
            page_numbers,
            type_selector,
            modifications,
        )

    return rules
