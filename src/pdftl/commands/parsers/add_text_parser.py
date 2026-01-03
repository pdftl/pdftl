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
from pdftl.utils.page_specs import parse_specs

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

NUMERIC_VARS = {"page", "total", "source_page", "source_rotation", "source_width", "source_height"}

# Regex to split by commas, but not inside single or double quotes
COMMA_SPLIT_REGEX = re.compile(r",(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)(?=(?:[^']*'[^']*')*[^']*$)")

# Regex to capture either an escaped block {{...}} OR a variable block {...}
TOKEN_REGEX = re.compile(r"(\{\{.*?\}\}|\{.*?\})")

# 1: (total-page)
COMPLEX_VAR_REGEX = re.compile(r"^\s*(total-page)\s*$")

# 1: (meta:Title)
META_VAR_REGEX = re.compile(r"^\s*(meta:\w+)\s*$", re.IGNORECASE)

# MASTER REGEX: Handles Var, optional Arithmetic, and optional Formatting
# Capture Groups:
#   var: The variable name (e.g. 'page')
#   op:  The operator (e.g. '+')
#   num: The operand (e.g. '5000')
#   fmt: The python format string (e.g. '06d')
# Examples: "{page}", "{page+1}", "{page:06d}", "{page+5000:06d}"
MASTER_VAR_REGEX = re.compile(
    r"^\s*(?P<var>[a-zA-Z_]\w*)"  # Variable name
    r"(?:\s*(?P<op>[+-])\s*(?P<num>\d+))?"  # Optional Arithmetic (+/- int)
    r"(?::(?P<fmt>.+))?"  # Optional Format Specifier (start with :)
    r"\s*$"
)

# Define the set of known simple variables
KNOWN_VARS = {
    "page",
    "total",
    "filename",
    "filename_base",
    "filepath",
    "date",
    "time",
    "datetime",
    # Source metadata variables
    "source_filename",
    "source_path",
    "source_page",
    "source_rotation",
    "source_width",
    "source_height",
    "source_orientation",
    "source_cropbox",
    "source_mediabox",
    "source_filesize",
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

    for spec_str, keyword_qualifier in grouped_specs:
        try:
            # 2. Split the spec into its three main parts.
            #    e.g. "1-5/my text/(pos=top)" -> "1-5", "my text", "(pos=top)"
            page_range_part, text_string, options_part = _split_spec_string(spec_str)

            logger.debug(
                "page_range_part='%s', text_string='%s', options_part='%s'",
                page_range_part,
                text_string,
                options_part,
            )

            # 3. Parse the operation string into a structured rule dictionary ONCE.
            rule_dict = _parse_add_text_op(text_string, options_part)

            # 4. Use the central parser to resolve the page selection.
            #    We pass the page_range_part as a single-element list.
            for page_spec in parse_specs([page_range_part], total_pages):

                # 5. Generate the list of affected page numbers
                step = 1 if page_spec.start <= page_spec.end else -1
                page_numbers = list(range(page_spec.start, page_spec.end + step, step))

                # Filter: Internal Qualifiers (from [1-5]even syntax)
                if "even" in page_spec.qualifiers:
                    page_numbers = [p for p in page_numbers if p % 2 == 0]
                if "odd" in page_spec.qualifiers:
                    page_numbers = [p for p in page_numbers if p % 2 != 0]

                # Filter: External Keyword Qualifier (legacy "even 1-5..." syntax)
                if keyword_qualifier == "even":
                    page_numbers = [p for p in page_numbers if p % 2 == 0]
                elif keyword_qualifier == "odd":
                    page_numbers = [p for p in page_numbers if p % 2 != 0]

                # Filter: Omissions
                for om_start, om_end in page_spec.omissions:
                    page_numbers = [p for p in page_numbers if not om_start <= p <= om_end]

                # 6. Apply the parsed rule to all generated page numbers.
                for p_num in page_numbers:
                    # Convert from 1-based page number to 0-based index.
                    page_rules[p_num - 1].append(rule_dict)

        except ValueError as exc:
            raise ValueError(f"Invalid add_text spec '{spec_str}': {exc}") from exc

    return dict(page_rules)


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
        # If it doesn't look like options, it might be part of the text if parsed wrongly,
        # but here we expect strictly options or empty.
        raise ValueError(
            f"Options block must be enclosed in parentheses, e.g., (...), but got: {options_part}"
        )

    content = options_part[1:-1].strip()
    return _parse_options_content(content)


def _parse_options_content(content: str):
    """
    Parses the inner content of an options string: "key=val, key2=val2".
    Used by both the main command options and variable parameter parsing.
    """
    if not content:
        return {}

    options_dict = {}

    # 1. Split by commas, but respect commas inside quotes.
    try:
        parts = COMMA_SPLIT_REGEX.split(content)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"Could not parse options: {content}") from exc

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # 2. Split *each part* on the first '='
        key_val = part.split("=", 1)

        if len(key_val) != 2:
            raise ValueError(f"Invalid option format: '{part}'")

        key, value = key_val
        key = key.strip()
        value = value.strip().strip("'\"")  # Un-quote and strip

        if not key:
            raise ValueError(f"Option missing key: '{part}'")

        options_dict[key] = value

    return _normalize_options(options_dict)


