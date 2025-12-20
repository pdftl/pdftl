# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/parsers/add_text_parser.py

"""Parser for add_text arguments"""

import logging
import re
from collections import defaultdict

logger = logging.getLogger(__name__)

from pdftl.core.constants import UNITS

# We import the same utilities as chop_parser
from pdftl.utils.page_specs import parse_page_spec

# Set of valid, case-insensitive preset position keywords
PRESET_POSITIONS = {
    "top-left",
    "top-center",
    "top-right",
    "mid-left",
    "mid-center",
    "mid-right",
    "bottom-left",
    "bottom-center",
    "bottom-right",
}

# Regex to split by commas, but not inside single or double quotes
COMMA_SPLIT_REGEX = re.compile(r",(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)(?=(?:[^']*'[^']*')*[^']*$)")

# Regex to capture either an escaped block {{...}} OR a variable block {...}
TOKEN_REGEX = re.compile(r"(\{\{.*?\}\}|\{.*?\})")

# Regex for parsing the *inside* of a variable block
# 1: (page) (2: -) (3: 1)
VAR_EXPR_REGEX = re.compile(r"^\s*(\w+)\s*([+-])\s*(\d+)\s*$")
# 1: (total-page)
COMPLEX_VAR_REGEX = re.compile(r"^\s*(total-page)\s*$")
# 1: (meta:Title)
META_VAR_REGEX = re.compile(r"^\s*(meta:\w+)\s*$", re.IGNORECASE)
# 1: (page)
SIMPLE_VAR_REGEX = re.compile(r"^\s*(\w+)\s*$")

# Define the set of known simple variables
KNOWN_SIMPLE_VARS = {
    "page",
    "total",
    "filename",
    "filename_base",
    "filepath",
    "date",
    "time",
    "datetime",
}


def parse_add_text_specs_to_rules(specs: list[str], total_pages: int):
    """
    Parses a list of add_text specifications into a dictionary of rules
    mapping page indices to their specific text-addition instructions.

    Unlike chop, a page can have *multiple* add_text operations, so the
    dictionary maps:
        page_index (int) -> list[rule_dict (dict)]
    """
    page_rules = defaultdict(list)

    # 1. Pre-process specs to handle 'even'/'odd' keywords cleanly.
    grouped_specs = _group_specs_with_qualifiers(specs)

    for spec_str, keyword_qualifiers in grouped_specs:
        # 2. Parse this single spec into its rules and page numbers
        try:
            rules_for_spec = _parse_one_spec_to_rules(spec_str, keyword_qualifiers, total_pages)
            # 3. Merge these rules into the main dictionary
            for page_index, rule in rules_for_spec:
                page_rules[page_index].append(rule)
        except ValueError as exc:
            raise ValueError(f"Invalid add_text spec '{spec_str}': {exc}") from exc

    return dict(page_rules)


def _parse_one_spec_to_rules(spec_str: str, keyword_qualifiers: str | None, total_pages: int):
    """
    Parses a single spec string and returns a list of (page_index, rule) tuples.
    This helper function exists to reduce the number of local variables in
    the main `parse_add_text_specs_to_rules` loop.
    """
    # 1. Split the spec into its three main parts.
    page_range_part, text_string, options_part = _split_spec_string(spec_str)
    logger.debug(
        "page_range_part='%s', text_string='%s', options_part='%s'",
        page_range_part,
        text_string,
        options_part,
    )

    # 2. Parse the page range to get numbers and a potential qualifier.
    page_spec = parse_page_spec(page_range_part, total_pages)
    start, end, range_qualifiers = (
        page_spec.start,
        page_spec.end,
        page_spec.qualifiers,
    )

    # 3. Determine the final qualifier (the range qualifier takes precedence).
    final_qualifiers = range_qualifiers or keyword_qualifiers

    # 4. Generate the list of affected page numbers.
    page_numbers = _get_qualified_page_numbers(start, end, final_qualifiers)

    # 5. Parse the operation string into a structured rule dictionary.
    rule_dict = _parse_add_text_op(text_string, options_part)

    # 6. Apply the parsed rule to all generated page numbers.
    #    We convert from 1-based page number to 0-based index.
    return [(p_num - 1, rule_dict) for p_num in page_numbers]


##################################################
# SPEC PARSING HELPERS
##################################################


def _find_options_part(s):
    # Find the options_part (if it exists) by searching from the right.
    # As per the prompt, we assume if a balanced (...) block exists at
    # the end, it is the options block.
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
            # Found the start of the balanced block
            split_pos = i
            break

    if split_pos != -1:
        # We found a balanced block. Treat it as the options.
        options_part = s[split_pos:].strip()
        rest_of_spec = s[:split_pos].strip()

    return options_part, rest_of_spec


