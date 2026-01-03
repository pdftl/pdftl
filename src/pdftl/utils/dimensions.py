# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/dimensions.py

"""Utilities related to dimensions, e.g., conversion"""

from pdftl.core.constants import UNITS


def dim_str_to_pts(val_str, total_dimension=None):
    """
    Parses a single crop dimension string (e.g., '10%', '2in', '50pt')
    and converts it into points.
    """
    import re

    val_str = val_str.lower().strip()
    if not val_str:
        return 0.0

    if val_str.endswith("%"):
        # Percentage is a special case that depends on the total dimension.
        numeric_part = val_str[:-1]
        try:
            return (float(numeric_part) / 100.0) * total_dimension
        except ValueError:
            # Let it fall through to the float conversion below which will raise error
            pass

    for unit, multiplier in UNITS.items():
        if val_str.endswith(unit):
            numeric_part = val_str[: -len(unit)]
            return float(numeric_part) * multiplier

    # Default to points, stripping an optional 'pt' suffix.
    numeric_part = re.sub(r"pts?$", "", val_str)
    return float(numeric_part)
