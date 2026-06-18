# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/parsers/modify_images_parser.py

import re
from dataclasses import dataclass
from typing import Any

from pdftl.exceptions import UserCommandLineError
from pdftl.operations.parsers.command_list_parser import split_spec_and_ops, split_semicolon_tokens


@dataclass
class ImageModifierCall:
    name: str
    params: dict[str, Any]


@dataclass
class ImageModifierCommand:
    page_spec: str
    operations: list[ImageModifierCall]


CMD_PATTERN = re.compile(r"^(.*?)\((.*)\)$")


def parse_modify_images_args(args: list[str]) -> list[ImageModifierCommand]:
    """
    Parses CLI tokens for modify_images into structured execution rules.
    Example: ['1(contrast=1.3; sharpen=true)', '(autocontrast)']
    """
    commands = []
    for arg in args:
        if not arg.strip():
            continue
        page_spec, ops_str = split_spec_and_ops(arg)  # raises on bad input
        operations = _parse_operations(ops_str)
        commands.append(ImageModifierCommand(page_spec, operations))
    return commands


def _parse_operations(ops_str: str) -> list[ImageModifierCall]:
    ops = []
    # Split distinct pipeline filters by semicolon
    tokens = split_semicolon_tokens(ops_str)

    for token in tokens:
        # Support standalone flags (e.g. "autocontrast" maps implicitly to "autocontrast=true")
        if "=" in token:
            key, val = token.split("=", 1)
            key = key.strip().lower()
            val = val.strip()
        else:
            key = token.strip().lower()
            val = "true"

        # Guard against malformed empty entries (e.g., intermediate typo semicolons ";;")
        if not key:
            raise UserCommandLineError("Invalid empty filter element in pipeline configuration.")

        # Build a parameter dictionary for the individual filter type
        params = _parse_modify_params(key, val)
        ops.append(ImageModifierCall(key, params))

    return ops


def _parse_modify_params(filter_name: str, val_str: str) -> dict[str, Any]:
    """
    Parses the value payload for a filter. If the filter supports comma-separated
    sub-arguments (like custom levels bounds), we parse them into a structured dict.
    Otherwise, we store the canonical value string under 'value'.
    """
    params: dict[str, Any] = {}

    if "," in val_str:
        # Split key sub-components if an advanced filter configuration needs it
        sub_tokens = [s.strip() for s in val_str.split(",")]
        params["values"] = sub_tokens
        # Fallback primary string representation
        params["value"] = val_str
    else:
        params["value"] = val_str

    return params
