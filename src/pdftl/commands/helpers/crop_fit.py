# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/helpers/crop_fit.py

import io
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pikepdf
    import pypdfium2 as pdfium

from pdftl.utils.page_specs import page_numbers_matching_page_spec

logger = logging.getLogger(__name__)


class FitCropContext:
    """
    Manages the state and logic for 'fit' (visible content) cropping.
    Handles lazy initialization of the rendering engine and caching of group calculations.
    """

    def __init__(self, pdf: "pikepdf.Pdf"):
        self.pikepdf_doc = pdf
        self._pdfium_doc: pdfium.PdfDocument | None = None
        self._group_cache: dict[str, Any] = {}

    @property
    def doc(self) -> "pdfium.PdfDocument":
        """Lazy loader for the pdfium document."""
        if self._pdfium_doc is None:
            self._init_pdfium_doc()
        return self._pdfium_doc  # type: ignore

    def _init_pdfium_doc(self):
        """Initializes pypdfium2 from the current pikepdf state."""
        try:
            import PIL  # Verify dependency presence
            import pypdfium2 as pdfium
        except ImportError as e:
            logger.error(f"Failed to import geometry dependencies: {e}")
            raise ImportError(
                "The 'fit' crop feature requires 'pypdfium2' and 'pillow'. "
                "Please install with: pip install pdftl[geometry]"
            ) from e

        pdf_buffer = io.BytesIO()
        self.pikepdf_doc.save(pdf_buffer)
        pdf_buffer.seek(0)
        self._pdfium_doc = pdfium.PdfDocument(pdf_buffer)

    def calculate_rect(
        self, page_idx: int, parsed: dict, rule_str: str, all_rules: dict
    ) -> tuple[float, float, float, float]:
        """
        Calculates the new CropBox based on 'fit' or 'fit-group' logic.
        Returns (llx, lly, urx, ury) in absolute PDF coordinates.
        """
        mode = parsed["mode"]
        padding = parsed["padding"]  # (l, t, r, b) - Positive expands outwards

        final_bbox = None

        if mode == "fit":
            # Simple case: Just measure this page
            # We pass the pikepdf page to handle coordinate origins/rotations correctly
            pikepdf_page = self.pikepdf_doc.pages[page_idx]
            final_bbox = get_visible_bbox(self.doc[page_idx], pikepdf_page)

        elif mode == "fit-group":
            source_spec = parsed["source"]
            # Cache implicit groups by rule string to avoid re-calculation
            cache_key = source_spec if source_spec else f"implicit:{rule_str}"

            if cache_key in self._group_cache:
                final_bbox = self._group_cache[cache_key]
            else:
                final_bbox = self._calculate_group_union(source_spec, rule_str, all_rules)
                self._group_cache[cache_key] = final_bbox

        # Apply Padding (Left, Top, Right, Bottom)
        # get_visible_bbox returns (x0, y0, x1, y1) where y0 is bottom.
        pad_l, pad_t, pad_r, pad_b = padding

        return (
            final_bbox[0] - pad_l,  # Left: subtract to move left
            final_bbox[1] - pad_b,  # Bottom: subtract to move down
            final_bbox[2] + pad_r,  # Right: add to move right
            final_bbox[3] + pad_t,  # Top: add to move up
        )

    def _calculate_group_union(self, source_spec: str | None, rule_str: str, all_rules: dict):
        """Calculates the union of visible bboxes for a group of pages."""
        # Determine which pages to scan
        if source_spec:
            # Explicit source: "fit-group=1-5"
            indices = [
                x - 1
                for x in page_numbers_matching_page_spec(source_spec, len(self.pikepdf_doc.pages))
            ]
        else:
            # Implicit source: All pages sharing this exact rule string
            indices = [k for k, v in all_rules.items() if v == rule_str]

        # Calculate Union of all source pages
        u_min_x, u_min_y = float("inf"), float("inf")
        u_max_x, u_max_y = float("-inf"), float("-inf")
        found_any = False

        for src_idx in indices:
            # Note: Since we are using the cached pdfium doc, check bounds
            if src_idx >= len(self.doc):
                continue

            pikepdf_page = self.pikepdf_doc.pages[src_idx]
            # get_visible_bbox returns (left, bottom, right, top)
            b = get_visible_bbox(self.doc[src_idx], pikepdf_page)

            # Check if box is valid (width > 0 and height > 0)
            if b[2] > b[0] and b[3] > b[1]:
                found_any = True
                u_min_x = min(u_min_x, b[0])
                u_min_y = min(u_min_y, b[1])
                u_max_x = max(u_max_x, b[2])
                u_max_y = max(u_max_y, b[3])

        if not found_any:
            # Fallback to 0 if group is empty or blank
            return (0.0, 0.0, 0.0, 0.0)

        return (u_min_x, u_min_y, u_max_x, u_max_y)


