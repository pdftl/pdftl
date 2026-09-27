# tests/utils/test_mrc_fidelity.py
#
# Ground truth: patterns whose edge energy is known exactly. A checkerboard
# of 1-pixel cells in black/white has |dx| + |dy| = 510 at every pixel.

import numpy as np
import pytest
from PIL import Image

from pdftl.utils.mrc import fidelity

DPI = 40  # 20-pixel tiles


def _checker(h, w):
    y, x = np.mgrid[0:h, 0:w]
    return np.where((x + y) % 2, 255, 0).astype(np.uint8)


def _flat(value, size):
    return Image.new("L", size, value)


def test_flat_source_has_no_detail_to_lose():
    gray = np.full((100, 100), 200, np.uint8)
    ink = np.zeros_like(gray, dtype=bool)
    assert fidelity.edge_loss(gray, ink, _flat(0, (5, 5)), _flat(200, (5, 5)), DPI) == 0.0


def test_stencil_that_captures_every_stroke_loses_nothing():
    gray = _checker(100, 100)
    ink = gray == 0
    # Black ink over white paper reproduces the checkerboard exactly.
    loss = fidelity.edge_loss(gray, ink, _flat(0, (5, 5)), _flat(255, (5, 5)), DPI)
    assert loss == 0.0


def test_missed_strokes_smeared_into_flat_background_lose_everything():
    gray = _checker(100, 100)
    ink = np.zeros_like(gray, dtype=bool)
    loss = fidelity.edge_loss(gray, ink, _flat(0, (5, 5)), _flat(128, (5, 5)), DPI)
    assert loss == 1.0


def test_reported_loss_is_that_of_the_worst_tenth_of_detailed_tiles():
    # 101 px gives 100 gradients: 5 x 5 tiles of 20 px. Detail in the top 3
    # tile rows (15 tiles); the stencil captures 13 tiles and misses 2.
    gray = np.full((101, 101), 255, np.uint8)
    gray[:60] = _checker(60, 101)
    ink = gray == 0
    ink[40:60, 60:] = False
    loss = fidelity.edge_loss(gray, ink, _flat(0, (5, 5)), _flat(255, (5, 5)), DPI)
    # The 90th percentile of thirteen 0s and two ~1s lies between them.
    losses = np.array([0.0] * 13 + [1.0, 1.0])
    assert loss == pytest.approx(np.percentile(losses, 90), abs=0.05)


def test_image_smaller_than_a_tile_counts_as_no_detail():
    gray = _checker(10, 10)
    ink = np.zeros_like(gray, dtype=bool)
    assert fidelity.edge_loss(gray, ink, _flat(0, (2, 2)), _flat(128, (2, 2)), DPI) == 0.0
