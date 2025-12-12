from unittest.mock import MagicMock

import pytest
from pikepdf import Array, Dictionary, Page

# --- Import the functions to test ---
from pdftl.utils.scale import (
    _scale_all_annots_in_page,
    _scale_rect,
    _scale_standard_page_boxes,
    apply_scaling,
)

# --- Test _scale_rect ---


@pytest.mark.parametrize(
    "rect_in, scale, rect_out",
    [
        ([10, 20, 100, 200], 2.0, [20.0, 40.0, 200.0, 400.0]),
        ([0, 0, 50, 50], 1.0, [0.0, 0.0, 50.0, 50.0]),
        ([10, 10, 10, 10], 0.5, [5.0, 5.0, 5.0, 5.0]),
        ([-10, -20, 0, 0], 3.0, [-30.0, -60.0, 0.0, 0.0]),
    ],
)
def test_scale_rect(rect_in, scale, rect_out):
    """Tests the pure _scale_rect function."""
    assert _scale_rect(rect_in, scale) == rect_out


# --- Test _scale_standard_page_boxes ---


def test_scale_standard_page_boxes(mocker):
    """
    Tests that standard page boxes are scaled in-place,
    while other keys are ignored.
    """
    # Arrange
    # Mock the external constant this function depends on
    mocker.patch("pdftl.utils.scale.PAGE_BOXES", ["/MediaBox", "/CropBox"])

    page = Dictionary(
        {
            "/MediaBox": Array([0, 0, 100, 200]),
            "/CropBox": Array([10, 10, 90, 190]),
            "/ArtBox": Array([0, 0, 0, 0]),  # Should be ignored (not in our mock)
            "/OtherKey": "value",
        }
    )

    # Act
    _scale_standard_page_boxes(page, 2.0)

    # Assert
    assert page.MediaBox == Array([0.0, 0.0, 200.0, 400.0])
    assert page.CropBox == Array([20.0, 20.0, 180.0, 380.0])
    assert page.ArtBox == Array([0, 0, 0, 0])
    assert page.OtherKey == "value"


def test_scale_all_annots_in_page():
    """
    Tests that all annotations with a /Rect attribute
    have that rect scaled in-place.
    """
    # Arrange
    annot1 = Dictionary({"/Rect": Array([10, 10, 20, 20])})
    annot2 = Dictionary({"/Rect": Array([50, 50, 60, 60])})
    annot3 = Dictionary({"/Other": "No Rect"})  # No /Rect, should be skipped

    # We need a mock page that can have .Annots
    page = MagicMock(spec=Page)
    page.Annots = [annot1, annot2, annot3]

    # Act
    _scale_all_annots_in_page(page, 2.0)

    # Assert
    assert annot1.Rect == Array([20.0, 20.0, 40.0, 40.0])
    assert annot2.Rect == Array([100.0, 100.0, 120.0, 120.0])
    assert "/Rect" not in annot3


def test_scale_all_annots_in_page_no_annots():
    """
    Tests that the function runs without crashing if
    the page has no /Annots key.
    """
    # Arrange
    page = MagicMock(spec=Page)
    # Simulate no /Annots key by deleting the attribute
    # from the MagicMock (so getattr() will use its default)
    del page.Annots

    # Act & Assert (should not crash)
    try:
        _scale_all_annots_in_page(page, 2.0)
    except AttributeError as e:
        if "Annots" in str(e):
            pytest.fail("Crashed on page with no Annots")
        else:
            raise e  # Re-raise other errors


# --- Test apply_scaling ---


def test_apply_scaling_no_op():
    """
    Tests that if scale is 1.0, the function returns early
    and no modifications are made.
    """
    # Arrange
    page = MagicMock(spec=Page)

    # Act
    apply_scaling(page, 1.0)

    # Assert
    page.contents_add.assert_not_called()


def test_apply_scaling_orchestration(mocker):
    """
    Tests that apply_scaling correctly calls all its helper
    functions with the correct arguments.
    """
    # Arrange
    # Patch the helper functions *within the scale module*
    mock_scale_boxes = mocker.patch("pdftl.utils.scale._scale_standard_page_boxes")
    mock_scale_annots = mocker.patch("pdftl.utils.scale._scale_all_annots_in_page")

    page = MagicMock(spec=Page)
    scale = 3.0

    # Act
    apply_scaling(page, scale)

    # Assert
    # 1. Check that the box scaling helper was called
    mock_scale_boxes.assert_called_once_with(page, scale)

    # 2. Check that the content stream was modified
    expected_bytes = b"3.0 0 0 3.0 0 0 cm"
    page.contents_add.assert_called_once_with(expected_bytes, prepend=True)

    # 3. Check that the annotation scaling helper was called
    mock_scale_annots.assert_called_once_with(page, scale)
