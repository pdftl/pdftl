# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
# src/pdftl/operations/helpers/excise_types.py
"""Shared dataclasses for excise (and later redact): kept dependency-free of
both excise.py and the other excise_* helper modules so all of them can import
from here without any risk of a circular import.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExciseRect:
    """One page's target region in unrotated PDF user-space points, plus
    the delete/partial direction for that region.

    The region is `rect` plus, optionally, `extra_rects` -- a UNION of
    axis-aligned boxes rather than a single one. This is what lets
    `redact` hand excise's engine several disjoint match boxes on one
    page (e.g. every occurrence of an SSN pattern) as a single
    ExciseRect, without the deletion engine needing to know or care that
    the target region isn't a single rectangle: every geometry test
    (`excise_geometry._matches`) is written against the full `rects`
    list, and `rect` is kept as a plain field (rather than folded into
    `extra_rects`) so existing single-rect callers/tests that construct
    `ExciseRect(rect=[...])` and read back `.rect` keep working
    unchanged -- excise itself never sets `extra_rects`.
    """

    rect: list[float]  # [x0, y0, x1, y1] -- always present, the "first" box
    extra_rects: list[list[float]] | None = None  # additional boxes, union
    delete: str = "inside"  # "inside" | "outside" -- content INSIDE the box
    #                          is deleted when delete="inside" (default,
    #                          the redaction use case); "outside" deletes
    #                          everything except what's inside the box.
    partial: str = "inside"  # "inside" | "outside" -- "inside" (default):
    #                           a partially-overlapping unit is treated the
    #                           same as a fully-inside unit. "outside": a
    #                           partially-overlapping unit is treated the
    #                           same as a fully-outside unit (i.e. only
    #                           units ENTIRELY inside the box count as
    #                           inside).
    glyph_overlap: str = "box"  # "box" | "center" -- how a text glyph's
    #                           own nominal advance-width box is tested
    #                           against the region, independent of how
    #                           images/paths are tested (those always use
    #                           `partial`-aware overlap, unconditionally).
    #                           "box" (default): the glyph's box is tested
    #                           the same way as an image/path -- true
    #                           overlap/containment, honoring `partial`
    #                           exactly like every other unit type.
    #                           "center": only the glyph's own midpoint is
    #                           tested for containment, ignoring `partial`
    #                           entirely (a point can't straddle). Exists
    #                           for callers whose match rect comes from a
    #                           different measurement than the glyph's own
    #                           box (e.g. a rendered/ink-based bbox vs. a
    #                           font-advance-derived one) and disagrees by
    #                           a point or so at a boundary; center-point
    #                           containment tolerates that noise instead of
    #                           over/under-deleting at it.

    @property
    def rects(self) -> list[list[float]]:
        """The full union of target boxes: `rect` plus `extra_rects`."""
        if not self.extra_rects:
            return [self.rect]
        return [self.rect, *self.extra_rects]


@dataclass
class ExciseStats:
    """Metrics collected across a full document pass."""

    streams_processed: int = 0
    images_total: int = 0
    images_deleted: int = 0
    annots_total: int = 0
    annots_deleted: int = 0
    paths_total: int = 0
    paths_deleted: int = 0
    subpaths_deleted: int = 0
    glyphs_total: int = 0
    glyphs_deleted: int = 0
