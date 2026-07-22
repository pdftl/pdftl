# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/transform.py

"""Method(s) for geometric transformations of PDF pages"""

import logging
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from pikepdf import Array, Pdf

from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.page_specs import expand_specs_to_pages
from pdftl.utils.scale import apply_scaling

logger = logging.getLogger(__name__)


def transform_pdf(source_pdf: "Pdf", specs: list):
    """
    Applies rotations and/or scaling to specified pages of a PDF.
    IMPORTANT: This function opens the PDF and modifies it in-memory.

    Returns:
        A pikepdf.Pdf object with the transformations applied in memory.
        The caller is responsible for saving this object to a file.
    """
    total_pages = len(source_pdf.pages)
    expanded = expand_specs_to_pages(specs, opened_pdfs=[source_pdf]) or []

    for page_transform in expanded:
        (angle, relative), scale = page_transform.rotation, page_transform.scale
        i = page_transform.index

        # i is 0-based, like pikepdf
        try:
            page = source_pdf.pages[i]
        except IndexError as exc:
            raise InvalidArgumentError(
                f"Page {i + 1} does not exist in the PDF (total pages: {total_pages})."
            ) from exc

        if scale != 1.0:
            apply_scaling(page, scale)

        angle_int = int(angle)
        if angle != angle_int or angle_int % 90 != 0:
            raise InvalidArgumentError(
                f"Rotation angle must be a multiple of 90 degrees. Got: {angle}"
            )

        # Apply rotation if it is non-zero (or if it is a relative 0, though that's a no-op)
        # Optimization: 0-degree relative rotation does nothing, but we pass it anyway
        # to keep logic simple unless strict performance is needed.
        page.rotate(angle_int, relative=relative)

    return source_pdf


def _rotate_pair(angle, x_coord, y_coord, page_width, page_height):
    """Apply a rotation. If x_coord and/or y_coord is None,
    do something reasonable."""
    mod_angle = angle % 360
    if mod_angle == 0:
        return x_coord, y_coord

    if mod_angle == 90:
        # new_x = h - y, new_y = x
        new_x = _subtract_or_none(page_height, y_coord)
        new_y = x_coord
        return new_x, new_y

    if mod_angle == 180:
        # new_x = w - x, new_y = h - y
        new_x = _subtract_or_none(page_width, x_coord)
        new_y = _subtract_or_none(page_height, y_coord)
        return new_x, new_y

    if mod_angle == 270:
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


def _get_float_or_none(val):
    """Safely extract a float from a coordinate, converting /null to None."""
    if val is None:
        return None
    try:
        val_str = str(val)
        if val_str == "/null":
            return None
        return float(val)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Destination Coordinates Helpers (Low Cognitive Complexity)
# ---------------------------------------------------------------------------


def _apply_scale(val, scale: float):
    """Applies the scale multiplier if the value exists and scaling is active."""
    if val is None:
        return None
    return float(val) * scale if scale != 1.0 else float(val)


def _clean_coordinate_types(coords: list) -> list:
    """Ensures the final array consists of floats, Nones, or un-castable pikepdf objects."""
    out = []
    for c in coords:
        if c is None:
            out.append(None)
        elif isinstance(c, (int, float)):
            out.append(float(c))
        else:
            try:
                out.append(float(c))
            except (TypeError, ValueError):
                out.append(c)
    return out


def _extract_fit_type(coords: list) -> str:
    """Checks the second element of the array to determine explicit fit types."""
    if len(coords) >= 2:
        try:
            val_str = str(coords[1])
            if val_str.startswith("/") and val_str != "/null":
                return val_str
        except (ValueError, TypeError):
            pass  # destination array missing recognized fit-mode string
    return ""


def _transform_fitr_coords(
    coords: list, new_coords: list, angle: int, width: float, height: float, scale: float
):
    """Handles the complex 4-point bounding box transformation for /FitR."""
    left = _get_float_or_none(coords[2])
    bottom = _get_float_or_none(coords[3])
    right = _get_float_or_none(coords[4])
    top = _get_float_or_none(coords[5])

    x1, y1 = _rotate_pair(angle, left, bottom, width, height)
    x2, y2 = _rotate_pair(angle, right, top, width, height)

    valid_x = [v for v in (x1, x2) if v is not None]
    valid_y = [v for v in (y1, y2) if v is not None]

    n_left = min(valid_x) if valid_x else None
    n_right = max(valid_x) if valid_x else None
    n_bottom = min(valid_y) if valid_y else None
    n_top = max(valid_y) if valid_y else None

    new_coords[2] = _apply_scale(n_left, scale)
    new_coords[3] = _apply_scale(n_bottom, scale)
    new_coords[4] = _apply_scale(n_right, scale)
    new_coords[5] = _apply_scale(n_top, scale)


