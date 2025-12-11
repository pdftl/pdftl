import pytest
from pikepdf import Pdf

from pdftl.commands.filter import filter_pdf


@pytest.fixture
def mock_pdf():
    """Mock a simple PDF structure for testing."""
    pdf = Pdf.new()
    pdf.pages.append(pdf.add_blank_page(page_size=(300, 400)))
    pdf.pages.append(pdf.add_blank_page(page_size=(500, 600)))
    return pdf


def test_filter_pdf_no_changes(mock_pdf):
    """Test that the 'filter' operation returns the PDF without changes."""
    result = filter_pdf(mock_pdf)
    assert result is mock_pdf, "The PDF should remain unchanged"


def test_filter_pdf_empty():
    """Test that an empty PDF still returns the same."""
    empty_pdf = Pdf.new()
    result = filter_pdf(empty_pdf)
    assert result == empty_pdf, "Empty PDF should remain unchanged"
