from unittest.mock import MagicMock

import pikepdf
import pytest
from pikepdf import Matrix

from pdftl.utils.geometry import (
    _resolve_anchor,
    calculate_fit_metrics,
    calculate_placement_matrix,
    transform_quadpoints,
    transform_rect_bbox,
)


@pytest.fixture
def mock_page():
    """Provides a page with known MediaBox and TrimBox."""
    with pikepdf.new() as pdf:
        pdf.add_blank_page(page_size=(200, 300))
        page = pdf.pages[0]
        # Set a TrimBox smaller than MediaBox to test line 31
        page.TrimBox = [10, 10, 110, 110]  # x=10, y=10, w=100, h=100
        yield page


def test_calculate_placement_matrix_trimbox(mock_page):
    """Hits lines 28-48 and verifies TrimBox is prioritized over MediaBox."""
    # Place source (center of its 100x100 trimbox) at dest 0,0
    # Source anchor center is (10+50, 10+50) = (60, 60)
    matrix = calculate_placement_matrix(mock_page, 0, 0, anchor_source="center")

    # Unpack shorthand for verification
    a, b, c, d, e, f = matrix.shorthand
    # Translation (e, f) should be -60, -60
    assert e == -60.0
    assert f == -60.0


def test_calculate_placement_matrix_mediabox_fallback():
    """Hits line 31 fallback when TrimBox is missing."""
    with pikepdf.new() as pdf:
        pdf.add_blank_page(page_size=(100, 100))
        page = pdf.pages[0]
        # Ensure no TrimBox exists
        if "/TrimBox" in page:
            del page.TrimBox

        matrix = calculate_placement_matrix(page, 10, 10, anchor_source="left-bottom")
        # bottom-left of 100x100 MediaBox is 0,0. Shift to 10,10.
        assert matrix.shorthand[4:] == (10.0, 10.0)


def test_resolve_anchor_variants():
    """Hits lines 105-113 and 120, 127 (the 'else' branches for center)."""
    x, y, w, h = 0, 0, 100, 100

    # Test "left-top" (swapped order logic in line 105)
    assert _resolve_anchor("left-top", x, y, w, h) == (0, 100)

    # Test "right" only (line 113 & 127)
    assert _resolve_anchor("right", x, y, w, h) == (100, 50)

    # Test "bottom" only (line 111 & 120)
    assert _resolve_anchor("bottom", x, y, w, h) == (50, 0)

    # Test single part "left" (line 113)
    assert _resolve_anchor("left", x, y, w, h) == (0, 50)


def test_calculate_fit_metrics_edge_cases():
    """Hits lines 154-177."""
    # Line 154: Invalid source dimensions
    assert calculate_fit_metrics(0, 100, 50, 50) == (1.0, 1.0, 0.0, 0.0)

    # Lines 165-177: preserve_aspect_ratio = False (Stretching)
    # Fit 100x100 into 200x400
    sx, sy, dx, dy = calculate_fit_metrics(100, 100, 200, 400, preserve_aspect_ratio=False)
    assert sx == 2.0
    assert sy == 4.0
    assert dx == 0.0  # Stretched to fill, no offset
    assert dy == 0.0

    # Lines 161-164: preserve_aspect_ratio = True (Letterboxing)
    # Fit 100x100 into 200x400. s = min(2, 4) = 2.0
    sx, sy, dx, dy = calculate_fit_metrics(100, 100, 200, 400, preserve_aspect_ratio=True)
    assert sx == 2.0
    assert sy == 2.0
    # final_w = 200, final_h = 200. dx = (200-200)/2 = 0, dy = (400-200)/2 = 100
    assert dx == 0.0
    assert dy == 100.0


def test_transform_quadpoints():
    """Hits lines 75-79: Tests point-pair transformations."""
    # Quadpoints are usually sets of 8 (4 points), but the function
    # handles any even number of floats.
    quads = [0.0, 0.0, 10.0, 10.0]
    matrix = Matrix().translated(5, 5)

    new_quads = transform_quadpoints(quads, matrix)
    assert new_quads == [5.0, 5.0, 15.0, 15.0]


def test_transform_point_and_matrix_array():
    """Hits lines 86-87: Tests the internal point math."""
    # Moving (10, 10) by a translation matrix
    matrix = Matrix().translated(10, 10)
    # This triggers _transform_point which uses m.as_array()
    from pdftl.utils.geometry import _transform_point

    nx, ny = _transform_point(10, 10, matrix)

    assert nx == 20.0
    assert ny == 20.0


def test_resolve_anchor_top_center():
    """Hits lines 102-104: The vertical-first split branch."""
    x, y, w, h = 0, 0, 100, 100
    # This hits: if parts[0] in ["top", "bottom", "center"]
    # followed by: if len(parts) > 1: h_pos = parts[1]
    res = _resolve_anchor("top-center", x, y, w, h)
    assert res == (50.0, 100.0)


def test_resolve_anchor_shorthand_vonly():
    """Hits line 102 but without a horizontal component."""
    x, y, w, h = 0, 0, 100, 100
    # Hits the logic where it's a split but only one part or no second part
    # though usually handled by the 'else' in line 120/127
    res = _resolve_anchor("top", x, y, w, h)
    assert res == (50.0, 100.0)


