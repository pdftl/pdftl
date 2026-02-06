import pytest

from pdftl.layouts.base import LayoutSlot, LayoutStrategy
from pdftl.layouts.grid import GridLayout


def test_grid_layout_implements_protocol():
    """Verify GridLayout matches the LayoutStrategy protocol at runtime."""
    layout = GridLayout(columns=2, rows=2)
    assert isinstance(layout, LayoutStrategy)


def test_grid_calculation_single_page():
    """Test a 2x2 grid on a 100x100 canvas with no margins/gutters."""
    layout = GridLayout(columns=2, rows=2)
    # canvas 100x100 means each slot is 50x50
    slots = list(layout.generate_slots(item_count=4, canvas_width=100, canvas_height=100))

    assert len(slots) == 4

    # Slot 0: Top-Left (PDF y is bottom-left of the slot)
    # Row 0, Col 0: x=0, y=100-50=50
    assert slots[0] == LayoutSlot(page_index=0, x=0.0, y=50.0, width=50.0, height=50.0)

    # Slot 3: Bottom-Right
    # Row 1, Col 1: x=50, y=100-50-50=0
    assert slots[3] == LayoutSlot(page_index=0, x=50.0, y=0.0, width=50.0, height=50.0)


def test_grid_with_margins_and_gutters():
    """Test complex math with margins and gutters."""
    # 2 columns, 1 row. Margin 10, Gutter 10. Canvas 110 wide.
    # Usable width = 110 - (2*10) = 90
    # Total gutter = (2-1)*10 = 10
    # Slot width = (90 - 10) / 2 = 40
    layout = GridLayout(columns=2, rows=1, margin=10, gutter=10)
    slots = list(layout.generate_slots(item_count=2, canvas_width=110, canvas_height=100))

    assert slots[0].x == 10.0  # Left margin
    assert slots[1].x == 60.0  # margin (10) + slot_w (40) + gutter (10)
    assert slots[0].width == 40.0


def test_grid_page_wrapping():
    """Verify that items wrap to the next page index correctly."""
    # 1x1 grid means every item is a new page
    layout = GridLayout(columns=1, rows=1, start_page_index=1)
    slots = list(layout.generate_slots(item_count=3, canvas_width=100, canvas_height=100))

    assert slots[0].page_index == 1
    assert slots[1].page_index == 2
    assert slots[2].page_index == 3


def test_grid_invalid_dimensions():
    """Verify that zero or negative dimensions return an empty iterator."""
    # 0 columns
    layout = GridLayout(columns=0, rows=2)
    slots = list(layout.generate_slots(4, 100, 100))
    assert len(slots) == 0

    # Margin exceeds canvas
    layout = GridLayout(columns=2, rows=2, margin=60)  # 2*60 = 120 > 100
    slots = list(layout.generate_slots(4, 100, 100))
    assert len(slots) == 0