def _split_spec_string(spec_str: str):
    """
    Splits a raw add_text spec string into its constituent parts,
    based on a robust right-to-left parsing algorithm.
    Syntax: [<page range>]<delimiter><text-string><delimiter>[<options>]

    Returns a tuple: (page_range_part, text_string, options_part)
    """
    s = spec_str.strip()
    if not s:
        raise ValueError("Empty add_text spec")

    # 1. Find the options_part (if it exists)
    options_part, rest_of_spec = _find_options_part(s)

    if not rest_of_spec:
        raise ValueError("Missing text string component")

    # 2. Find the delimiter. It's the last character of the remaining string.
    delimiter = rest_of_spec[-1]
    if delimiter.isalnum() or delimiter in "()":
        raise ValueError(
            f"Invalid text delimiter '{delimiter}'. "
            "Delimiter must be a non-alphanumeric character."
        )
    logger.debug("Found delimiter: '%s'", delimiter)

    # 3. Find the *first* occurrence of the delimiter to split
    #    page_range from the text_string.

    # We use `rfind` to find the last delimiter (which we know is at the end)
    # and `find` to find the first.
    first_delim_pos = rest_of_spec.find(delimiter)
    last_delim_pos = len(rest_of_spec) - 1  # We already know this is the delimiter

    if first_delim_pos == last_delim_pos:
        # Only one delimiter was found (e.g., "1-5/text").
        # This is an unmatched delimiter error.
        raise ValueError(f"Unmatched text delimiter '{delimiter}'")

    # 4. Extract the three parts based on the delimiter positions
    page_range_part = rest_of_spec[:first_delim_pos].strip()
    text_string = rest_of_spec[first_delim_pos + 1 : last_delim_pos]

    # 5. Apply default page range if it was omitted
    if not page_range_part:
        page_range_part = "1-end"

    return page_range_part, text_string, options_part


def _parse_add_text_op(text_string: str, options_part: str):
    """
    Parses the text string and options part into a structured rule dict.
    """
    rule = {"text": _compile_text_renderer(text_string)}
    options = _parse_options_string(options_part)
    rule.update(options)
    return rule


def _parse_options_string(options_part: str):
    """
    Parses the (key=value, ...) string into a normalized dictionary.
    """
    if not options_part:
        return {}  # No options provided

    if not (options_part.startswith("(") and options_part.endswith(")")):
        raise ValueError(
            "Options block must be enclosed in parentheses, "
            f"e.g., (...), but got: {options_part}"
        )

    content = options_part[1:-1].strip()
    if not content:
        return {}  # Empty parentheses

    # Replace the permissive re.findall with a strict, two-pass parser.
    options_dict = {}

    # 1. Split by commas, but respect commas inside quotes.
    try:
        parts = COMMA_SPLIT_REGEX.split(content)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"Could not parse options: {content}") from exc

    for part in parts:
        part = part.strip()
        if not part:
            continue  # Skip empty parts (e.g., from "foo=bar,,baz=qux")

        # 2. Split *each part* on the first '='
        key_val = part.split("=", 1)

        # 3. NOW we can find the error you pointed out.
        if len(key_val) != 2:
            raise ValueError(f"Invalid option format: '{part}'")

        key, value = key_val
        key = key.strip()
        value = value.strip().strip("'\"")  # Un-quote and strip

        if not key:
            raise ValueError(f"Option missing key: '{part}'")

        options_dict[key] = value

    # Normalize values (e.g., convert "10pt" to a structured dict)
    return _normalize_options(options_dict)


def _normalize_options(options_dict: dict):
    """
    Converts a dictionary of string values into a structured dict with
    parsed and validated types (dimensions, floats, etc.).
    """
    normalized = {}
    options_copy = options_dict.copy()

    # Refactored to reduce branch complexity.
    # Each helper function pops keys from options_copy as it handles them.
    _normalize_positioning(options_copy, normalized)
    _normalize_layout(options_copy, normalized)
    _normalize_formatting(options_copy, normalized)

    # --- Error on unknown ---
    if options_copy:
        raise ValueError(f"Unknown options: {', '.join(options_copy.keys())}")

    return normalized


