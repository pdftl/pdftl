# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/utils/images/test_placement.py

from __future__ import annotations

import pytest
from pdftl.utils.images.placement import calculate_placement_matrix


def test_calculate_placement_matrix_invalid_inputs():
    """Verifies that invalid or zero/negative dimensions return safe boundary defaults."""
    # Zero image size
    a, b, c, d, e, f = calculate_placement_matrix(
        img_size=(0, 100),
        box_bounds=(0, 0, 500, 500),
    )
    assert (a, b, c, d, e, f) == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    # Zero box size
    a, b, c, d, e, f = calculate_placement_matrix(
        img_size=(100, 100),
        box_bounds=(10, 10, 10, 500),
    )
    assert (a, b, c, d, e, f) == (0.0, 0.0, 0.0, 0.0, 10.0, 10.0)


def test_calculate_placement_matrix_no_scaling():
    """Tests standard placement when no scaling is requested."""
    # Image size 100x200 inside 500x500 box, anchored at bottom-left
    a, b, c, d, e, f = calculate_placement_matrix(
        img_size=(100, 200),
        box_bounds=(50, 50, 550, 550),
        scale_mode="none",
        anchor="bottom-left",
    )
    assert a == 100.0
    assert d == 200.0
    assert e == 50.0
    assert f == 50.0


def test_calculate_placement_matrix_stretch():
    """Tests stretching scaling strategy with and without requested bounds."""
    # Stretch to fill entirely the custom requested size
    a, b, c, d, e, f = calculate_placement_matrix(
        img_size=(100, 100),
        box_bounds=(0, 0, 500, 500),
        requested_size=(300, 400),
        scale_mode="stretch",
    )
    assert a == 300.0
    assert d == 400.0

    # Stretch without requested size should default to the bounding box
    a, b, c, d, e, f = calculate_placement_matrix(
        img_size=(100, 100),
        box_bounds=(50, 50, 350, 450),
        scale_mode="stretch",
    )
    assert a == 300.0  # 350 - 50
    assert d == 400.0  # 450 - 50


def test_calculate_placement_matrix_fit():
    """Tests fitting an image entirely inside bounds while preserving aspect ratio."""
    # Image aspect ratio is wider than target box aspect ratio (2:1 vs 1:1)
    a, b, c, d, e, f = calculate_placement_matrix(
        img_size=(200, 100),
        box_bounds=(0, 0, 500, 500),
        scale_mode="fit",
        anchor="center",
    )
    # Target scale fits horizontally first: scaling factor is 500 / 200 = 2.5
    assert a == pytest.approx(500.0)
    assert d == pytest.approx(250.0)
    # Centered vertically: (500 - 250) / 2 = 125
    assert e == pytest.approx(0.0)
    assert f == pytest.approx(125.0)

    # Image aspect ratio is taller than target box aspect ratio (1:2 vs 1:1)
    a, b, c, d, e, f = calculate_placement_matrix(
        img_size=(100, 200),
        box_bounds=(0, 0, 500, 500),
        scale_mode="fit",
        anchor="center",
    )
    # Target scale fits vertically first: scaling factor is 500 / 200 = 2.5
    assert a == pytest.approx(250.0)
    assert d == pytest.approx(500.0)
    # Centered horizontally: (500 - 250) / 2 = 125
    assert e == pytest.approx(125.0)
    assert f == pytest.approx(0.0)


def test_calculate_placement_matrix_fill():
    """Tests filling a boundary box while preserving aspect ratio (cropping overflows)."""
    # Image is wider than box (2:1 vs 1:1), should scale to match height and let width bleed out
    a, b, c, d, e, f = calculate_placement_matrix(
        img_size=(200, 100),
        box_bounds=(0, 0, 500, 500),
        scale_mode="fill",
        anchor="center",
    )
    # Fits vertically: scaling factor is 500 / 100 = 5.0
    assert a == pytest.approx(1000.0)
    assert d == pytest.approx(500.0)
    # Centered horizontally: (500 - 1000) / 2 = -250
    assert e == pytest.approx(-250.0)
    assert f == pytest.approx(0.0)


def test_calculate_placement_matrix_explicit_both_dimensions():
    """Verifies that providing both dimensions with scale_mode 'none' uses them directly."""
    a, b, c, d, e, f = calculate_placement_matrix(
        img_size=(100, 200),
        box_bounds=(0, 0, 500, 500),
        requested_size=(300, 400),
        scale_mode="none",
    )
    assert a == 300.0
    assert d == 400.0


def test_calculate_placement_matrix_implicit_aspect_ratio():
    """Tests auto-calculating missing dimension when only one size is specified."""
    # Only width specified
    a, b, c, d, e, f = calculate_placement_matrix(
        img_size=(100, 200),
        box_bounds=(0, 0, 500, 500),
        requested_size=(300, None),
    )
    assert a == 300.0
    assert d == 600.0  # (300 / 100) * 200

    # Only height specified
    a, b, c, d, e, f = calculate_placement_matrix(
        img_size=(100, 200),
        box_bounds=(0, 0, 500, 500),
        requested_size=(None, 400),
    )
    assert a == 200.0  # (400 / 200) * 100
    assert d == 400.0


def test_calculate_placement_matrix_anchors():
    """Verifies that all 9 anchoring alignments resolve layout coordinates perfectly."""
    img_size = (100, 100)
    box = (10, 20, 210, 320)  # Box width=200, height=300

    anchors_expected = {
        "bottom-left": (10.0, 20.0),
        "bottom-center": (60.0, 20.0),  # x = 10 + (200 - 100) / 2
        "bottom-right": (110.0, 20.0),  # x = 10 + 200 - 100
        "center-left": (10.0, 120.0),  # y = 20 + (300 - 100) / 2
        "center": (60.0, 120.0),
        "center-right": (110.0, 120.0),
        "top-left": (10.0, 220.0),  # y = 20 + 300 - 100
        "top-center": (60.0, 220.0),
        "top-right": (110.0, 220.0),
    }

    for anchor_name, (expected_x, expected_y) in anchors_expected.items():
        _, _, _, _, x, y = calculate_placement_matrix(
            img_size=img_size,
            box_bounds=box,
            anchor=anchor_name,
        )
        assert x == pytest.approx(expected_x), f"Anchor {anchor_name} failed on X coordinate"
        assert y == pytest.approx(expected_y), f"Anchor {anchor_name} failed on Y coordinate"


def test_calculate_placement_matrix_offset():
    """Verifies relative offset parameter displacements are added cleanly."""
    a, b, c, d, e, f = calculate_placement_matrix(
        img_size=(100, 100),
        box_bounds=(0, 0, 500, 500),
        anchor="bottom-left",
        offset=(25.5, -45.0),
    )
    assert e == pytest.approx(25.5)
    assert f == pytest.approx(-45.0)
