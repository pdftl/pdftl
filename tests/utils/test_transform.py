import logging
from unittest.mock import MagicMock, call, patch

import pikepdf
import pytest
from pikepdf import Array, Pdf

# --- Import functions to test ---
from pdftl.utils.transform import (
    _rotate_pair,
    transform_destination_coordinates,
    transform_pdf,
)

# --- Tests for _rotate_pair ---


@pytest.mark.parametrize(
    "angle, x_in, y_in, w, h, x_out, y_out",
    [
        (0, 10, 20, 100, 200, 10, 20),  # No rotation
        (90, 10, 20, 100, 200, 180, 10),  # 90 deg: (h-y, x)
        (180, 10, 20, 100, 200, 90, 180),  # 180 deg: (w-x, h-y)
        (270, 10, 20, 100, 200, 20, 90),  # 270 deg: (y, w-x)
    ],
)
def test_rotate_pair_valid_angles(angle, x_in, y_in, w, h, x_out, y_out):
    """Tests the coordinate transformation for 0, 90, 180, 270 degrees."""
    result = _rotate_pair(angle, x_in, y_in, w, h)
    assert result == (x_out, y_out)


def test_rotate_pair_unsupported_angle(caplog):
    """Tests that an unsupported angle logs a warning and returns original coords."""
    # caplog is a pytest fixture that captures log output
    caplog.set_level(logging.WARNING)

    result = _rotate_pair(45, 10, 20, 100, 200)
    assert result == (10, 20)  # Should return original coords
    assert "Unsupported rotation angle 45°" in caplog.text


# --- Tests for transform_destination_coordinates ---

TEST_BOX = [0, 0, 100, 200]  # width=100, height=200


@pytest.mark.parametrize(
    "coords_in, box, angle, scale, coords_out",
    [
        # No op
        ([10, 20, 0], TEST_BOX, 0, 1.0, [10.0, 20.0, 0.0]),
        # Rotation only (90 deg)
        ([10, 20, 0], TEST_BOX, 90, 1.0, [180.0, 10.0, 0.0]),
        # Rotation only (180 deg)
        ([10, 20, 0], TEST_BOX, 180, 1.0, [90.0, 180.0, 0.0]),
        # Rotation only (270 deg)
        ([10, 20, 0], TEST_BOX, 270, 1.0, [20.0, 90.0, 0.0]),
        # Scaling only
        ([10, 20, 0], TEST_BOX, 0, 2.0, [20.0, 40.0, 0.0]),
        # Rotation (90) AND Scaling (2.0)
        # (h-y, x) -> (180, 10) -> (180*2, 10*2) -> (360, 20)
        ([10, 20, 0], TEST_BOX, 90, 2.0, [360.0, 20.0, 0.0]),
        # Rotation (180) AND Scaling (0.5)
        # (w-x, h-y) -> (90, 180) -> (90*0.5, 180*0.5) -> (45, 90)
        ([10, 20, 0], TEST_BOX, 180, 0.5, [45.0, 90.0, 0.0]),
        # Handle None in coordinates (x=None, y=20) -> (h-y, x) -> (180, None) -> scaled (360, None)
        ([None, 20, 0], TEST_BOX, 90, 2.0, [360.0, None, 0.0]),
        # Handle None in coordinates (x=10, y=None) -> (h-y, x) -> (None, 10) -> scaled (None, 20)
        ([10, None, 0], TEST_BOX, 90, 2.0, [None, 20.0, 0.0]),
        # Handle extra coords (like zoom)
        ([10, 20, 0.5, 500], TEST_BOX, 0, 1.0, [10.0, 20.0, 0.5, 500.0]),
        # Handle extra coords with rotation and scaling
        ([10, 20, 0.5, 500], TEST_BOX, 270, 3.0, [60.0, 270.0, 0.5, 500.0]),
    ],
)
def test_transform_destination_coordinates(coords_in, box, angle, scale, coords_out):
    """
    Tests various combinations of rotation and scaling on /XYZ coordinates.
    """
    # Use pikepdf.Array to match one of the type hints
    page_box_array = Array(box)
    result = transform_destination_coordinates(coords_in, page_box_array, angle, scale)
    assert result == coords_out


# --- Tests for transform_pdf ---


@pytest.fixture
def mock_pdf():
    """Creates a mock pikepdf.Pdf object with 4 mock pages."""
    # We use a real Pdf object so len() works, but mock its pages
    pdf = Pdf.new()
    pdf.add_blank_page()
    pdf.add_blank_page()
    pdf.add_blank_page()
    pdf.add_blank_page()

    # Replace the real pages with mocks so we can check calls
    mock_pages = [
        MagicMock(spec=pikepdf.Page),
        MagicMock(spec=pikepdf.Page),
        MagicMock(spec=pikepdf.Page),
        MagicMock(spec=pikepdf.Page),
    ]
    # We patch .pages to return our list of mocks
    with patch.object(Pdf, "pages", new=mock_pages):
        yield pdf


@patch("pdftl.utils.transform.apply_scaling")
@patch("pdftl.utils.page_specs.parse_sub_page_spec")
def test_transform_pdf(
    mock_parse_spec,
    mock_apply_scaling,
    mock_pdf,
):
    """
    Tests the orchestration logic of transform_pdf.
    """
    # --- Arrange ---
    spec_str = "1,3"

    # Define a side effect to return different specs based on input
    def parser_side_effect(spec, total_pages):
        m = MagicMock()
        m.rotate = (90, True)
        m.scale = 2.0
        m.qualifiers = set()
        m.omissions = []

        if spec == "1":
            m.start = 1
            m.end = 1
        elif spec == "3":
            m.start = 3
            m.end = 3
        else:
            # Default fallback (shouldn't happen with "1,3")
            m.start = 1
            m.end = total_pages
        return m

    mock_parse_spec.side_effect = parser_side_effect

    # Get references to the mock pages
    page1 = mock_pdf.pages[0]
    page2 = mock_pdf.pages[1]
    page3 = mock_pdf.pages[2]
    page4 = mock_pdf.pages[3]

    # --- Act ---
    returned_pdf = transform_pdf(mock_pdf, [spec_str])

    # --- Assert ---
    assert returned_pdf is mock_pdf

    # Check that our spec parsers were called correctly
    expected_calls = [call("1", 4), call("3", 4)]
    mock_parse_spec.assert_has_calls(expected_calls, any_order=True)

    # Check transformations on PAGE 1
    mock_apply_scaling.assert_any_call(page1, 2.0)
    page1.rotate.assert_called_with(90, relative=True)

    # Check transformations on PAGE 3
    mock_apply_scaling.assert_any_call(page3, 2.0)
    page3.rotate.assert_called_with(90, relative=True)

    # Check that pages 2 and 4 were NOT touched
    page2.rotate.assert_not_called()
    page4.rotate.assert_not_called()
