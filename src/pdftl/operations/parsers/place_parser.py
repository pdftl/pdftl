# src/pdftl/operations/parsers/place_parser.py

import re
from dataclasses import dataclass
from typing import Any

from pdftl.exceptions import UserCommandLineError


@dataclass
class PlacementOp:
    name: str
    params: dict[str, Any]


@dataclass
class PlaceCommand:
    page_spec: str
    operations: list[PlacementOp]


CMD_PATTERN = re.compile(r"^(.*?)\((.*)\)$")


def parse_place_args(args: list[str]) -> list[PlaceCommand]:
    commands = []
    for arg in args:

        arg = arg.strip()
        if arg.startswith("("):
            arg = "1-end" + arg

        match = CMD_PATTERN.match(arg.strip())
        if not match:
            raise UserCommandLineError(
                f"Invalid syntax for place command: '{arg}'. Expected format: 'pages(op=val;...)'"
            )

        page_spec = match.group(1).strip()
        ops_str = match.group(2).strip()

        operations = _parse_operations(ops_str)
        commands.append(PlaceCommand(page_spec, operations))

    return commands


def _parse_operations(ops_str: str) -> list[PlacementOp]:
    ops = []
    # Split operations by semicolon
    tokens = [t.strip() for t in ops_str.split(";") if t.strip()]

    for token in tokens:
        if "=" not in token:
            raise UserCommandLineError(f"Invalid operation format near '{token}'")

        key, val = token.split("=", 1)
        key = key.strip()
        val = val.strip()

        if key == "shift":
            # syntax: shift=dx,dy
            if "," not in val:
                raise UserCommandLineError(
                    f"Shift requires x,y coordinates (e.g. shift=10,20), got '{val}'"
                )

            dx_str, dy_str = val.split(",", 1)

            ops.append(
                PlacementOp("shift", {"dx": _split_math(dx_str), "dy": _split_math(dy_str)})
            )

        elif key in ("scale", "spin"):
            # syntax: scale=0.5  OR  scale=0.5:anchor
            value_part = val
            anchor_part = None

            if ":" in val:
                value_part, anchor_part = val.split(":", 1)

            params: dict[str, Any] = {"value": value_part.strip()}

            if anchor_part:
                anchor_part = anchor_part.strip()
                if "," in anchor_part:
                    ax, ay = anchor_part.split(",", 1)
                    params["anchor_type"] = "coord"
                    params["anchor_x"] = _split_math(ax)
                    params["anchor_y"] = _split_math(ay)
                else:
                    params["anchor_type"] = "named"
                    params["anchor_name"] = anchor_part
            else:
                params["anchor_type"] = "named"
                params["anchor_name"] = "center"

            ops.append(PlacementOp(key, params))

        else:
            raise UserCommandLineError(f"Unknown operation: '{key}'")

    return ops


def _split_math(s: str) -> list[str]:
    """
    Splits string on '+' or '-' keeping the operator attached to the term.
    Input: "50%+1in" -> ["50%", "+1in"]
    """
    s = s.replace(" ", "")
    # Split while keeping delimiters
    terms = re.split(r"([+-])", s)
    result = []

    current_sign = ""
    for t in terms:
        if t in ("+", "-"):
            current_sign = t
        elif t:
            result.append(current_sign + t)
            current_sign = ""

    return result