def test_transform_rect_bbox():
    """Hits lines 56-68: Tests AABB calculation after rotation."""
    # A 10x10 square at origin [x1, y1, x2, y2]
    rect = [0, 0, 10, 10]

    # Rotate 45 degrees around the origin
    matrix = Matrix().rotated(45)

    bbox = transform_rect_bbox(rect, matrix)

    # Math check:
    # Corner (10,10) becomes (10cos45 - 10sin45, 10sin45 + 10cos45) = (0, 14.14)
    # Corner (10,0) becomes (7.07, 7.07)
    # Corner (0,10) becomes (-7.07, 7.07)
    # So the AABB is approx [-7.07, 0, 7.07, 14.14]

    assert bbox[0] == pytest.approx(-7.071, abs=1e-3)  # Min X
    assert bbox[1] == pytest.approx(0.0, abs=1e-3)  # Min Y
    assert bbox[2] == pytest.approx(7.071, abs=1e-3)  # Max X
    assert bbox[3] == pytest.approx(14.142, abs=1e-3)  # Max Y


def test_calculate_placement_matrix_compensates_for_rotation_shift():
    """
    Tests that rotating a page by -90 degrees correctly shifts the
    visual bounding box so the content doesn't render off-grid.
    """
    # 1. Setup a Mock Page (100 pts wide, 200 pts high)
    mock_page = MagicMock()
    mock_page.trimbox = [0.0, 0.0, 100.0, 200.0]

    dest_x = 50.0
    dest_y = 50.0

    # 2. Calculate the matrix with a -90 degree rotation
    matrix = calculate_placement_matrix(
        source_page=mock_page,
        dest_x=dest_x,
        dest_y=dest_y,
        scale_x=1.0,
        scale_y=1.0,
        rotate=-90.0,
        anchor_source="bottom-left",
    )

    # 3. Analyze what we expect
    # A 100x200 page rotated -90 degrees around (0,0) falls into the negative Y space.
    # The bottom-right corner (100, 0) swings down to (0, -100).
    # To place this at a destination of Y=50, the matrix MUST shift the Y axis up
    # by an extra 100 points to prevent the page from hanging below the destination line.
    # Therefore, the final Y translation (matrix.f) should be 150 (50 + 100), not 50.

    # Check transformation (rotation) components using pytest.approx
    # to handle tiny floating-point artifacts from math.cos/sin
    assert matrix.a == pytest.approx(0.0, abs=1e-9)  # cos(-90)
    assert matrix.b == pytest.approx(-1.0, abs=1e-9)  # sin(-90)
    assert matrix.c == pytest.approx(1.0, abs=1e-9)  # -sin(-90)
    assert matrix.d == pytest.approx(0.0, abs=1e-9)  # cos(-90)

    # Check the translation components!
    # BUGGY CODE would return e=50, f=50.
    # FIXED CODE will return e=50, f=150.
    assert matrix.e == pytest.approx(50.0, abs=1e-9)
    assert matrix.f == pytest.approx(150.0, abs=1e-9)


def test_calculate_placement_matrix_handles_cropped_origin():
    """
    Tests that a cropped page (non-zero origin) rotates correctly
    around its own visual boundary, not the absolute document origin.
    """
    # 1. Setup a Mock Cropped Page
    # The page was cropped, so its visual boundary starts at X=100, Y=100.
    # It has a width of 100 (200-100) and a height of 200 (300-100).
    mock_page = MagicMock()
    mock_page.trimbox = [100.0, 100.0, 200.0, 300.0]

    dest_x = 50.0
    dest_y = 50.0

    # 2. Calculate matrix with a +90 degree rotation
    matrix = calculate_placement_matrix(
        source_page=mock_page,
        dest_x=dest_x,
        dest_y=dest_y,
        scale_x=1.0,
        scale_y=1.0,
        rotate=90.0,
        anchor_source="bottom-left",
    )

    # 3. Analyze what we expect
    # The page is 100x200.
    # Step A: The function shifts it by (-100, -100) so its bottom-left is at (0,0).
    # Step B: It rotates +90. The 100x200 box now points left, sitting in the negative X space.
    #         The original top-left corner (0, 200) swings to (-200, 0).
    # Step C: The function detects the bounding box is between X=[-200, 0] and Y=[0, 100].
    # Step D: It aligns this visual bounding box to (0,0) by adding (+200, 0).
    # Step E: It moves it to the destination (50, 50).
    #
    # When we multiply all these matrices together for pikepdf (v_new = v @ Matrix):
    # - Original X coordinate needs a total translation of +350.
    # - Original Y coordinate needs a total translation of -50.

    assert matrix.a == pytest.approx(0.0, abs=1e-9)  # cos(90)
    assert matrix.b == pytest.approx(1.0, abs=1e-9)  # sin(90)
    assert matrix.c == pytest.approx(-1.0, abs=1e-9)  # -sin(90)
    assert matrix.d == pytest.approx(0.0, abs=1e-9)  # cos(90)

    # Check the translation components!
    assert matrix.e == pytest.approx(350.0, abs=1e-9)
    assert matrix.f == pytest.approx(-50.0, abs=1e-9)
