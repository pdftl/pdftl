# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/io_helpers.py

"""Helper functions for robust file I/O operations."""

import sys
from contextlib import nullcontext
from pathlib import Path


def smart_open_output(filename: str, mode="w", encoding="utf-8"):
    """
    Context manager that opens a filename for writing,
    or yields sys.stdout (or sys.stdout.buffer) if filename is None.
    """
    if filename is None:
        # If binary mode is requested, use the buffer underlying stdout
        if "b" in mode:
            return nullcontext(sys.stdout.buffer)
        return nullcontext(sys.stdout)

    if "b" in mode:
        # Binary mode doesn't take an encoding argument
        return open(filename, mode)  # pylint: disable=unspecified-encoding

    return open(filename, mode, encoding=encoding)


def can_read_file(filename: str) -> bool:
    """Test if we can read a file by attempting to open it."""
    try:
        p = Path(filename)

        if not p.is_file():
            return False

        with p.open("rb") as _:
            pass
        return True

    except (OSError, FileNotFoundError):
        return False
