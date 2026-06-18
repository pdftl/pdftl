# src/pdftl/operations/parsers/command_list_parser.py
from __future__ import annotations
import re
from pdftl.exceptions import UserCommandLineError

_CMD_PATTERN = re.compile(r"^(.*?)\((.*)\)$")


def split_spec_and_ops(arg: str) -> tuple[str, str]:
    """Parses 'page_spec(ops)' into (page_spec, ops_str).

    A bare '(ops)' token is treated as '1-end(ops)'.
    Raises UserCommandLineError on malformed input.
    """
    arg = arg.strip()
    if arg.startswith("("):
        arg = "1-end" + arg

    match = _CMD_PATTERN.match(arg)
    if not match:
        raise UserCommandLineError(
            f"Invalid command syntax: '{arg}'. Expected format: 'pages(op=val;...)'"
        )
    return match.group(1).strip(), match.group(2).strip()


def split_semicolon_tokens(ops_str: str) -> list[str]:
    """Splits a semicolon-separated pipeline string into non-empty tokens."""
    return [t.strip() for t in ops_str.split(";") if t.strip()]


def split_key_value(token: str, context: str = "") -> tuple[str, str]:
    """Splits 'key=value' token, raising UserCommandLineError if '=' is absent."""
    if "=" not in token:
        raise UserCommandLineError(
            f"Invalid operation format near '{token}'" + (f" in '{context}'" if context else "")
        )
    key, val = token.split("=", 1)
    return key.strip(), val.strip()
