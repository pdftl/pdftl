# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
# src/pdftl/operations/helpers/redact_geometry.py
"""Bbox-clustering helpers for `redact`.

grep-style text search produces one bbox PER LINE of each match (see
TextProvider.get_bboxes_for_lines), and a redact spec can have many
matches on one page (e.g. every SSN on a densely-packed form). Passed
straight through, that's one excise deletion pass per bbox -- correct,
but wasteful, and it also means the drawn "redacted" box (if requested)
is a pile of separate rects rather than a few clean ones.

merge_rects() clusters bboxes that are "close enough" (per `mode`) into
their bounding-box union, using a union-find over all pairwwise-mergeable
bboxes. Two independent merge heuristics are offered (see `mode`):

  * "line" (default): only merge bboxes that plausibly sit on the same
    printed line -- their y-ranges overlap (or nearly do, within a
    height-relative vertical tolerance, to absorb baseline/leading
    jitter between adjacent lines of a match) -- AND their x-ranges are
    within `merge_distance` points of each other. This never merges
    across unrelated rows (e.g. two table columns, or two different
    paragraph lines that happen to be horizontally close), which a
    naive Euclidean-distance threshold would do.

  * "area": row-agnostic. Merges whenever the union bbox's area isn't
    much bigger than the sum of the two source areas (ratio controlled
    by `merge_ratio`), regardless of relative position. This is a
    reasonable choice when matches are known to cluster in blob-like
    regions rather than along text lines (e.g. redacting a signature
    block or a stamped image region).
"""

from __future__ import annotations

from pdftl.utils.geometry import rects_overlap

# Default horizontal gap (points) within which two same-line bboxes are
# still considered part of one merged redaction box -- roughly one
# average space-character's width at common body-text sizes. Deliberately
# a plain constant, not derived from any one match's own font size: grep
# hits merged together can come from different fonts/sizes entirely.
DEFAULT_MERGE_DISTANCE = 6.0

# Default union-area / sum-of-areas ratio ceiling for "area" mode. A
# value of 2.0 means: merge two boxes if their union is no more than
# twice as large as their combined individual areas (i.e. they overlap
# or sit close together relative to their own size); a lower value is
# stricter (closer to requiring actual overlap), a higher value merges
# more aggressively across empty space.
DEFAULT_MERGE_RATIO = 2.0

# Vertical tolerance for "line" mode, as a fraction of the shorter of the
# two bboxes' own heights -- absorbs the kind of baseline/leading jitter
# TextProvider._group_into_lines already tolerates (its own line-grouping
# threshold is 0.6x height) without merging genuinely different lines.
_LINE_VERTICAL_TOLERANCE_FRAC = 0.6


def _y_gap(a: list[float], b: list[float]) -> float:
    """Vertical gap between two bboxes' y-ranges; 0 (or negative) if they overlap."""
    return max(a[1], b[1]) - min(a[3], b[3])


def _x_gap(a: list[float], b: list[float]) -> float:
    """Horizontal gap between two bboxes' x-ranges; 0 (or negative) if they overlap."""
    return max(a[0], b[0]) - min(a[2], b[2])


def _rect_area(r: list[float]) -> float:
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def _union_bbox(rects: list[list[float]]) -> list[float]:
    return [
        min(r[0] for r in rects),
        min(r[1] for r in rects),
        max(r[2] for r in rects),
        max(r[3] for r in rects),
    ]


def _should_merge_line(a: list[float], b: list[float], merge_distance: float) -> bool:
    """ "line" mode: same-row bboxes (y-ranges overlap, or are within a
    height-relative tolerance) within merge_distance points horizontally."""
    height = min(a[3] - a[1], b[3] - b[1]) or 1.0
    if _y_gap(a, b) > height * _LINE_VERTICAL_TOLERANCE_FRAC:
        return False
    return _x_gap(a, b) <= merge_distance


def _should_merge_area(a: list[float], b: list[float], merge_ratio: float) -> bool:
    """ "area" mode: merge if the union isn't much bigger than the sum of parts."""
    if rects_overlap(a, b):
        return True
    area_a, area_b = _rect_area(a), _rect_area(b)
    if area_a == 0.0 or area_b == 0.0:
        return False
    union_area = _rect_area(_union_bbox([a, b]))
    return union_area <= merge_ratio * (area_a + area_b)


class _UnionFind:
    """Minimal union-find (disjoint-set) over range(n), path-compressed,
    union-by-rank -- local to this module since no shared utility exists
    elsewhere in the codebase for it."""

    def __init__(self, n: int) -> None:
        self._parent = list(range(n))
        self._rank = [0] * n

    def find(self, i: int) -> int:
        root = i
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[i] != root:
            self._parent[i], i = root, self._parent[i]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1


def merge_rects(
    rects: list[list[float]],
    mode: str = "line",
    merge_distance: float = DEFAULT_MERGE_DISTANCE,
    merge_ratio: float = DEFAULT_MERGE_RATIO,
) -> list[list[float]]:
    """Clusters `rects` ([x0, y0, x1, y1] each) into their per-cluster
    bounding-box unions, using the "line" or "area" merge heuristic (see
    module docstring). Order of the returned rects is not meaningful.

    O(n^2) pairwise comparisons -- deliberately simple, since redact's
    per-page match counts are small in practice (dozens, not thousands);
    a spatial index would be premature here.
    """
    n = len(rects)
    if n <= 1:
        return [list(r) for r in rects]

    uf = _UnionFind(n)
    should_merge = _should_merge_line if mode == "line" else _should_merge_area
    param = merge_distance if mode == "line" else merge_ratio

    for i in range(n):
        for j in range(i + 1, n):
            if should_merge(rects[i], rects[j], param):
                uf.union(i, j)

    clusters: dict[int, list[list[float]]] = {}
    for i, r in enumerate(rects):
        clusters.setdefault(uf.find(i), []).append(r)

    return [_union_bbox(cluster) for cluster in clusters.values()]
