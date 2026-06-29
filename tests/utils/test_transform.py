# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/utils/test_transform.py

import logging
from unittest.mock import MagicMock, patch

import pikepdf
import pytest
from pikepdf import Array, Pdf

from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.page_specs import PageTransform

# --- Import functions to test ---
from pdftl.utils.transform import (
    _get_float_or_none,
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
        ([10, 20, 0], TEST_BOX, 90, 2.0, [360.0, 20.0, 0.0]),
        # Rotation (180) AND Scaling (0.5)
        ([10, 20, 0], TEST_BOX, 180, 0.5, [45.0, 90.0, 0.0]),
        # Handle None in coordinates
        ([None, 20, 0], TEST_BOX, 90, 2.0, [360.0, None, 0.0]),
        ([10, None, 0], TEST_BOX, 90, 2.0, [None, 20.0, 0.0]),
        # Handle extra coords (like zoom)
        ([10, 20, 0.5, 500], TEST_BOX, 0, 1.0, [10.0, 20.0, 0.5, 500.0]),
        # Handle extra coords with rotation and scaling
        ([10, 20, 0.5, 500], TEST_BOX, 270, 3.0, [60.0, 270.0, 0.5, 500.0]),
        # Handle empty coords (coverage for line 135)
        ([], TEST_BOX, 90, 1.0, []),
        # Handle fallback raw coords uncastable to float (coverage for lines 258-259)
        ([10, 20, "not-a-float", []], TEST_BOX, 0, 1.0, [10.0, 20.0, "not-a-float", []]),
    ],
)
def test_transform_destination_coordinates(coords_in, box, angle, scale, coords_out):
    """
    Tests various combinations of rotation and scaling on /XYZ coordinates.
    """
    page_box_array = Array(box)
    result = transform_destination_coordinates(coords_in, page_box_array, angle, scale)
    assert result == coords_out


def test_transform_destination_coordinates_str_throws():
    """Hits the except (ValueError, TypeError) block when str() fails on coords[1]."""

    class BadStr:
        def __str__(self):
            raise ValueError("I refuse to be a string")

    bad_obj = BadStr()
    coords = [10, bad_obj, 20]
    box = Array([0, 0, 100, 200])

    res = transform_destination_coordinates(coords, box, 0, 1.0)
    assert res == [10.0, None, 20.0]


# --- Tests for transform_pdf orchestration ---
@pytest.fixture
def mock_pdf():
    """Creates a mock pikepdf.Pdf object with 4 mock pages."""
    pdf = Pdf.new()
    for _ in range(4):
        pdf.add_blank_page()

    mock_pages = [
        MagicMock(spec=pikepdf.Page),
        MagicMock(spec=pikepdf.Page),
        MagicMock(spec=pikepdf.Page),
        MagicMock(spec=pikepdf.Page),
    ]
    with patch.object(Pdf, "pages", new=mock_pages):
        yield pdf


@patch("pdftl.utils.transform.apply_scaling")
@patch("pdftl.utils.transform.expand_specs_to_pages")
def test_transform_pdf(mock_expand, mock_apply_scaling, mock_pdf):
    spec_str = "1,3"
    mock_pages = [MagicMock(name=f"page{i}") for i in range(4)]
    mock_pdf.pages = mock_pages

    m1 = MagicMock(name="transform1")
    m1.index = 0
    m1.rotation = (90, True)
    m1.scale = 2.0

    m3 = MagicMock(name="transform3")
    m3.index = 2
    m3.rotation = (90, True)
    m3.scale = 2.0

    mock_expand.return_value = [m1, m3]

    returned_pdf = transform_pdf(mock_pdf, [spec_str])

    assert returned_pdf is mock_pdf
    mock_expand.assert_called_once_with([spec_str], opened_pdfs=[mock_pdf])

    mock_apply_scaling.assert_any_call(mock_pages[0], 2.0)
    mock_pages[0].rotate.assert_called_with(90, relative=True)

    mock_apply_scaling.assert_any_call(mock_pages[2], 2.0)
    mock_pages[2].rotate.assert_called_with(90, relative=True)

    mock_pages[1].rotate.assert_not_called()
    mock_pages[3].rotate.assert_not_called()


@pytest.fixture
def dummy_pdf():
    pdf = pikepdf.new()
    for _ in range(5):
        pdf.add_blank_page()
    return pdf


def test_transform_even_odd_qualifiers(dummy_pdf):
    specs = ["evenright", "odddown"]
    transform_pdf(dummy_pdf, specs)
    assert dummy_pdf.pages[0].get("/Rotate") == 180
    assert dummy_pdf.pages[1].get("/Rotate") == 90


def test_transform_omissions(dummy_pdf):
    specs = ["1-5~3right"]
    transform_pdf(dummy_pdf, specs)
    assert dummy_pdf.pages[0].get("/Rotate") == 90
    assert dummy_pdf.pages[2].get("/Rotate") is None
    assert dummy_pdf.pages[4].get("/Rotate") == 90


def test_transform_page_out_of_bounds(dummy_pdf):
    specs = ["10right"]
    with pytest.raises(InvalidArgumentError) as exc:
        transform_pdf(dummy_pdf, specs)
    assert "only 5 pages" in str(exc.value)


def test_transform_pdf_index_error():
    mock_pdf = MagicMock()
    mock_pdf.pages = [MagicMock()]
    mock_spec = MagicMock()
    mock_spec.index = 5
    mock_spec.rotation = (0, False)
    mock_spec.scale = 1.0

    with patch("pdftl.utils.transform.expand_specs_to_pages", return_value=[mock_spec]):
        with pytest.raises(InvalidArgumentError, match="Page 6 does not exist"):
            transform_pdf(mock_pdf, ["ignored_spec"])