def _normalize_options(options_dict: dict):
    """
    Converts a dictionary of string values into a structured dict with
    parsed and validated types.
    """
    normalized = {}
    options_copy = options_dict.copy()

    # Special handling for "format" and "start" which appear in variable params,
    # but not in the main rule options. We pass them through if present.
    if "format" in options_copy:
        normalized["format"] = options_copy.pop("format")
    if "start" in options_copy:
        try:
            normalized["start"] = int(options_copy.pop("start"))
        except ValueError as exc:
            raise ValueError("Variable parameter 'start' must be an integer") from exc

    # Standard rule options
    _normalize_positioning(options_copy, normalized)
    _normalize_layout(options_copy, normalized)
    _normalize_formatting(options_copy, normalized)

    # If anything remains, it's either an error or a custom param we don't know yet.
    # For strictness, we raise error.
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
        return size_str  # Already parsed

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
    """
    Parses a space-separated color string into a list of floats.
    """
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


##################################################
# TEXT VARIABLE PARSING
##################################################


def _parse_var_expression(expr: str):
    """
    Parses the inner content of a {variable} block into a token tuple.
    """
    # 1. Complex variables
    if COMPLEX_VAR_REGEX.fullmatch(expr):
        return ("total-page", None, {})

    # 2. Metadata variables
    if match := META_VAR_REGEX.fullmatch(expr):
        return (f"meta:{match.group(1).split(':', 1)[1]}", None, {})

    # 3. MASTER REGEX: Handles Simple, Arithmetic, and Formatting
    #    e.g. "page", "page+1", "page:06d", "page+5000:06d"
    if match := MASTER_VAR_REGEX.fullmatch(expr):
        groups = match.groupdict()
        var = groups["var"].lower()
        if var not in KNOWN_VARS:
            raise ValueError(f"Unknown variable: {{{var}}}")

        # Build the operation payload
        # payload = (arithmetic_value, format_string)
        op_val = int(groups["num"]) if groups["num"] else 0
        if groups["op"] == "-":
            op_val = -op_val

        # If arithmetic is requested (op_val != 0), ensure variable is numeric.
        if op_val != 0 and var not in NUMERIC_VARS:
            raise ValueError(f"Cannot apply arithmetic to non-numeric variable: {var}")

        fmt_spec = groups["fmt"]  # None if missing

        return (var, "master", (op_val, fmt_spec))

    raise ValueError(f"Unknown variable expression: {{{expr}}}")


def _evaluate_token(token: tuple, context: dict):
    """
    Evaluates a single parsed token against the runtime context.
    """
    var, op, param = token

    # --- Case 1: Special Logic Variables ---
    if var == "total-page":
        return context.get("total", 0) - context.get("page", 0)

    if var.startswith("meta:"):
        meta_key = var[5:]
        return context.get("metadata", {}).get(meta_key, "")

    # --- Case 2: Standard Variables ---
    base_value = context.get(var, "")

    # Handle "master" (Arithmetic + Formatting)
    if op == "master":
        offset, fmt_spec = param  # param is (int_offset, str_format)

        # Apply Arithmetic (only if base is numeric)
        final_val = base_value
        if offset != 0:
            if isinstance(base_value, (int, float)):
                final_val = base_value + offset
            else:
                raise ValueError(f"Cannot apply arithmetic to non-numeric variable: {var}")

        # Apply Formatting
        if fmt_spec:
            try:
                # Python string formatting: "{:06d}".format(val)
                return "{:{}}".format(final_val, fmt_spec)
            except (ValueError, TypeError) as e:
                # Fallback or strict error? Let's be strict for bates stamping.
                raise ValueError(f"Formatting error for {{{var}:{fmt_spec}}}: {e}")

        return final_val

    return base_value


def _tokenize_text_string(text_str: str) -> list:
    """
    Splits the text string into a list of literals and parsed tokens.
    """
    parts = []
    # Split the string by our token regex.
    split_parts = TOKEN_REGEX.split(text_str)

    for i, part in enumerate(split_parts):
        if not part:
            continue

        is_token = i % 2 == 1  # Literals are at even indices

        if not is_token:
            parts.append(part)
        elif part.startswith("{{"):
            parts.append(part[1:-1])  # Unescape {{...}}
        else:
            # Parse {expr}
            parts.append(_parse_var_expression(part[1:-1]))

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
            # It's a token tuple
            result.append(str(_evaluate_token(part, context)))
    return "".join(result)


def _compile_text_renderer(text_str: str):
    """
    Parses and "compiles" a text string into a render function.
    """
    parts = _tokenize_text_string(text_str)
    return lambda context: _default_renderer(parts, context)


def _find_unit(input_str: str):
    """Find a unit from UNITS in the string"""
    for unit_name in UNITS:
        if input_str.endswith(unit_name):
            return unit_name
    return None


def _group_specs_with_qualifiers(specs):
    """
    Pre-processes the specs list to pair qualifiers ('even', 'odd').
    """
    grouped_specs = []
    specs_iterator = iter(specs)
    for spec in specs_iterator:
        is_qualifier = spec.lower() in ("even", "odd")
        if is_qualifier:
            try:
                next_spec = next(specs_iterator)
                grouped_specs.append((next_spec, spec.lower()))
            except StopIteration as exc:
                raise ValueError(f"Missing spec after '{spec}' keyword.") from exc
        else:
            grouped_specs.append((spec, None))
    return grouped_specs
