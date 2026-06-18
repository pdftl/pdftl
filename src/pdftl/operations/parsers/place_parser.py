# src/pdftl/operations/parsers/place_parser.py

import re

from dataclasses import dataclass
from typing import Any

from pdftl.exceptions import UserCommandLineError
from pdftl.operations.parsers.command_list_parser import split_spec_and_ops


@dataclass
class PlacementOp:
    name: str
    params: dict[str, Any]


@dataclass
class PlaceCommand:
    page_spec: str
    operations: list[PlacementOp]


def parse_place_args(args: list[str]) -> list[PlaceCommand]:
    commands = []
    for arg in args:
        if not arg.strip():
            continue
        page_spec, ops_str = split_spec_and_ops(arg)
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
            ops.append(_parse_shift(val))
        elif key in ("scale", "spin"):
            ops.append(_parse_scale_or_spin(val, key))
        else:
            raise UserCommandLineError(f"Unknown operation: '{key}'")

    return ops


def _parse_scale_or_spin(val, key):
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

    return PlacementOp(key, params)


def _parse_shift(val):
    # syntax: shift=dx,dy
    if "," not in val:
        raise UserCommandLineError(
            f"Shift requires x,y coordinates (e.g. shift=10,20), got '{val}'"
        )

    dx_str, dy_str = val.split(",", 1)

    return PlacementOp("shift", {"dx": _split_math(dx_str), "dy": _split_math(dy_str)})


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
