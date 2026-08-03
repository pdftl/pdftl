# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
# src/pdftl/operations/helpers/excise_geometry.py
"""Path (fill/stroke) geometry and overlap-deletion helpers for excise."""

from __future__ import annotations

from typing import Any

from pdftl.utils.geometry import rects_overlap, rect_contains
from pdftl.utils.graphics_state import multiply_matrices
from pdftl.utils.path_types import Path
from pdftl.operations.helpers.excise_types import ExciseRect, ExciseStats

# CTM in effect at the start of an untransformed content stream. Used as a
# fallback when a Path somehow lacks a state_snapshot (shouldn't happen in
# practice -- _Segmenter always sets one -- but keeps path_bbox total).
IDENTITY_CTM: tuple[float, ...] = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def transform_point_local(x: float, y: float, ctm: tuple[float, ...]) -> tuple[float, float]:
    """Applies a raw 6-tuple CTM to a single (x, y) user-space point.

    A local, dependency-free copy of geometry._transform_point's math
    (that helper is module-private in geometry.py) -- kept in sync by
    convention since both implement the same spec formula.
    """
    a, b, c, d, e, f = ctm
    return (a * x + c * y + e, b * x + d * y + f)


def points_bbox(points: list[tuple[float, float]], ctm: tuple[float, ...]) -> list[float] | None:
    """Device-space AABB of a list of user-space points under ctm, or None
    if points is empty (degenerate/malformed subpath -- caller should treat
    that as 'cannot test overlap, keep by default')."""
    if not points:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for x, y in points:
        dx, dy = transform_point_local(x, y, ctm)
        xs.append(dx)
        ys.append(dy)
    return [min(xs), min(ys), max(xs), max(ys)]


def path_bbox(path: Path, ctm: tuple[float, ...]) -> list[float] | None:
    """Device-space AABB across all of a Path's subpaths (used for
    whole-Path fill deletion, not per-subpath stroke deletion)."""
    all_points = [pt for sp in path.subpaths for pt in sp.points]
    return points_bbox(all_points, ctm)


def _matches(bbox: list[float], excise_rect: ExciseRect) -> bool:
    """Whether a unit's device-space bbox counts as INSIDE excise_rect's
    target region, per excise_rect.partial: "inside" (default) = any
    overlap with ANY box in the region counts as inside; "outside" = the
    unit must be entirely contained within at least one single box in
    the region to count as inside.

    The region is a UNION of one or more boxes (excise_rect.rects --
    ordinarily just [excise_rect.rect], but redact hands in several
    boxes per page as one ExciseRect). Containment is tested per-box
    rather than against each box's own bbox union, since a box straddling
    two disjoint regions of the union isn't "contained" in either one --
    e.g. with partial=outside, a unit spanning two separate redaction
    boxes should NOT count as inside just because it's inside their
    combined bounding box.
    """
    rects = excise_rect.rects
    if excise_rect.partial == "outside":
        return any(rect_contains(bbox, r) for r in rects)
    return any(rects_overlap(bbox, r) for r in rects)


def overlap_means_delete(bbox: list[float], excise_rect: ExciseRect) -> bool:
    """Applies excise_rect.delete's direction to the inside/outside test:
    delete="inside" (default, the redaction use case) deletes when the
    unit counts as INSIDE; delete="outside" deletes when it does NOT
    count as inside. "partial" (inside/outside) controls whether a
    partially-overlapping unit counts as inside in the first place --
    see _matches.
    """
    is_inside = _matches(bbox, excise_rect)
    return is_inside if excise_rect.delete == "inside" else not is_inside


def filter_path(
    path: Path, excise_rect: ExciseRect, stats: ExciseStats, initial_ctm: tuple[float, ...]
) -> list[Any]:
    """Decides the fate of one segmented Path and returns the raw
    instructions that should survive into the output stream (empty list
    if the whole Path is deleted).

    Clip paths are never deletion candidates (locked policy: they draw no
    visible pixels themselves, and deleting one that overlaps could
    unpredictably change later content's visibility) -- always passed
    through verbatim, and not counted in paths_total/paths_deleted since
    they were never a deletion candidate to begin with.
    """
    if path.is_clipping:
        return list(path.original_instructions)

    stats.paths_total += 1
    local_ctm = path.state_snapshot.ctm if path.state_snapshot is not None else IDENTITY_CTM
    ctm = multiply_matrices(local_ctm, initial_ctm)

    is_stroke = path.paint_op in ("S", "s")
    has_provenance = is_stroke and all(sp.instructions is not None for sp in path.subpaths)

    if has_provenance:
        return filter_stroke_path(path, ctm, excise_rect, stats)
    return filter_fill_path(path, ctm, excise_rect, stats)


def filter_fill_path(
    path: Path, ctm: tuple[float, ...], excise_rect: ExciseRect, stats: ExciseStats
) -> list[Any]:
    """Whole-Path-unit deletion on overlap -- used for fill paths, and as
    the fallback for stroke paths lacking per-subpath instruction
    provenance (e.g. if track_instructions somehow wasn't set)."""
    bbox = path_bbox(path, ctm)
    if bbox is None:
        return list(path.original_instructions)

    should_delete = overlap_means_delete(bbox, excise_rect)
    if should_delete:
        stats.paths_deleted += 1
        return []
    return list(path.original_instructions)


def filter_stroke_path(
    path: Path, ctm: tuple[float, ...], excise_rect: ExciseRect, stats: ExciseStats
) -> list[Any]:
    """Per-Subpath granularity stroke deletion, using each Subpath's raw
    instruction slice (Subpath.instructions, populated by
    segment(..., track_instructions=True))."""
    kept_instructions: list[Any] = []
    any_kept = False
    any_dropped = False

    for sp in path.subpaths:
        sp_bbox = points_bbox(sp.points, ctm)
        if sp_bbox is None:
            kept_instructions.extend(sp.instructions or [])
            any_kept = True
            continue

        should_delete = overlap_means_delete(sp_bbox, excise_rect)
        if should_delete:
            any_dropped = True
            stats.subpaths_deleted += 1
        else:
            any_kept = True
            kept_instructions.extend(sp.instructions or [])

    if not any_kept:
        stats.paths_deleted += 1
        return []

    if not any_dropped:
        return list(path.original_instructions)

    result = list(kept_instructions)
    if path.original_instructions:
        result.append(path.original_instructions[-1])
    return result
