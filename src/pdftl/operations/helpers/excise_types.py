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
    """One page's target rectangle in unrotated PDF user-space points,
    plus the delete/partial direction for that rectangle."""

    rect: list[float]  # [x0, y0, x1, y1]
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
