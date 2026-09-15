# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/parsers/modify_annots_parser.py

"""Parser for modify_annots arguments"""

import logging
import re
from dataclasses import dataclass

from pdftl.utils.page_specs import page_numbers_matching_page_spec
from pdftl.utils.string_utils import split_string_respecting_quotes

logger = logging.getLogger(__name__)

# --- This logic is borrowed directly from add_text_parser.py ---
# We use it to parse the (modifications) string.


def _unquote_string(val: str) -> str:
    """Helper to remove one layer of quotes from a string."""
    if (val.startswith("'") and val.endswith("'")) or (val.startswith('"') and val.endswith('"')):
        return val[1:-1]
    return val


# Sentinel used as the "value" half of a bare-key selector (no "=Value"),
# e.g. "A" inside "/Link(A)". Means "annotation has this property" rather
# than a value comparison. Only produced when require_keyval=False.
EXISTS = object()


def _parse_kv_pair(part: str, require_keyval: bool = True) -> tuple[str, str]:
    """
    Parses a single 'Key=Value' string, respecting quotes in the value.
    (Adapted from add_text_parser._parse_kv_pair)

    If require_keyval is False, a bare key with no '=' (e.g. "A") is
    accepted and returned as (key, EXISTS) — an existence check rather
    than a value comparison. If require_keyval is True (default), a
    bare key raises ValueError as before.
    """
    all_parts = split_string_respecting_quotes(part, delimiter="=")
    if len(all_parts) < 2:
        if not require_keyval:
            key = part.strip()
            if not key:
                raise ValueError(f"Invalid modification: '{part}'. Key cannot be empty.")
            return key, EXISTS
        raise ValueError(f"Invalid modification: '{part}'. Expected format 'Key=Value'.")

    key = all_parts[0].strip()
    if not key:
        raise ValueError(f"Invalid modification: '{part}'. Key cannot be empty.")
    value = _unquote_string(("=".join(all_parts[1:])).strip())
    return key, value


def _split_spec(spec: str) -> tuple[str, str | None]:
    """Split 'selector(mods)' into ('selector', 'mods') or ('selector', None)."""
    cleaned = spec.strip()
    paren_pos = cleaned.find("(")
    if paren_pos == -1:
        return cleaned, None
    if not cleaned.endswith(")"):
        raise ValueError(
            f"Invalid modification spec format: '{spec}'. "
            "Expected a format like 'selector(Key=Value, ...)' or just 'selector'."
        )
    return cleaned[:paren_pos], cleaned[paren_pos + 1 : -1]


@dataclass
class ModificationRule:
    """Dataclass to hold a single modification rule."""

    page_numbers: list[int]
    type_selector: str | None
    modifications: list[tuple[str, str]]


@dataclass
class SelectionRule:
    """Dataclass to hold a single selection rule."""

    page_numbers: list[int]
    type_selector: str | None
    value_selectors: list[tuple[str, str]] | None


def _parse_modification_string(mod_str: str, require_keyval: bool = True) -> list[tuple[str, str]]:
    """
    Parses the comma-separated key=value string from inside the parentheses.
    e.g., "Border=null, Foo=bar, 'T=(New Author Name)'"
    If require_keyval is False, bare keys (no '=') are accepted as
    existence checks — see _parse_kv_pair.
    """
    if not mod_str:
        raise ValueError("Empty modification list '()'. Must specify modifications.")

    mod_parts = split_string_respecting_quotes(mod_str, delimiter=",")
    modifications = []
    for part in mod_parts:
        if part.strip():
            modifications.append(_parse_kv_pair(part, require_keyval=require_keyval))
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


def specs_to_selection_rules(specs: list[str], total_pages: int) -> list[SelectionRule]:
    mod_rules = specs_to_modification_rules(specs, total_pages, require_keyval=False)
    return [
        SelectionRule(
            page_numbers=x.page_numbers,
            type_selector=x.type_selector,
            value_selectors=x.modifications or None,
        )
        for x in mod_rules
    ]


def specs_to_modification_rules(
    specs: list[str], total_pages: int, require_keyval: bool = True
) -> list[ModificationRule]:
    """
    Main parser for the modify_annots and dump_annots operations.
    Converts a list of spec strings into a list of ModificationRule objects.
    If require_keyval is True (default), raises if no (K=V...) part is present.
    If require_keyval is False, modifications will be an empty list
    (used as filters in dump_annots).
    """
    rules = []
    for spec in specs:
        if not isinstance(spec, str):
            raise ValueError("Invalid spec: not a string")

        selector_str, mod_str = _split_spec(spec)

        logger.debug(
            "Parsing modify_annots spec: selector='%s', modifications='%s'",
            selector_str,
            mod_str,
        )

        if require_keyval and not mod_str:
            raise ValueError(
                f"Invalid modification spec format: '{spec}'. "
                "Expected a format like 'selector(Key=Value, ...)'."
            )

        page_spec, type_selector = _parse_selector_string(selector_str.strip())
        modifications = (
            _parse_modification_string(mod_str, require_keyval=require_keyval) if mod_str else []
        )
        page_numbers = page_numbers_matching_page_spec(page_spec, total_pages)

        rules.append(ModificationRule(page_numbers, type_selector, modifications))
        logger.debug(
            "Parsed rule: pages=%s, type=%s, mods=%s",
            page_numbers,
            type_selector,
            modifications,
        )

    return rules
