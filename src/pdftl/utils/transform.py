# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/transform.py

"""Method(s) for geometric transformations of PDF pages"""

import logging
from typing import TYPE_CHECKING, Union

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pikepdf import Array, Pdf

from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.page_specs import parse_specs
from pdftl.utils.scale import apply_scaling


def transform_pdf(source_pdf: "Pdf", specs: list):
    """
    Applies rotations and/or scaling to specified pages of a PDF.

    IMPORTANT: This function opens the PDF and modifies it in-memory.

    Returns:
        A pikepdf.Pdf object with the transformations applied in memory.
        The caller is responsible for saving this object to a file.
    """
    total_pages = len(source_pdf.pages)

    # The parse_specs generator handles commas and brackets, yielding
    # atomic PageSpec objects one by one.
    for page_spec in parse_specs(specs, total_pages):

        # 1. Generate the list of target pages from the PageSpec object
        step = 1 if page_spec.start <= page_spec.end else -1

        # Inclusive range (end + step)
        page_numbers = list(range(page_spec.start, page_spec.end + step, step))

        # 2. Filter: Qualifiers (Even/Odd)
        if "even" in page_spec.qualifiers:
            page_numbers = [p for p in page_numbers if p % 2 == 0]
        if "odd" in page_spec.qualifiers:
            page_numbers = [p for p in page_numbers if p % 2 != 0]

        # 3. Filter: Omissions
        # Omissions are stored as list of (start, end) tuples in the PageSpec
        for om_start, om_end in page_spec.omissions:
            page_numbers = [p for p in page_numbers if not om_start <= p <= om_end]

        # 4. Apply Transformations
        (angle, relative), scale = page_spec.rotate, page_spec.scale

        for i in page_numbers:
            # i is 1-based, pikepdf is 0-based
            try:
                page = source_pdf.pages[i - 1]
            except IndexError:
                raise InvalidArgumentError(
                    f"Page {i} does not exist in the PDF (total pages: {total_pages})."
                )

            if scale != 1.0:
                apply_scaling(page, scale)

            # Apply rotation if it is non-zero (or if it is a relative 0, though that's a no-op)
            # Optimization: 0-degree relative rotation does nothing, but we pass it anyway
            # to keep logic simple unless strict performance is needed.
            page.rotate(angle, relative=relative)

    return source_pdf


def _rotate_pair(angle, x_coord, y_coord, page_width, page_height):
    """Apply a rotation. If x_coord and/or y_coord is None,
    do something reasonable."""
    if angle == 0:
        return x_coord, y_coord
    if angle == 90:
        # new_x = h - y, new_y = x
        new_x = _subtract_or_none(page_height, y_coord)
        new_y = x_coord
        return new_x, new_y
    if angle == 180:
        # new_x = w - x, new_y = h - y
        new_x = _subtract_or_none(page_width, x_coord)
        new_y = _subtract_or_none(page_height, y_coord)
        return new_x, new_y
    if angle == 270:
        # new_x = y, new_y = w - x
        new_x = y_coord
        new_y = _subtract_or_none(page_width, x_coord)
        return new_x, new_y
    # Fallback to original coordinates
    logger.warning(
        "Unsupported rotation angle %s° encountered. Coordinate transformation may be incorrect.",
        angle,
    )
    return x_coord, y_coord


def _subtract_or_none(a, b):
    if a is None or b is None:
        return None
    return a - b


def transform_destination_coordinates(
    coords: list, page_box: Union["Array", list], angle: int, scale: float
) -> list:
    """
    Applies rotation and scaling to a set of PDF destination coordinates.

    This function specifically handles /XYZ coordinate arrays. It logs a
    warning if it encounters a non-standard rotation angle.

    :param coords: A list of coordinates, e.g., [x_coord, y_coord, zoom].
    :param page_box: The MediaBox or CropBox of the target page.
    :param angle: The rotation angle (must be a multiple of 90).
    :param scale: The scaling factor applied to the page.
    :return: A new list of transformed coordinates.
    """
    x_coord = float(coords[0]) if coords[0] is not None else None
    y_coord = float(coords[1]) if coords[1] is not None else None
    width = float(page_box[2] - page_box[0])
    height = float(page_box[3] - page_box[1])

    # Apply rotation first to get coordinates in the unscaled, rotated space
    x_coord, y_coord = _rotate_pair(angle, x_coord, y_coord, width, height)

    # Apply scaling
    if scale != 1.0:
        x_coord = x_coord * scale if x_coord is not None else None
        y_coord = y_coord * scale if y_coord is not None else None

    # Reconstruct the coordinate array, including any additional parameters (like zoom)
    new_coords = [x_coord, y_coord] + coords[2:]
    return [c if c is None else float(c) for c in new_coords]
