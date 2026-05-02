# src/pdftl/utils/dimensions.py

"""Utilities related to dimensions, e.g., conversion"""

import re
from typing import TYPE_CHECKING

from pdftl.core.constants import UNITS
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.parsers.paper_parser import parse_paper_spec

if TYPE_CHECKING:
    import pikepdf


def _parse_paper_size(val_str: str, axis: str | None) -> float | None:
    if axis in ("width", "height"):
        paper_dims = parse_paper_spec(val_str)
        if paper_dims:
            return paper_dims[0] if axis == "width" else paper_dims[1]
    return None


def _parse_percentage(val_str: str, total_dimension: float | None) -> float | None:
    if not val_str.endswith("%"):
        return None

    numeric_part = val_str[:-1]
    if total_dimension is None:
        raise ValueError(f"Percentage value '{val_str}' requires a total dimension.")

    try:
        return (float(numeric_part) / 100.0) * total_dimension
    except ValueError as e:
        raise InvalidArgumentError(
            f"Could not parse percentage dimension: '{numeric_part}'"
        ) from e


def _parse_unit(val_str: str) -> float | None:
    for unit, multiplier in UNITS.items():
        if val_str.endswith(unit):
            numeric_part = val_str[: -len(unit)]
            try:
                return float(numeric_part) * multiplier
            except ValueError as e:
                raise InvalidArgumentError(
                    f"Could not parse numeric dimension with unit: '{numeric_part}'"
                ) from e
    return None


def _parse_default_pts(val_str: str) -> float:
    numeric_part = re.sub(r"pts?$", "", val_str)
    try:
        return float(numeric_part)
    except ValueError as e:
        raise InvalidArgumentError(f"Could not parse numeric dimension: '{numeric_part}'") from e


def dim_str_to_pts(
    val_str: str, total_dimension: float | None = None, axis: str | None = None
) -> float:
    """
    Parses a single crop dimension string (e.g., '10%', '2in', '50pt', 'a4')
    and converts it into points.

    If axis is provided ("width" or "height"), it can resolve standard paper sizes.
    """
    val_str = val_str.lower().strip()
    if not val_str:
        return 0.0

    # Delegate to specialized parsers, returning the first successful match
    paper_val = _parse_paper_size(val_str, axis)
    if paper_val is not None:
        return paper_val

    perc_val = _parse_percentage(val_str, total_dimension)
    if perc_val is not None:
        return perc_val

    unit_val = _parse_unit(val_str)
    if unit_val is not None:
        return unit_val

    # Fallback
    return _parse_default_pts(val_str)


def get_visible_page_dimensions(page: "pikepdf.Page", box="cropbox", apply_rotate=True):
    """Safely retrieves the page's visible dimensions using
    /TrimBox (if box is "trimbox" and /TrimBox is present)
    or /CropBox if present, or /MediaBox otherwise.

    Returns:
        origin_x, origin_y, signed_width, signed_height, or None on error.

    """
    try:
        return get_visible_page_dimensions_or_raise(page, box, apply_rotate)
    except (TypeError, IndexError, ValueError, AttributeError):
        return None


def get_visible_page_dimensions_or_raise(page, box, apply_rotate):
    if box == "trimbox":
        rect = page.trimbox
    else:
        rect = page.cropbox
    x0, y0, x1, y1 = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
    w, h = x1 - x0, y1 - y0
    if apply_rotate and getattr(page, "Rotate", None) and (page.Rotate % 360) in (90, 270):
        return x0, y0, h, w
    return x0, y0, w, h