def _normalize_positioning(options: dict, normalized: dict):
    """Handles 'position', 'x', and 'y' options."""
    position = options.pop("position", None)
    x = options.pop("x", None)
    y = options.pop("y", None)

    if position and (x or y):
        raise ValueError("Cannot specify both 'position' and 'x'/'y' coordinates.")

    if position:
        pos_lower = position.lower()
        if pos_lower not in PRESET_POSITIONS:
            raise ValueError(f"Unknown position '{position}'. Must be one of {PRESET_POSITIONS}")
        normalized["position"] = pos_lower

    if x:
        normalized["x"] = _parse_dimension(x)
    if y:
        normalized["y"] = _parse_dimension(y)


def _normalize_layout(options: dict, normalized: dict):
    """Handles 'offset-x', 'offset-y', and 'rotate' options."""
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
    """Handles 'font', 'size', 'color', and 'align' options."""
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
    if "align" in options:
        align_lower = options.pop("align").lower()
        if align_lower not in ("left", "center", "right"):
            raise ValueError(f"Invalid align value: '{align_lower}'")
        normalized["align"] = align_lower


def _parse_dimension(size_str: str):
    """
    Parses a size string (e.g., "10pt", "5%", "1cm") into a structured
    dict: {'type': 'pt' | '%', 'value': float}.
    """
    if not isinstance(size_str, str):
        return size_str  # Already parsed, e.g., from a test

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
            # Resolve all units to 'pt' immediately
            return {"type": "pt", "value": value * UNITS[unit_name]}
        except ValueError as exc:
            raise ValueError(f"Invalid size value: '{size_str}'") from exc
    else:
        # Default to 'pt'
        try:
            return {"type": "pt", "value": float(size_str)}
        except ValueError as exc:
            raise ValueError(f"Invalid size or unit in dimension: '{size_str}'") from exc


# In add_text_parser.py, replace the _parse_color function


def _parse_color(color_str: str):
    """
    Parses a space-separated color string into a list of floats.
    - "0.2"       -> [0.2, 0.2, 0.2]  (Gray to RGB)
    - "1 0 0"     -> [1.0, 0.0, 0.0]  (RGB)
    - "0 0 1 0.5" -> [0.0, 0.0, 1.0, 0.5] (RGBA)
    """
    color_str = color_str.strip()

    try:
        # Split by spaces and convert all parts to float
        parts = [float(c) for c in color_str.split()]
    except ValueError as exc:
        # This will catch "blue", "1 0 1a", etc.
        raise ValueError(f"Invalid characters in color string: '{color_str}'") from exc

    num_parts = len(parts)

    if num_parts == 1:
        # Grayscale: expand [g] to [g, g, g]
        gray = parts[0]
        return [gray, gray, gray, 1]

    if num_parts == 3:
        # RGB: [r, g, b]
        parts.append(1)
        return parts

    if num_parts == 4:
        # RGBA: [r, g, b, a]
        return parts

    # If we get here, it's the wrong number of components
    raise ValueError(
        f"Color string '{color_str}' must have 1 (Gray), 3 (RGB), or 4 (RGBA) "
        f"space-separated numbers. Got {num_parts}."
    )


# def _parse_color(color_str: str):
#     """
#     Parses a color string.
#     - 'red' -> 'red'
#     - '0.5 0.5 0.5' -> [0.5, 0.5, 0.5]
#     """
#     color_str = color_str.strip()
#     if " " in color_str:
#         try:
#             return [float(c) for c in color_str.split()]
#         except ValueError as exc:
#             raise ValueError(f"Invalid RGB/CMYK color: '{color_str}'") from exc

#     # Assume it's a named color (e.g., 'red') or hex (which pikepdf handles)
#     return color_str


##################################################
# TEXT VARIABLE PARSING
##################################################


def _parse_var_expression(expr: str):
    """
    Parses the inner content of a {variable} block into a token.
    'page-1' -> ('page', '-', 1)
    'total'  -> ('total', None, 0)
    'meta:Title' -> ('meta:Title', None, 0)
    """
    # 1. Check for complex, hardcoded values
    if COMPLEX_VAR_REGEX.fullmatch(expr):
        return ("total-page", None, 0)

    # 2. Check for simple arithmetic: var+1, var-2
    match = VAR_EXPR_REGEX.fullmatch(expr)
    if match:
        var, op, val = match.groups()
        var_low = var.lower()
        # Make sure we're doing math on a known numeric var
        if var_low not in ("page", "total"):
            raise ValueError(f"Cannot apply arithmetic to non-numeric variable: {var}")
        return (var_low, op, int(val))

    # 3. Check for metadata variable
    match = META_VAR_REGEX.fullmatch(expr)
    if match:
        var = match.group(1)
        # We need to normalize the 'meta:' part to lowercase but keep the key case
        # 'mEtA:Title' -> 'meta:Title'
        parts = var.split(":", 1)
        normalized_var = f"meta:{parts[1]}"
        return (normalized_var, None, 0)

    # 4. Check for simple variable
    match = SIMPLE_VAR_REGEX.fullmatch(expr)
    if match:
        var = match.group(1).lower()
        # Check if the matched variable is in our known set
        if var in KNOWN_SIMPLE_VARS:
            return (var, None, 0)

    raise ValueError(f"Unknown variable expression: {{{expr}}}")


