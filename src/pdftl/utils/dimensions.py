# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/dimensions.py

"""Utilities related to dimensions, e.g., conversion"""

from typing import TYPE_CHECKING

from pdftl.core.constants import UNITS

if TYPE_CHECKING:
    import pikepdf


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


def get_visible_page_dimensions(page: "pikepdf.Page"):
    """Safely retrieves the page's visible dimensions using
    /CropBox if present, or /MediaBox otherwise.

    Returns:
        origin_x, origin_y, signed_width, signed_height, or None on error.

    """
    try:
        x0, y0, x1, y1 = (float(p) for p in page.cropbox)
        return x0, y0, x1 - x0, y1 - y0
    except (TypeError, IndexError, ValueError, AttributeError):
        return None
