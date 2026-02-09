from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Set, Tuple

if TYPE_CHECKING:
    from pikepdf import Pdf


@dataclass
class PageTransform:
    """A dataclass for passing page transformation data around"""

    pdf: "Pdf"
    index: int
    rotation: Tuple[int | float, bool]
    scale: float


@dataclass(frozen=True)
class PageSpec:
    """A structured representation of a parsed page specification."""

    start: int
    end: int
    rotate: Tuple[int, bool]
    scale: float
    qualifiers: Set[str]
    omissions: List[Tuple[int, int]]

    def __tuple__(self):
        return (
            self.start,
            self.end,
            self.rotate,
            self.scale,
            self.qualifiers,
            self.omissions,
        )