def _evaluate_token(token: tuple, context: dict):
    """
    Evaluates a single parsed token against the runtime context.
    """
    var, op, val = token

    # 1. Handle complex 'total-page'
    if var == "total-page":
        return context.get("total", 0) - context.get("page", 0)

    # 2. Handle metadata
    if var.startswith("meta:"):
        meta_key = var[5:]  # Get 'Title' from 'meta:Title'
        # Return the value from the metadata dict, or an empty string
        return context.get("metadata", {}).get(meta_key, "")

    # 3. Handle simple and arithmetic vars
    base_value = context.get(var, 0)
    if not isinstance(base_value, (int, float)):
        # Handle non-numeric context values (e.g., 'filename')
        if op is None:
            return base_value
        # Can't do math on 'filename'
        raise ValueError(f"Cannot apply arithmetic to variable: {var}")

    if op == "+":
        return base_value + val
    if op == "-":
        return base_value - val

    # No operator, just return the base value
    return base_value


def _tokenize_text_string(text_str: str) -> list:
    """
    Splits the text string into a list of literals and parsed tokens.
    "Page {page}" -> ["Page ", ("page", None, 0)]
    """
    parts = []
    # Split the string by our token regex.
    # This gives a list like: [LITERAL, TOKEN, LITERAL, TOKEN, ...]
    split_parts = TOKEN_REGEX.split(text_str)

    for i, part in enumerate(split_parts):
        if not part:  # re.split can leave empty strings
            continue

        is_token = i % 2 == 1  # Literals are at even indices

        if not is_token:
            parts.append(part)  # It's a literal string
        elif part.startswith("{{"):
            parts.append(part[1:-1])  # It's an escaped literal
        else:
            parts.append(_parse_var_expression(part[1:-1]))  # It's a var

    logger.debug("parts=%s", parts)
    return parts


def _default_renderer(parts: list, context: dict) -> str:
    """
    Renders a pre-compiled list of parts against a context dict.
    """
    result = []
    for part in parts:
        if isinstance(part, str):
            result.append(part)
        else:
            # It's a token tuple, evaluate it
            result.append(str(_evaluate_token(part, context)))
    return "".join(result)


def _compile_text_renderer(text_str: str):
    """
    Parses and "compiles" a text string into a render function.
    The returned function takes a context dict and returns a final string.
    """
    # 1. Parse the string into tokens *once*
    parts = _tokenize_text_string(text_str)

    # 2. Return a simple lambda that calls the renderer with those tokens
    return lambda context: _default_renderer(parts, context)


##################################################
# GENERIC HELPERS (replicated from chop_parser.py)
##################################################


def _find_unit(input_str: str):
    """Find a unit from UNITS in the string"""
    for unit_name in UNITS:
        if input_str.endswith(unit_name):
            return unit_name
    return None


def _group_specs_with_qualifiers(specs):
    """
    Pre-processes the specs list to pair qualifiers ('even', 'odd')
    with the spec string that follows them.
    Returns a list of tuples: [(spec_str, qualifier_keyword), ...].
    """
    logger.debug("got specs=%s", specs)
    grouped_specs = []
    specs_iterator = iter(specs)
    for spec in specs_iterator:
        is_qualifier = spec.lower() in ("even", "odd")
        if is_qualifier:
            try:
                # The qualifier applies to the *next* spec string.
                next_spec = next(specs_iterator)
                grouped_specs.append((next_spec, spec.lower()))
            except StopIteration as exc:
                raise ValueError(f"Missing spec after '{spec}' keyword.") from exc
        else:
            # This spec has no preceding keyword qualifier.
            grouped_specs.append((spec, None))
    logger.debug("returning grouped_specs=%s", grouped_specs)
    return grouped_specs


def _get_qualified_page_numbers(start, end, qualifier):
    """
    Generates a list of page numbers for a given range, filtered by a qualifier.
    """
    step = 1 if start <= end else -1
    full_range = list(range(start, end + step, step))

    if qualifier == "even":
        return [p for p in full_range if p % 2 == 0]
    if qualifier == "odd":
        return [p for p in full_range if p % 2 != 0]

    return full_range
