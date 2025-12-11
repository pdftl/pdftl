# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/dump.py

"""Dump data to stdout or a file"""

import sys


def dump(data, dest=None):
    """Dump data to stdout or a file"""
    if dest is None:
        print(data, file=sys.stdout)
    else:
        with open(dest, "w", encoding="utf-8") as file:
            print(data, file=file)
