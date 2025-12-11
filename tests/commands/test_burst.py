import pikepdf
import pytest

from pdftl.commands.burst import burst_pdf


def test_burst_basic(two_page_pdf):
    """Test standard bursting of a 2-page PDF."""
    # The fixture returns a path, so we must open it
    with pikepdf.open(two_page_pdf) as pdf:
        # burst_pdf expects a list of open PDF objects
        results = list(burst_pdf([pdf]))

        # Assertions
        assert len(results) == 2

        # Check first page output
        fname1, pdf1 = results[0]
        assert fname1 == "pg_0001.pdf"
        assert len(pdf1.pages) == 1

        # Check second page output
        fname2, pdf2 = results[1]
        assert fname2 == "pg_0002.pdf"
        assert len(pdf2.pages) == 1


def test_burst_custom_pattern(two_page_pdf):
    """Test that output_pattern argument works."""
    with pikepdf.open(two_page_pdf) as pdf:
        results = list(burst_pdf([pdf], output_pattern="page_%d.pdf"))

        assert results[0][0] == "page_1.pdf"
        assert results[1][0] == "page_2.pdf"


def test_burst_invalid_pattern(two_page_pdf):
    """Test that the ValueError is raised for bad patterns."""
    with pikepdf.open(two_page_pdf) as pdf:
        with pytest.raises(ValueError, match="Output pattern must include"):
            list(burst_pdf([pdf], output_pattern="bad_filename.pdf"))


def test_burst_multiple_inputs(two_page_pdf):
    """Test passing multiple PDF documents at once."""
    with pikepdf.open(two_page_pdf) as pdf:
        # Pass the same PDF object twice to simulate multiple inputs
        results = list(burst_pdf([pdf, pdf]))

        # Should be 2 pages + 2 pages = 4 outputs
        assert len(results) == 4
        # The counter should increment continuously (1, 2, 3, 4)
        assert results[3][0] == "pg_0004.pdf"
