# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/info/parse_dump.py

"""Parse metadata from dump_data text-based output.

Public: parse_dump_data

FIXME: currently have *undefined behaviour* if a string in the dump
contains newlines.

"""

import logging

logger = logging.getLogger(__name__)


def parse_dump_data(lines, string_decoder):
    """
    Parses the text output of the 'pdftl dump_data' command into a
    structured Python dictionary.

    Args:
        lines: an iterable of strings, the dump_data output

    Returns:
        dict: A dictionary containing the parsed data, organized into
              'Info', 'Bookmarks', 'PageMedia', and other top-level keys.
    """
    pdf_data = {
        "Info": {},
        "BookmarkList": [],
        "PageMediaList": [],
        "PageLabelList": [],
    }

    state = _reset_state()

    for line in lines:
        _handle_line(line, pdf_data, state, string_decoder)

    return pdf_data


def _reset_state(state=None, state_type=None):
    """Reset state"""
    if state is None:
        state = {}
    state["current_type"] = state_type
    state["current_value"] = None
    state["last_info_key"] = None
    return state


def _handle_line(line, pdf_data, state, string_decoder):
    """Handle a line during parsing"""
    # skip empty lines
    if not line.strip():
        return

    # the following string-or-bytes handling is needed when
    # we take data on stdin... fixme: why?

    def decode(x):
        if isinstance(x, bytes):
            return x.decode()
        return x

    split_at = bytes(":", "utf-8") if isinstance(line, bytes) else ":"

    parts = line.split(split_at, 1)
    key = decode(parts[0]).strip()

    if len(parts) == 2:
        value = decode(parts[1]).strip()
        _handle_key_value(key, value, pdf_data, state, string_decoder)
        return

    if len(parts) == 1 and key.endswith("Begin"):
        _handle_begin_tag(key[:-5], pdf_data, state, string_decoder)
        return

    logger.warning("Parsing error for 'update_data': line '%s' does not end in 'Begin'", key)


def _handle_begin_tag(key, pdf_data, state, _string_decoder):
    """Resets the parser state when a '...Begin' tag is found."""
    _reset_state(state, state_type=key)
    if key == "Info":
        pass
    elif (list_name := key + "List") in pdf_data.keys():
        new_record = {}
        pdf_data[list_name].append(new_record)
        state["current_value"] = new_record
    else:
        logger.warning("Unknown Begin tag '%s' in metadata. Ignoring.", key)
        _reset_state(state, None)


def _handle_key_value(key, value, pdf_data, state, string_decoder):
    """
    Dispatches a key-value pair to the correct parser
    based on the current parser state.
    """
    lookups = _parse_field_decode_lookups(string_decoder)
    if (current_type := state["current_type"]) in lookups:
        if not key.startswith(current_type):
            logger.warning(
                "While parsing metadata: key '%s' in %sBegin block"
                " should start with '%s'. Ignoring this line.",
                key,
                current_type,
                current_type,
            )
            return
        _parse_field(key, value, state["current_value"], current_type, lookups[current_type])
    elif key in ("InfoKey", "InfoValue"):
        _parse_info_field(key, value, pdf_data["Info"], state, string_decoder)
    else:
        _parse_top_level_field(key, value, pdf_data, string_decoder)


def _parse_field_decode_lookups(string_decoder):
    ret = {}
    ret["Bookmark"] = {
        "Title": string_decoder,
        "Level": _safe_int,
        "PageNumber": _safe_int,
    }
    ret["PageMedia"] = {
        "Number": _safe_int,
        "Rotation": _safe_int,
        "CropRect": _safe_float_list,
        "Rect": _safe_float_list,
        "Dimensions": _safe_float_list,
    }
    ret["PageLabel"] = {
        "NewIndex": _safe_int,
        "Start": _safe_int,
        "NumStyle": lambda x: x,
        "Prefix": string_decoder,
    }
    return ret


def _safe_int(value):
    """Safely convert a string to an int, or return the original string."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return value


def _safe_float_list(value):
    """Safely convert a space-separated string to a list of floats."""
    try:
        return [float(n) for n in value.split()]
    except (ValueError, TypeError, AttributeError):
        return value


def _parse_field(key, value, current_data, prefix, decode_lookup):
    """Parses a field"""
    assert key.startswith(prefix)
    if (short_key := key[len(prefix) :]) in decode_lookup:
        current_data[short_key] = decode_lookup[short_key](value)
    else:
        raise ValueError(f"Unknown key {key} in metadata")


def _parse_info_field(key, value, info_dict, state, string_decoder):
    """Parses InfoKey/InfoValue pairs, which are state-dependent. The
    key must be either InfoKey or InfoValue."""
    if key == "InfoKey":
        state["last_info_key"] = value
    elif key == "InfoValue":
        if state["last_info_key"] is not None:
            info_dict[state["last_info_key"]] = string_decoder(value)
            state["last_info_key"] = None  # Consume the key
        else:
            logger.warning("Got InfoValue without a preceding InfoKey. Ignoring")
    else:
        raise ValueError(f"Unknown Info field key '{key}' in metadata. This is a bug.")


def _parse_top_level_field(key, value, pdf_data, _string_decoder):
    """Parses simple key-value pairs at the root of the data."""
    if key in ("PdfID0", "PdfID1"):
        pdf_data[key] = value
    elif key == "NumberOfPages":
        pdf_data[key] = _safe_int(value)
    else:
        raise ValueError(f"Unknown key {key} in metadata")
