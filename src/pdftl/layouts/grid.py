from collections.abc import Iterator
from dataclasses import dataclass

from .base import LayoutSlot


@dataclass
class GridLayout:
    """
    A concrete strategy for row/column grids.
    """

    columns: int
    rows: int
    margin: float = 0.0
    gutter: float = 0.0
    start_page_index: int = 0

    def generate_slots(
        self, item_count: int, canvas_width: float, canvas_height: float
    ) -> Iterator[LayoutSlot]:
        # 1. Validate Dimensions
        usable_w = canvas_width - (2 * self.margin)
        usable_h = canvas_height - (2 * self.margin)

        if usable_w <= 0 or usable_h <= 0 or self.columns <= 0 or self.rows <= 0:
            return

        # 2. Calculate Slot Size
        total_gutter_w = (self.columns - 1) * self.gutter
        total_gutter_h = (self.rows - 1) * self.gutter

        slot_w = (usable_w - total_gutter_w) / self.columns
        slot_h = (usable_h - total_gutter_h) / self.rows

        # 3. Generate
        current_page = self.start_page_index
        items_per_page = self.columns * self.rows

        # Grid layout starts visually at Top-Left
        # PDF Y-axis starts at Bottom-Left.
        # Top Y limit = Height - Margin
        top_limit = canvas_height - self.margin

        for i in range(item_count):
            # Page Wrap Logic
            page_local_index = i % items_per_page
            if i > 0 and page_local_index == 0:
                current_page += 1

            # Grid Coordinates (Row, Col)
            row = page_local_index // self.columns
            col = page_local_index % self.columns

            # X: Left Margin + (Col * Width) + (Col * Gutter)
            x = self.margin + (col * (slot_w + self.gutter))

            # Y: Top Limit - (Row * Height) - (Row * Gutter) - Height
            # (Subtracting height because (x,y) is the bottom-left corner of the rect)
            y = top_limit - (row * (slot_h + self.gutter)) - slot_h

            yield LayoutSlot(page_index=current_page, x=x, y=y, width=slot_w, height=slot_h)
