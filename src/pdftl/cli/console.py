# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/cli/console.py

"""A global rich console"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.console import Console

_CONSOLE = None


def get_console() -> "Console":
    global _CONSOLE
    if _CONSOLE is None:
        from rich.console import Console

        _CONSOLE = Console()
    return _CONSOLE
