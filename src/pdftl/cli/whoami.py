# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/cli/whoami.py

"""Metadata for the project"""

import os
import sys

_exe = os.path.basename(sys.argv[0]) if sys.argv and sys.argv[0] != "-m" else None
WHOAMI = "pdftl" if _exe is None else _exe
PACKAGE = "pdftl"
HOMEPAGE = "https://github.com/pdftl/pdftl"
ISSUES = HOMEPAGE + "/issues"
DOCSPAGE = "https://pdftl.readthedocs.io"
