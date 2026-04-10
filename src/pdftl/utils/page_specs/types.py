from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Pdf


@dataclass
class PageTransform:
    """A dataclass for passing page transformation data around"""

    pdf: "Pdf"
    index: int
    rotation: tuple[int | float, bool]
    scale: float


@dataclass(frozen=True)
class PageSpec:
    """A structured representation of a parsed page specification."""

    start: int
    end: int
    step: int
    rotate: tuple[int, bool]
    scale: float
    qualifiers: set[str]
    omissions: list[tuple[int, int]]

    def __tuple__(self):
        return (
            self.start,
            self.end,
            self.step,
            self.rotate,
            self.scale,
            self.qualifiers,
            self.omissions,
        )
