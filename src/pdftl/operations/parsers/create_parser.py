# src/pdftl/operations/parsers/create_parser.py

from pdftl.exceptions import UserCommandLineError
from pdftl.operations.parsers.new_page_parser import parse_new_page_args


def parse_create_args(args: list[str]) -> list[dict]:
    """
    Parses arguments for the create command into a list of geometry specification dictionaries.

    Syntax: [N][(spec)] [[N][(spec)] ...]
    """
    if not args:
        # Fallback default: 1 generic blank page
        return [{"count": 1, "geometry": None}]

    definitions = []

    for arg in args:
        # Pass the single argument as a list to match the utility's expected input type
        count, geometry_spec, remaining = parse_new_page_args([arg], accept_standalone_number=True)

        # If 'remaining' still contains the argument, the regex lookahead rejected it
        if remaining:
            raise UserCommandLineError(
                f"Unexpected argument for 'create': {arg}\n"
                "Correct syntax: pdftl create [count][(geometry)]"
            )

        definitions.append({"count": count, "geometry": geometry_spec})

    return definitions
