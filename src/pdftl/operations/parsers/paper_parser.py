# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/parsers/paper_parser.py

"""Parser routine for paper sizes"""

import re

from pdftl.core.constants import PAPER_SIZES

def parse_paper_spec(spec_str):
    """
    Parses a spec string to determine if it's a paper size (e.g., 'a4', 'a4_l', '4x6').
    Returns a (width, height) tuple in points, or None if not a paper spec.
    """
    spec_lower = spec_str.lower()
    landscape = False
    if spec_lower.endswith("_l"):
        landscape = True
        spec_lower = spec_lower[:-2]

    paper_size = PAPER_SIZES.get(spec_lower)
    if not paper_size:
        # Try parsing custom inch dimensions like "4x6"
        match = re.match(r"^(\d*\.?\d+)x(\d*\.?\d+)$", spec_lower)
        if match:
            width_in, height_in = float(match.group(1)), float(match.group(2))
            paper_size = (width_in * 72, height_in * 72)

    if paper_size and landscape:
        return paper_size[1], paper_size[0]  # Swap width and height

    return paper_size

