# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/parsers/barcode_parser.py

"""Parser for barcode spec strings."""

from collections import defaultdict

from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.keyval_parser import parse_keyval_list
from pdftl.utils.page_specs import page_numbers_matching_page_spec
from pdftl.utils.string_utils import split_string_respecting_quotes
from pdftl.utils.text_templates import compile_text_renderer

_BARCODE_DEFAULTS = {
    "format": "QRCode",
    "scale": "10",
    "width": "72pt",
    "x": "0pt",
    "y": "0pt",
    "position": None,
    "offset-x": "0",
    "offset-y": "0",
}


def _extract_options_block(spec_str: str) -> tuple[str, str]:
    """Extract the trailing (options) block from a spec string."""
    if not spec_str.endswith(")"):
        return "", spec_str

    nest_level = 0
    for i in range(len(spec_str) - 1, -1, -1):
        char = spec_str[i]
        if char == ")":
            nest_level += 1
        elif char == "(":
            nest_level -= 1

        if nest_level == 0 and char == "(":
            options_part = spec_str[i:]
            rest_of_spec = spec_str[:i].strip()
            return options_part, rest_of_spec

    return "", spec_str


def _extract_text_components(spec_str: str) -> tuple[str, str]:
    """Extract the page range and text string based on the delimiter."""
    if not spec_str:
        raise ValueError("Missing text string component")

    delimiter = spec_str[-1]
    if delimiter.isalnum() or delimiter in "()":
        raise ValueError("Delimiter must be a non-alphanumeric character.")

    first_delim_pos = spec_str.find(delimiter)
    last_delim_pos = len(spec_str) - 1

    if first_delim_pos == last_delim_pos:
        raise ValueError(f"Unmatched text delimiter '{delimiter}'")

    page_range = spec_str[:first_delim_pos].strip() or "1-end"
    text_string = spec_str[first_delim_pos + 1 : last_delim_pos]

    return page_range, text_string


def _split_barcode_spec(spec_str: str) -> tuple[str, str, str]:
    """Split a raw spec string into (page_range, text, options)."""
    s = spec_str.strip()
    if not s:
        raise ValueError("Empty barcode spec")

    options_part, rest_of_spec = _extract_options_block(s)
    page_range, text_string = _extract_text_components(rest_of_spec)

    return page_range, text_string, options_part


def _parse_options_string(options_part: str) -> dict:
    """Parse the (key=value, ...) string into a raw dictionary."""
    if not options_part:
        return {}

    content = options_part[1:-1].strip()
    if not content:
        return {}

    parts = split_string_respecting_quotes(content, delimiter=",")

    try:
        raw_parsed = parse_keyval_list(
            parts,
            allowed_keys=list(_BARCODE_DEFAULTS.keys()),
            lowercase_keys=True,
            context="barcode option",
        )
    except InvalidArgumentError as exc:
        # Re-raise as ValueError so the orchestrator catches and wraps it with the spec context
        raise ValueError(str(exc)) from exc

    # Strip surrounding quotes from values
    stripped = {k: v.strip("'\"") for k, v in raw_parsed.items()}

    ret = stripped
    if "position" in stripped and stripped["position"] == "center":
        ret["position"] = "mid-center"
    return ret


def _validate_and_merge_options(parsed_options: dict) -> dict:
    """Merge parsed options with defaults and validate them."""
    final_options = _BARCODE_DEFAULTS.copy()
    final_options.update(parsed_options)

    if final_options["position"] and ("x" in parsed_options or "y" in parsed_options):
        raise ValueError("Cannot specify both 'position' and 'x'/'y' coordinates.")

    try:
        scale_val = int(final_options["scale"])
        if scale_val <= 0:
            raise ValueError
        final_options["scale"] = scale_val
    except ValueError as exc:
        raise ValueError("Scale must be a positive integer.") from exc

    return final_options


def parse_barcode_specs_to_rules(specs: list[str], total_pages: int) -> dict:
    """Parse a list of barcode specifications into per-page rule dicts."""
    page_rules = defaultdict(list)

    for spec in specs:
        try:
            page_range, text_string, options_part = _split_barcode_spec(spec)

            raw_options = _parse_options_string(options_part)
            validated_options = _validate_and_merge_options(raw_options)

            rule = {"text_renderer": compile_text_renderer(text_string), **validated_options}

            matched_pages = page_numbers_matching_page_spec(page_range, total_pages)
            for count, p_num in enumerate(matched_pages, 1):
                copied_rule = rule.copy()
                copied_rule["n"] = count
                page_rules[p_num - 1].append(copied_rule)

        except ValueError as exc:
            raise InvalidArgumentError(f"Invalid barcode spec '{spec}': {exc}") from exc

    return dict(page_rules)
