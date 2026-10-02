# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/gui/page_geometry.py

"""Qt-free page geometry: displayed-space coordinate transforms, dimension
string formatting, and grouping pages that render pixel-for-pixel alike.

"Displayed" space is what pdfium actually renders: /Rotate applied, origin
top-left, y increasing downward, units of points (before any pixel scale).
This matches thumbs.render_page's output orientation directly.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pikepdf

from pdftl.core.constants import UNITS
from pdftl.utils.geometry import get_visual_mapping_matrices

_GROUP_ROUND_PT = 0.01
_DECIMALS = {"pt": 2, "mm": 2, "cm": 3, "in": 3, "%": 2}


@dataclass(frozen=True)
class PageGeometry:
    """One page's visible box and rotation, in absolute PDF user space.

    `origin_x`/`origin_y`/`width`/`height` describe the CropBox (falling
    back to MediaBox) before rotation; `rotation` is /Rotate normalized to
    one of 0, 90, 180, 270.
    """

    origin_x: float
    origin_y: float
    width: float
    height: float
    rotation: int

    @property
    def displayed_width(self) -> float:
        return self.height if self.rotation in (90, 270) else self.width

    @property
    def displayed_height(self) -> float:
        return self.width if self.rotation in (90, 270) else self.height

    def user_to_displayed(self, x: float, y: float) -> tuple[float, float]:
        """Absolute PDF user-space point -> displayed space (points)."""
        m_u_to_v, _ = get_visual_mapping_matrices(
            self.origin_x, self.origin_y, self.width, self.height, self.rotation
        )
        vx, vy = m_u_to_v.transform((x, y))
        dx = vx - self.origin_x
        dy = (self.origin_y + self.displayed_height) - vy
        return dx, dy

    def displayed_to_user(self, dx: float, dy: float) -> tuple[float, float]:
        """Displayed space (points) -> absolute PDF user-space point."""
        _, m_v_to_u = get_visual_mapping_matrices(
            self.origin_x, self.origin_y, self.width, self.height, self.rotation
        )
        vx = dx + self.origin_x
        vy = (self.origin_y + self.displayed_height) - dy
        return m_v_to_u.transform((vx, vy))

    def displayed_to_pixels(self, dx: float, dy: float, scale: float) -> tuple[float, float]:
        return dx * scale, dy * scale

    def pixels_to_displayed(self, px: float, py: float, scale: float) -> tuple[float, float]:
        return px / scale, py / scale

    def group_key(
        self, round_to: float = _GROUP_ROUND_PT
    ) -> tuple[float, float, int, float, float]:
        """Key such that pages sharing it render at identical pixel size and
        origin for any given scale, so their renders can be composited."""

        def r(value: float) -> float:
            return round(value / round_to) * round_to

        return (
            r(self.displayed_width),
            r(self.displayed_height),
            self.rotation,
            r(self.origin_x),
            r(self.origin_y),
        )


def _normalize_rotation(raw: float) -> int:
    return int(round(raw / 90.0)) * 90 % 360


def page_geometry_from_page(page: pikepdf.Page) -> PageGeometry:
    """Builds a `PageGeometry` from an already-open pikepdf page."""
    box = page.cropbox  # falls back to /MediaBox when /CropBox is absent
    x0, y0, x1, y1 = (float(v) for v in box)
    rotation = _normalize_rotation(float(page.rotation))
    return PageGeometry(origin_x=x0, origin_y=y0, width=x1 - x0, height=y1 - y0, rotation=rotation)


def read_page_geometries(
    pdf_path: str | Path, pages: Sequence[int], password: str | None = None
) -> dict[int, PageGeometry]:
    """Reads `PageGeometry` for each 1-based page number in `pages`.

    Preserves `pages`' order, so the result can be grouped directly.
    Raises `pikepdf.PasswordError` if `password` doesn't open the file.
    """
    with pikepdf.open(pdf_path, password=password or "") as pdf:
        return {page: page_geometry_from_page(pdf.pages[page - 1]) for page in pages}


def group_by_geometry(geometries: dict[int, PageGeometry]) -> list[list[int]]:
    """Groups page numbers whose geometry key matches, in first-appearance order."""
    groups: dict[tuple, list[int]] = {}
    order: list[tuple] = []
    for page, geometry in geometries.items():
        key = geometry.group_key()
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(page)
    return [groups[key] for key in order]


def _trim(value: float, decimals: int) -> str:
    text = f"{value:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def pts_to_dim_str(value_pts: float, unit: str, total_dimension: float | None = None) -> str:
    """Formats `value_pts` in `unit` ('pt', 'mm', 'cm', 'in', or '%'), the
    inverse of `dim_str_to_pts`. '%' is of `total_dimension` (also in points).
    """
    if unit == "%":
        if not total_dimension:
            raise ValueError("'%' unit requires a nonzero total_dimension")
        magnitude = value_pts / total_dimension * 100.0
    elif unit in UNITS:
        magnitude = value_pts / UNITS[unit]
    else:
        raise ValueError(f"Unknown unit: {unit!r}")
    return f"{_trim(magnitude, _DECIMALS.get(unit, 2))}{unit}"
