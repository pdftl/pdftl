# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/parsers/move_parser.py

"""Parser for move command arguments"""

from pdftl.commands.types.move_types import MoveSpec
from pdftl.exceptions import UserCommandLineError


def parse_move_args(args: list[str], _data=None) -> MoveSpec:
    """
    Parses arguments for the move command.
    Syntax: <source-spec> {before|after} <target-spec>
    """
    if not args:
        raise UserCommandLineError(
            "Move command requires arguments: <source> {before|after} <target>"
        )

    # Find the pivot keyword (before/after)
    pivot_idx = -1
    mode = "after"

    for i, arg in enumerate(args):
        if arg.lower() in ("before", "after"):
            pivot_idx = i
            mode = arg.lower()
            break

    if pivot_idx == -1:
        raise UserCommandLineError("Move command must include 'before' or 'after' keyword.")

    if pivot_idx == 0:
        raise UserCommandLineError("Move command missing source specification.")

    if pivot_idx == len(args) - 1:
        raise UserCommandLineError(f"Move command missing target specification after '{mode}'.")

    # Join parts to form specs (handling spaces within ranges like "1 - 5")
    source_spec = " ".join(args[:pivot_idx])
    target_spec = " ".join(args[pivot_idx + 1 :])

    return MoveSpec(source_spec, mode, target_spec)
