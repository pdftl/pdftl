import pytest

from pdftl.layouts.base import LayoutSlot


def test_layout_slot_immutability():
    """Ensure LayoutSlot is frozen as per dataclass definition."""
    slot = LayoutSlot(page_index=0, x=10, y=10, width=50, height=50)
    with pytest.raises(AttributeError):
        slot.x = 20  # type: ignore
