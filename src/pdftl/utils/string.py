# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/string.py

"""String processing utilities, including before_space and
xml_{de,en}code_for_info"""

import itertools
import re

from pdftl.utils.whatisit import whatis_guess

IGNORED_NONPRINTING_CHAR_RE = re.compile(
    "["
    + "".join(
        map(
            chr,
            itertools.chain(
                range(0x00, 0x0A),
                range(0x0B, 0x0D),
                range(0x0E, 0x20),
                range(0x80, 0xA0),
            ),
        )
    )
    + "]"
)


def remove_ignored_nonprinting_chars(s):
    """Remove ignored non-printing characters from a string"""
    return IGNORED_NONPRINTING_CHAR_RE.sub("", s)


XML_LOOKUPS = {
    "&": "&amp;",
    "'": "&apos;",
    '"': "&quot;",
    "<": "&lt;",
    ">": "&gt;",
}


def xml_encode_for_info(x):
    """
    Ad-hoc imitation of pdftk's xml escaping
    """

    # pdftk escapes ASCII DEL = 0x7f
    return remove_ignored_nonprinting_chars(
        "".join(
            [
                (
                    XML_LOOKUPS[c]
                    if c in XML_LOOKUPS
                    else c if c.isascii() and c != chr(0x7F) else f"&#{ord(c)};"
                )
                for c in x
            ]
        )
    )


def _xml_decode_lookup_step(x):
    """
    If x starts with an encoded lookup string, decode it.

    Returns:
    x', y
    where x' is x with any leading encoded lookup removed,
    and y is the decoded version of that encoded lookup, or None
    """
    for k, v in XML_LOOKUPS.items():
        if x.startswith(v):
            return x[len(v) :], k
    return x, None


def xml_decode_for_info(x):
    """
    Reverse the encoding of xml_encode_for_info()
    """
    out = ""
    while len(x) > 0:
        x, y = _xml_decode_lookup_step(x)
        if y is not None:
            out += y
            continue

        if x.startswith("&#") and ";" in x:
            code, x = x[2:].split(";", 1)
            out += chr(int(code))
            continue

        out += x[0]
        x = x[1:]

    return out


def before_space(x):
    """return everything before the first space in x, if there is one,
    or x if not"""
    return x.partition(" ")[0]


def pdf_num_to_string(x):
    """Formats a PDF number as an int if possible, otherwise a float."""
    return f"{int(x) if x == round(x) else float(x):,}"


def pdf_rect_to_string(arr):
    """Normalizes and formats a PDF rectangle array."""
    if len(arr) != 4:
        return ""
    x0, y0, x1, y1 = arr
    rect = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
    return " ".join(map(pdf_num_to_string, rect))


def pdf_obj_to_string(x):
    """Convert a pdf object to a string, for dump_data_annots style output"""
    from pikepdf import Array, Name

    guess = whatis_guess(x)
    if guess == Name:
        return str(x)[1:]
    if guess in (Array, list):
        return " ".join([pdf_obj_to_string(y) for y in x])
    if guess in (int, str):
        return str(x)
    raise NotImplementedError(guess, x, type(x))


def split_escaped(text: str, delimiter: str) -> list[str]:
    """
    Splits a string by a delimiter, allowing the delimiter to be
    escaped with a backslash.

    Other backslashes should be left alone!

    Args:
        text: The string to split.
        delimiter: A single character to split on.

    Returns:
        A list of unescaped strings.

    Example:
        >>> split_escaped("a.b\\.c.d", ".")
        ['a', 'b.c', 'd']
        >>> split_escaped("a\\\\.b.c", ".")
        ['a\\\.b', 'c']
    """  # noqa: W605  # pylint: disable=W1401
    if len(delimiter) != 1:
        raise ValueError("Delimiter must be a single character")

    parts = []
    current_part = []
    is_escaped = False

    for char in text:
        if is_escaped:
            # The previous char was a backslash.
            if char != delimiter:
                current_part.append("\\")
            current_part.append(char)
            is_escaped = False
        elif char == "\\":
            # Start an escape. The backslash itself is *not* added
            # to the output. It is consumed.
            is_escaped = True
        elif char == delimiter:
            # We've hit a non-escaped delimiter.
            # Add the completed part and start a new one.
            parts.append("".join(current_part))
            current_part = []
        else:
            # A normal character.
            current_part.append(char)

    # After the loop, add the final part.
    parts.append("".join(current_part))
    return parts


def compact_json_string(json_string):
    """Use regex heuristics to compact a json string, by eliminating some linebreaks"""

    def compact_simple_array(match):
        # Extract the content of the array
        array_content = match.group(2)
        # Remove newlines and reduce whitespace to single spaces
        compacted_content = " ".join(array_content.strip().split())
        return f"{match.group(1)}{compacted_content}{match.group(3)}"

    ret = json_string
    for regex_str in [r"(\[)\s*([^\[\]]*?)\s*(\])", r"(\{)\s*([^\{\}]*?)\s*(\})"]:
        ret = re.sub(regex_str, compact_simple_array, ret)
    return ret
