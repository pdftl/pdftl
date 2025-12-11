import pytest
from pikepdf import Pdf, Rectangle

from pdftl.commands.rotate import rotate_pdf
from pdftl.exceptions import InvalidArgumentError


@pytest.fixture
def mock_pdf():
    """Mock a simple PDF structure with multiple pages."""
    pdf = Pdf.new()
    pdf.pages.append(pdf.add_blank_page(page_size=(300, 400)))
    pdf.pages.append(pdf.add_blank_page(page_size=(500, 600)))
    return pdf


def test_rotate_pdf_90_degrees(mock_pdf):
    """Test that pages rotate correctly by 90 degrees."""
    specs = ["1-endright"]  # Rotate all pages 90 degrees clockwise
    result = rotate_pdf(mock_pdf, specs)
    # After rotation, the width and height should swap
    mediabox = result.pages[0].mediabox
    mediarect = Rectangle(*mediabox)
    assert result.pages[0].Rotate == 90, "Rotation key should be set to 90"
    assert mediarect.width == 300, "mediabox width should not change"
    assert mediarect.height == 400, "mediabox height should not change"


def test_rotate_pdf_invalid_spec(mock_pdf):
    """Test handling of an invalid rotation spec."""
    specs = ["invalid_spec"]
    with pytest.raises(InvalidArgumentError):
        rotate_pdf(mock_pdf, specs)
