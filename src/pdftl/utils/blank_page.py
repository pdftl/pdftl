# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/blank_page.py

"""Utilities for creating blank PDF pages."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pikepdf


def make_blank_page(
    pdf: pikepdf.Pdf,
    media_box: tuple[float, float, float, float] | pikepdf.Array,
    crop_box: tuple[float, float, float, float] | pikepdf.Array | None = None,
    trim_box: tuple[float, float, float, float] | pikepdf.Array | None = None,
) -> pikepdf.Page:
    """
    Create a blank page of the given size and add it to pdf.

    Args:
        pdf:       The Pdf object to add the page to.
        media_box: MediaBox as (x0, y0, x1, y1).
        crop_box:  Optional explicit CropBox as (x0, y0, x1, y1).
        trim_box:  Optional explicit TrimBox as (x0, y0, x1, y1).

    Returns:
        The newly created pikepdf.Page.
    """
    page = pdf.add_blank_page()
    page.MediaBox = list(media_box)
    if crop_box:
        page.CropBox = list(crop_box)
    if trim_box:
        page.TrimBox = list(trim_box)
    return page
