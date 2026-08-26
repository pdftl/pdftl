from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.operations.chop import chop_pages


def test_chop_basic(two_page_pdf):
    """Test chopping pages into rows (horizontal split)."""
    with pikepdf.open(two_page_pdf) as pdf:
        # "rows" defaults to 2 equal pieces per page
        # 2 input pages * 2 pieces = 4 output pages
        specs = ["rows"]
        result = chop_pages(pdf, specs).pdf

        assert len(result.pages) == 4

        # Extract the boxes
        box0 = result.pages[0].mediabox
        box1 = result.pages[1].mediabox

        # Calculate width and height for Piece 0
        w0 = box0[2] - box0[0]
        h0 = box0[3] - box0[1]

        # Calculate width and height for Piece 1
        w1 = box1[2] - box1[0]
        h1 = box1[3] - box1[1]

        # Assert they have the same dimensions
        assert w0 == w1
        assert h0 == h1


def test_chop_specific_spec(two_page_pdf):
    """Test chopping with a specific column spec."""
    with pikepdf.open(two_page_pdf) as pdf:
        # "cols3" -> 3 vertical columns
        # 2 input pages * 3 pieces = 6 output pages
        specs = ["cols3"]
        result = chop_pages(pdf, specs).pdf
        assert len(result.pages) == 6


def test_chop_no_spec_defaults(two_page_pdf):
    """Test that empty specs default to 'cols' (2 columns)."""
    with pikepdf.open(two_page_pdf) as pdf:
        result = chop_pages(pdf, []).pdf
        # 2 pages * 2 cols = 4 pages
        assert len(result.pages) == 4


def test_chop_unmatched_page_pass_through():
    """
    Covers line 183: final_pages.append(source_page)
    Occurs when a page index is not found in page_rules.
    """
    # Setup PDF with 2 pages
    mock_pdf = MagicMock()
    p1 = MagicMock(name="Page1")
    p2 = MagicMock(name="Page2")
    # Determine length for range
    mock_pdf.pages = [p1, p2]

    # Mock the rules parser to only return a rule for index 0 (Page 1)
    # Page 2 (index 1) will have no rule, triggering line 183
    mock_rules = {0: "cols2"}

    with patch("pdftl.operations.chop.parse_chop_specs_to_rules", return_value=mock_rules):
        # We also mock the internal apply function to avoid complex logic there
        with patch("pdftl.operations.chop._apply_chop_to_page", return_value=[MagicMock()]):
            chop_pages(mock_pdf, ["irrelevant_spec"])

    # Verify behavior:
    # The function deletes all pages and extends with final_pages.
    # We expect p2 (the one without a rule) to be in the final list unmodified.
    assert p2 in mock_pdf.pages


def test_chop_rotated_page(two_page_pdf):
    """
    Test that chopping a rotated page preserves the rotation flag
    and correctly maps visual cuts into physical coordinate cuts.
    """
    with pikepdf.open(two_page_pdf) as pdf:
        # 1. Force a known physical size: Width 400, Height 600
        # (This is unrotated physical canvas space)
        pdf.pages[0].mediabox = [0, 0, 400, 600]

        # 2. Rotate 90 degrees clockwise.
        # Visually, the page on-screen is now Width 600, Height 400.
        pdf.pages[0].Rotate = 90

        # 3. Chop into 2 "rows".
        # This means visually splitting the on-screen height (400) in half.
        result = chop_pages(pdf, ["rows"]).pdf

        # First check: The rotation flag MUST be preserved on the chopped pieces
        assert int(result.pages[0].get("/Rotate", 0)) == 90
        assert int(result.pages[1].get("/Rotate", 0)) == 90

        # Second check: Geometry.
        # A visual horizontal split (rows) on a 90-degree rotated page
        # means we must physically cut the original X-axis (width of 400).
        # The resulting physical boxes should be 200 wide and 600 high.
        box0 = result.pages[0].mediabox

        w0 = box0[2] - box0[0]
        h0 = box0[3] - box0[1]

        # Assert physical dimensions match the mathematically correct cut
        assert w0 == 200
        assert h0 == 600


def test_chop_rotation_90(tmp_path):
    """Tests that rotation=90 correctly maps visual rects to physical space."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 300))
    pdf.pages[0]["/Rotate"] = 90  # landscape via rotation
    result = chop_pages(pdf, ["cols2"])
    assert len(result.pdf.pages) == 2


def test_chop_rotation_180(tmp_path):
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 300))
    pdf.pages[0]["/Rotate"] = 180
    result = chop_pages(pdf, ["cols2"])
    assert len(result.pdf.pages) == 2


def test_chop_rotation_270(tmp_path):
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 300))
    pdf.pages[0]["/Rotate"] = 270
    result = chop_pages(pdf, ["cols2"])
    assert len(result.pdf.pages) == 2


def test_chop_removes_extra_bounding_boxes():
    """Tests that CropBox/TrimBox/BleedBox/ArtBox are removed from chopped pages."""
    import pikepdf

    from pdftl.operations.chop import chop_pages

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 300))
    # Set extra bounding boxes on the page
    pdf.pages[0]["/CropBox"] = pikepdf.Array([0, 0, 200, 300])
    pdf.pages[0]["/TrimBox"] = pikepdf.Array([5, 5, 195, 295])

    result = chop_pages(pdf, ["cols2"])

    for page in result.pdf.pages:
        assert "/CropBox" not in page
        assert "/TrimBox" not in page


def test_chop_raises_on_unexpected_rotation():
    import pikepdf

    from pdftl.exceptions import OperationError
    from pdftl.operations.chop import _apply_chop_to_page

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 300))
    page = pdf.pages[0]
    page["/Rotate"] = 45  # Not 0/90/180/270

    with pytest.raises(OperationError, match="Unexpected rotation"):
        _apply_chop_to_page(pdf, page, "cols2")


def test_chop_with_overlap_end_to_end(two_page_pdf):
    """The two chopped pieces should overlap, so their widths sum to
    more than the original page width by the overlap amount."""
    with pikepdf.open(two_page_pdf) as pdf:
        page_width = float(pdf.pages[0].mediabox[2]) - float(pdf.pages[0].mediabox[0])

        result = chop_pages(pdf, ["cols2+20pt"]).pdf

        box0 = result.pages[0].mediabox
        box1 = result.pages[1].mediabox
        w0 = float(box0[2]) - float(box0[0])
        w1 = float(box1[2]) - float(box1[0])

        assert w0 + w1 == pytest.approx(page_width + 20)
        assert w0 == pytest.approx(w1)
