# src/pdftl/commands/parsers/move_parser.py

from typing import NamedTuple

from pdftl.exceptions import UserCommandLineError


class MoveSpec(NamedTuple):
    source_spec: str
    mode: str  # 'before' or 'after'
    target_spec: str


def parse_move_args(args: list[str]) -> MoveSpec:
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
    mode = None

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
