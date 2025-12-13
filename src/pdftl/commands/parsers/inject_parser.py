# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/parsers/inject_parser.py

"""Parser for inject arguments"""

import logging

logger = logging.getLogger(__name__)


def parse_inject_args(inject_args: list):
    """
    Parses the command-line style arguments for injection commands.

    This function implements the state machine to group page specifications
    with their corresponding 'head'/'tail' code blocks.

    Returns a tuple of (heads, tails, remaining_specs).
    """

    state = "neutral"
    specs = []
    heads = []
    tails = []
    for arg in inject_args:
        if state in ("just saw head", "just saw tail"):
            # This argument is the code following a 'head' or 'tail' command.
            current_specs = specs if specs else ["1-end"]
            data = {"specs": current_specs, "code": arg}

            if "head" in state:
                heads.append(data)
            else:
                tails.append(data)

            state = "neutral"
            specs = []
        elif arg == "head":
            state = "just saw head"
        elif arg == "tail":
            state = "just saw tail"
        else:
            # This is a page specification.
            specs.append(arg)

    logger.debug("heads=%s", heads)
    logger.debug("tails=%s", tails)
    logger.debug("specs=%s", specs)

    return heads, tails, specs