def get_visible_bbox(page, pikepdf_page=None, scale=1.0, margin=0):
    """
    Calculates the visible bounding box (ink-box) of a page.

    Args:
        page: pypdfium2 page object
        pikepdf_page: The corresponding pikepdf page object (for origin/box lookup)
        scale (float): Render scale. 1.0 = 72 DPI.
        margin (float): Padding in PDF points to add around the crop.

    Returns:
        (left, bottom, right, top) in PDF points (Absolute coordinates).
    """
    from PIL import ImageOps

    # 1. Render to PIL Image
    # We enforce rotation 0 to ensure we are analyzing the content in the
    # raw PDF coordinate space, matching the MediaBox/CropBox dimensions.
    page.set_rotation(0)
    pil_image = page.render(scale=scale).to_pil()

    # 2. Prepare for detection (Invert so ink is non-zero)
    inverted = ImageOps.invert(pil_image.convert("RGB"))

    # 3. Calculate bounding box in PIXELS (Top-Left origin)
    # Returns (left_px, top_px, right_px, bottom_px)
    box_px = inverted.getbbox()

    # Use image size for page dimensions to ensure consistency with the 0-rotation render
    img_width_px, img_height_px = pil_image.size
    page_width_pt = img_width_px / scale
    page_height_pt = img_height_px / scale

    # Determine coordinate system origin (offset)
    origin_x, origin_y = 0.0, 0.0
    if pikepdf_page:
        # Use CropBox if available, else MediaBox. Matches standard rendering behavior.
        box = pikepdf_page.cropbox  # gives mediabox if there is no /CropBox
        origin_x, origin_y = float(box[0]), float(box[1])

    if not box_px:
        # Page is completely blank
        return (
            origin_x,
            origin_y,
            origin_x + page_width_pt,
            origin_y + page_height_pt,
        )

    px_left, px_top, px_right, px_bottom = box_px

    # 4. Convert Pixels -> Points
    # We divide by scale to normalize back to the 72-DPI coordinate space
    pt_left = px_left / scale
    pt_right = px_right / scale
    pt_top_edge = px_top / scale  # Distance from top of page in points
    pt_bottom_edge = px_bottom / scale  # Distance from top of page in points

    # 5. Flip Y-Axis (Image Top-Left -> PDF Bottom-Left RELATIVE TO IMAGE)
    # The image represents the visible page area starting at (0,0) locally.
    pdf_left = pt_left
    pdf_right = pt_right
    pdf_top = page_height_pt - pt_top_edge
    pdf_bottom = page_height_pt - pt_bottom_edge

    # 6. Apply margins and Add Origin Offset
    # We clamp the "local" coordinates to the image bounds (0 to width/height)
    # Then shift by the origin to get absolute PDF coordinates.
    local_left = max(0, pdf_left - margin)
    local_bottom = max(0, pdf_bottom - margin)
    local_right = min(page_width_pt, pdf_right + margin)
    local_top = min(page_height_pt, pdf_top + margin)

    return (
        origin_x + local_left,
        origin_y + local_bottom,
        origin_x + local_right,
        origin_y + local_top,
    )