def _transform_xyz(
    coords: list, new_coords: list, angle: int, width: float, height: float, scale: float
):
    """Handles the transformation for /XYZ explicit destinations."""
    x = _get_float_or_none(coords[2]) if len(coords) > 2 else None
    y = _get_float_or_none(coords[3]) if len(coords) > 3 else None
    new_x, new_y = _rotate_pair(angle, x, y, width, height)

    if len(coords) > 2:
        new_coords[2] = _apply_scale(new_x, scale)
    if len(coords) > 3:
        new_coords[3] = _apply_scale(new_y, scale)


def _transform_fit_h(
    coords: list,
    new_coords: list,
    angle: int,
    width: float,
    height: float,
    scale: float,
    fit_type: str,
):
    """Handles the transformation for /FitH and /FitBH explicit destinations."""
    import pikepdf

    y = _get_float_or_none(coords[2]) if len(coords) > 2 else None
    new_x, new_y = _rotate_pair(angle, None, y, width, height)
    mod_angle = angle % 360

    if mod_angle in (90, 270):
        new_coords[1] = pikepdf.Name("/FitV") if fit_type == "/FitH" else pikepdf.Name("/FitBV")
        if len(coords) > 2:
            new_coords[2] = _apply_scale(new_x, scale)
    else:
        if len(coords) > 2:
            new_coords[2] = _apply_scale(new_y, scale)


def _transform_fit_v(
    coords: list,
    new_coords: list,
    angle: int,
    width: float,
    height: float,
    scale: float,
    fit_type: str,
):
    """Handles the transformation for /FitV and /FitBV explicit destinations."""
    import pikepdf

    x = _get_float_or_none(coords[2]) if len(coords) > 2 else None
    new_x, new_y = _rotate_pair(angle, x, None, width, height)
    mod_angle = angle % 360

    if mod_angle in (90, 270):
        new_coords[1] = pikepdf.Name("/FitH") if fit_type == "/FitV" else pikepdf.Name("/FitBH")
        if len(coords) > 2:
            new_coords[2] = _apply_scale(new_y, scale)
    else:
        if len(coords) > 2:
            new_coords[2] = _apply_scale(new_x, scale)


def _transform_explicit_dest(
    coords: list, fit_type: str, angle: int, width: float, height: float, scale: float
) -> list:
    """Routes and applies transformations for full ISO 32000 explicit destination arrays."""
    new_coords = list(coords)

    if fit_type in ("/Fit", "/FitB"):
        pass
    elif fit_type == "/XYZ":
        _transform_xyz(coords, new_coords, angle, width, height, scale)
    elif fit_type in ("/FitH", "/FitBH"):
        _transform_fit_h(coords, new_coords, angle, width, height, scale, fit_type)
    elif fit_type in ("/FitV", "/FitBV"):
        _transform_fit_v(coords, new_coords, angle, width, height, scale, fit_type)
    elif fit_type == "/FitR" and len(coords) >= 6:
        _transform_fitr_coords(coords, new_coords, angle, width, height, scale)
    else:
        logger.warning("Unrecognized or unhandled explicit destination fit type %s.", fit_type)

    return _clean_coordinate_types(new_coords)


def _transform_raw_xyz_coords(
    coords: list, angle: int, width: float, height: float, scale: float
) -> list:
    """Transforms a raw sequence of /XYZ parameters for backward compatibility."""
    x_coord = _get_float_or_none(coords[0]) if len(coords) > 0 else None
    y_coord = _get_float_or_none(coords[1]) if len(coords) > 1 else None

    x_coord, y_coord = _rotate_pair(angle, x_coord, y_coord, width, height)

    x_coord = _apply_scale(x_coord, scale)
    y_coord = _apply_scale(y_coord, scale)

    new_coords = [x_coord, y_coord] + coords[2:]
    return _clean_coordinate_types(new_coords)


# ---------------------------------------------------------------------------
# Main Routine
# ---------------------------------------------------------------------------


def transform_destination_coordinates(
    coords: list, page_box: Union["Array", list], angle: int, scale: float
) -> list:
    """
    Applies rotation and scaling to a set of PDF destination coordinates.

    This function handles both:
    1. A full explicit destination array (e.g., [page, /FitH, top]), supporting
       all ISO 32000-2 explicit destination types (/XYZ, /Fit, /FitH, /FitV, etc.).
    2. A raw list of /XYZ parameters (e.g., [left, top, zoom]) for backward compatibility.

    It logs a warning if it encounters a non-standard rotation angle.

    :param coords: A full destination array OR a list of coordinates [x, y, zoom].
    :param page_box: The MediaBox or CropBox of the target page.
    :param angle: The rotation angle (must be a multiple of 90).
    :param scale: The scaling factor applied to the page.
    :return: A new list of transformed coordinates.
    """
    if not coords:
        return coords

    width = float(page_box[2]) - float(page_box[0])
    height = float(page_box[3]) - float(page_box[1])

    fit_type = _extract_fit_type(coords)

    if fit_type:
        return _transform_explicit_dest(coords, fit_type, angle, width, height, scale)

    return _transform_raw_xyz_coords(coords, angle, width, height, scale)