def test_rotate_pair_invalid_angle(caplog):
    with caplog.at_level(logging.WARNING):
        x, y = _rotate_pair(45, 10, 10, 100, 100)
    assert "Unsupported rotation angle 45" in caplog.text
    assert x == 10
    assert y == 10


def test_transform_pdf_invalid_angle(dummy_pdf):
    dummy_pdf.add_blank_page()
    bad_transform = PageTransform(dummy_pdf, index=0, rotation=(45, False), scale=1.0)
    with pytest.raises(InvalidArgumentError, match="multiple of 90 degrees"):
        with patch("pdftl.utils.transform.expand_specs_to_pages", return_value=[bad_transform]):
            transform_pdf(dummy_pdf, ["ignored_spec"])


# --- Additional ISO 32000-2 Alignment Tests ---


def test_get_float_or_none():
    assert _get_float_or_none(10) == 10.0
    assert _get_float_or_none("20.5") == 20.5
    assert _get_float_or_none(None) is None
    assert _get_float_or_none(pikepdf.Name("/null")) is None
    assert _get_float_or_none("invalid") is None
    assert _get_float_or_none(pikepdf.Name("/Page1")) is None


@pytest.mark.parametrize(
    "dest_in, box, angle, scale, dest_out",
    [
        # /XYZ full array
        (
            [pikepdf.Name("/Page1"), pikepdf.Name("/XYZ"), 10, 20, 1.5],
            TEST_BOX,
            90,
            1.0,
            [pikepdf.Name("/Page1"), pikepdf.Name("/XYZ"), 180.0, 10.0, 1.5],
        ),
        # /XYZ full array with /null and scaling
        (
            [pikepdf.Name("/Page1"), pikepdf.Name("/XYZ"), pikepdf.Name("/null"), 20, 1.5],
            TEST_BOX,
            90,
            2.0,
            [pikepdf.Name("/Page1"), pikepdf.Name("/XYZ"), 360.0, None, 1.5],
        ),
        # /Fit and /FitB (parameterless, nothing happens)
        (
            [pikepdf.Name("/Page1"), pikepdf.Name("/Fit")],
            TEST_BOX,
            90,
            2.0,
            [pikepdf.Name("/Page1"), pikepdf.Name("/Fit")],
        ),
        (
            [pikepdf.Name("/Page1"), pikepdf.Name("/FitB")],
            TEST_BOX,
            180,
            1.0,
            [pikepdf.Name("/Page1"), pikepdf.Name("/FitB")],
        ),
        # /FitH changes to /FitV on 90 deg
        (
            [pikepdf.Name("/Page1"), pikepdf.Name("/FitH"), 20],
            TEST_BOX,
            90,
            1.0,
            [pikepdf.Name("/Page1"), pikepdf.Name("/FitV"), 180.0],
        ),
        # /FitH stays /FitH on 180 deg
        (
            [pikepdf.Name("/Page1"), pikepdf.Name("/FitH"), 20],
            TEST_BOX,
            180,
            1.0,
            [pikepdf.Name("/Page1"), pikepdf.Name("/FitH"), 180.0],
        ),
        # /FitV changes to /FitH on 90 deg
        (
            [pikepdf.Name("/Page1"), pikepdf.Name("/FitV"), 10],
            TEST_BOX,
            90,
            1.0,
            [pikepdf.Name("/Page1"), pikepdf.Name("/FitH"), 10.0],
        ),
        # /FitV stays /FitV on 180 deg (coverage for lines 204-205)
        (
            [pikepdf.Name("/Page1"), pikepdf.Name("/FitV"), 10],
            TEST_BOX,
            180,
            1.0,
            [pikepdf.Name("/Page1"), pikepdf.Name("/FitV"), 90.0],
        ),
        # /FitBH changes to /FitBV on 270 deg
        (
            [pikepdf.Name("/Page1"), pikepdf.Name("/FitBH"), 20],
            TEST_BOX,
            270,
            1.0,
            [pikepdf.Name("/Page1"), pikepdf.Name("/FitBV"), 20.0],
        ),
        # /FitR full transform
        # in: left=10, bottom=20, right=30, top=40
        # 90 deg geometry:
        # left,bottom (10,20) -> (200-20, 10) = (180, 10)
        # right,top (30,40) -> (200-40, 30) = (160, 30)
        # new left=min(180,160)=160, right=max(180,160)=180
        # new bottom=min(10,30)=10, top=max(10,30)=30
        # out: [160, 10, 180, 30]
        (
            [pikepdf.Name("/Page1"), pikepdf.Name("/FitR"), 10, 20, 30, 40],
            TEST_BOX,
            90,
            1.0,
            [pikepdf.Name("/Page1"), pikepdf.Name("/FitR"), 160.0, 10.0, 180.0, 30.0],
        ),
    ],
)
def test_transform_explicit_destination(dest_in, box, angle, scale, dest_out):
    """Verifies that full ISO 32000-2 explicit destination arrays transform dynamically."""
    page_box_array = pikepdf.Array(box)
    dest_array = pikepdf.Array(dest_in)

    result = transform_destination_coordinates(dest_array, page_box_array, angle, scale)

    assert len(result) == len(dest_out)
    for r, e in zip(result, dest_out):
        if isinstance(e, pikepdf.Name):
            assert isinstance(r, pikepdf.Name)
            assert str(r) == str(e)
        else:
            assert r == e
