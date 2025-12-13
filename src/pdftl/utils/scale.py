# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/scale.py

"""
Page scaling
"""

import logging

logger = logging.getLogger(__name__)
from pdftl.core.constants import PAGE_BOXES


def _scale_rect(rect, scale):
    return [float(c) * scale for c in rect]


def _scale_standard_page_boxes(page, scale):
    for box_name in PAGE_BOXES:
        if box_name in page:
            page[box_name] = _scale_rect(page[box_name], scale)


def _scale_all_annots_in_page(page, scale):
    for annot in getattr(page, "Annots", {}):
        if hasattr(annot, "Rect"):
            logger.debug("scaling %s", list(annot.Rect))
            annot.Rect = _scale_rect(annot.Rect, scale)


def apply_scaling(page, scale):
    """
    Applies scaling to a page's boxes, content stream, and annotations.

    Args:
        page: The destination pikepdf.Page object to modify.
        scale: The scaling factor (float). 1.0 means no change.
    """
    if scale == 1.0:
        return

    _scale_standard_page_boxes(page, scale)

    # Prepend a scaling matrix to the page's content stream.
    page.contents_add(bytes(f"{scale} 0 0 {scale} 0 0 cm", "utf-8"), prepend=True)

    _scale_all_annots_in_page(page, scale)
