from dataclasses import dataclass, field
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
    step: int = 1
    block_rep: int = 1
    rep: int = 1
    rotate: tuple[int, bool] = (0, True)
    scale: float = 1.0
    qualifiers: set[str] = field(default_factory=set)
    omissions: list[tuple[int, int]] = field(default_factory=list)

    def __tuple__(self):
        return (
            self.start,
            self.end,
            self.step,
            self.block_rep,
            self.rep,
            self.rotate,
            self.scale,
            self.qualifiers,
            self.omissions,
        )
