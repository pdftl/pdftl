from dataclasses import dataclass
from typing import Iterator, Protocol, runtime_checkable


@dataclass(frozen=True)
class LayoutSlot:
    """
    Represents a single target destination for content on a specific page.
    """

    page_index: int
    x: float
    y: float
    width: float
    height: float


@runtime_checkable
class LayoutStrategy(Protocol):
    """
    Interface for any logic that determines where content goes.
    """

    def generate_slots(
        self, item_count: int, canvas_width: float, canvas_height: float
    ) -> Iterator[LayoutSlot]:
        """
        Yields a stream of slots for the given number of items.
        """
        ...
