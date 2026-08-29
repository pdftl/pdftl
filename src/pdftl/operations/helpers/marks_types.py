# src/pdftl/operations/helpers/marks_types.py
"""Configuration type for the add_marks operation."""

from __future__ import annotations

from dataclasses import dataclass

from pdftl.operations.helpers.marks_geometry import MARK_STYLES, WEIGHTS

DEFAULT_OFFSET_PT = 9.0
DEFAULT_LENGTH_PT = 18.0
DEFAULT_WEIGHT_PT = 0.25


@dataclass(frozen=True)
class MarksConfig:
    """One page's fully-resolved printer-marks request.

    `cropmarks` is "western", "japanese", or None (no crop marks drawn).
    """

    cropmarks: str | None = "western"
    registration: bool = True
    colorbars: bool = False
    pageinfo: bool = False
    startarget: bool = False
    weight: float = DEFAULT_WEIGHT_PT
    offset: float = DEFAULT_OFFSET_PT
    length: float = DEFAULT_LENGTH_PT

    def __post_init__(self):
        if self.cropmarks is not None and self.cropmarks not in MARK_STYLES:
            raise ValueError(f"cropmarks must be one of {MARK_STYLES} or false")
        if self.weight not in WEIGHTS:
            weights = ", ".join(f"{w} pt" for w in WEIGHTS)
            raise ValueError(f"weight must be one of {weights}")
        if self.offset < 0:
            raise ValueError("offset must be zero or more")
        if self.length <= 0:
            raise ValueError("length must be positive")

    @property
    def wants_anything(self) -> bool:
        """False means: draw nothing, and if marks exist already, remove them."""
        return (
            bool(self.cropmarks)
            or self.registration
            or self.colorbars
            or self.pageinfo
            or self.startarget
        )
