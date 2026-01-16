# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/__main__.py

# pragma: exclude this file

"""
Main entrypoint for pdftl.
"""

import sys

from pdftl.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
