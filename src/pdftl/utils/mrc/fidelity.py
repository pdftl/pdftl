# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/mrc/fidelity.py

"""How much detail MRC layers lose against the source they were built from.

Text the stencil misses falls into the low-resolution background and
smears; that shows as lost edge energy in the tiles holding it.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

#: Mean |gradient| (luma levels) for a tile to count as holding detail.
DETAIL = 6.0
#: Tiles are this fraction of an inch square.
TILE_INCH = 0.5
#: Share of tiles (the worst) that may lose more than the reported loss.
WORST_SHARE = 0.1


def _gradient(rows: np.ndarray) -> np.ndarray:
    """|dx| + |dy| for all but the last row and column."""
    rows = rows.astype(np.int16)
    return np.abs(np.diff(rows, axis=1))[:-1] + np.abs(np.diff(rows, axis=0))[:, :-1]


def _tile_means(grad: np.ndarray, tile: int) -> np.ndarray:
    columns = grad.shape[1] // tile
    cropped = grad[:tile, : columns * tile]
    return cropped.reshape(tile, columns, tile).mean(axis=(0, 2))


def edge_loss(
    gray: np.ndarray,
    ink: np.ndarray,
    foreground: Image.Image,
    background: Image.Image,
    dpi: float,
) -> float:
    """Edge energy the composite loses, in its worst detailed tiles (0 = none, 1 = all)."""
    h, w = gray.shape
    fg = np.asarray(foreground.convert("L").resize((w, h), Image.BILINEAR), dtype=np.uint8)
    bg = np.asarray(background.convert("L").resize((w, h), Image.BILINEAR), dtype=np.uint8)
    composite = np.where(ink, fg, bg)
    del fg, bg
    tile = max(int(dpi * TILE_INCH), 4)
    losses = []
    for top in range(0, h - tile, tile):
        rows = slice(top, top + tile + 1)
        source = _tile_means(_gradient(gray[rows]), tile)
        result = _tile_means(_gradient(composite[rows]), tile)
        detailed = source >= DETAIL
        losses.append(np.maximum(0.0, 1.0 - result[detailed] / source[detailed]))
    found = np.concatenate(losses) if losses else np.empty(0)
    if found.size == 0:
        return 0.0
    return float(np.percentile(found, 100 * (1 - WORST_SHARE)))
