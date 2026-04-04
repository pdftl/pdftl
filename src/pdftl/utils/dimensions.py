# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/dimensions.py

"""Utilities related to dimensions, e.g., conversion"""

from typing import TYPE_CHECKING

from pdftl.core.constants import UNITS
from pdftl.exceptions import InvalidArgumentError

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
        if total_dimension is None:
            raise ValueError(f"Percentage value '{val_str}' requires a total dimension.")
        try:
            return (float(numeric_part) / 100.0) * total_dimension
        except ValueError as e:
            raise InvalidArgumentError(
                f"Could not parse percentage dimension: '{numeric_part}'"
            ) from e

    for unit, multiplier in UNITS.items():
        if val_str.endswith(unit):
            numeric_part = val_str[: -len(unit)]
            try:
                return float(numeric_part) * multiplier
            except ValueError as e:
                raise InvalidArgumentError(
                    f"Could not parse numeric dimension with unit: '{numeric_part}'"
                ) from e

    # Default to points, stripping an optional 'pt' suffix.
    numeric_part = re.sub(r"pts?$", "", val_str)
    try:
        return float(numeric_part)
    except ValueError as e:
        raise InvalidArgumentError(f"Could not parse numeric dimension: '{numeric_part}'") from e


def get_visible_page_dimensions(page: "pikepdf.Page", box="cropbox"):
    """Safely retrieves the page's visible dimensions using
    /TrimBox (if box is "trimbox" and /TrimBox is present)
    or /CropBox if present, or /MediaBox otherwise.

    Returns:
        origin_x, origin_y, signed_width, signed_height, or None on error.

    """
    try:
        if box == "trimbox":
            rect = page.trimbox
        else:
            rect = page.cropbox
        x0, y0, x1, y1 = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
        return x0, y0, x1 - x0, y1 - y0
    except (TypeError, IndexError, ValueError, AttributeError):
        return None
