import pikepdf
import pytest

from pdftl.operations.shuffle import shuffle_pdfs


@pytest.fixture
def pdf_a():
    """Create a 2-page dummy PDF."""
    p = pikepdf.new()
    p.add_blank_page()
    p.add_blank_page()
    return p


@pytest.fixture
def pdf_b():
    """Create a 1-page dummy PDF."""
    p = pikepdf.new()
    p.add_blank_page()
    return p


def test_shuffle_basic(pdf_a, pdf_b):
    """Test shuffling two documents."""
    inputs = ["a.pdf", "b.pdf"]
    opened_pdfs = [pdf_a, pdf_b]

    # Aliases must map Handle -> Integer Index
    aliases = {"A": 0, "B": 1}
    specs = []

    result = shuffle_pdfs(inputs, specs, opened_pdfs, aliases).pdf

    # 2 pages + 1 page = 3 pages total
    assert len(result.pages) == 3


def test_shuffle_with_specs(pdf_a, pdf_b):
    """Test shuffling specific ranges."""
    inputs = ["a.pdf", "b.pdf"]
    opened_pdfs = [pdf_a, pdf_b]

    # Aliases must map Handle -> Integer Index
    aliases = {"A": 0, "B": 1}
    specs = ["A1", "B1"]

    result = shuffle_pdfs(inputs, specs, opened_pdfs, aliases).pdf

    assert len(result.pages) == 2


def test_shuffle_no_pages_error(pdf_b):
    """Test shuffle when specs result in zero pages (should just return empty PDF)."""
    inputs = ["b.pdf"]
    opened_pdfs = [pdf_b]
    aliases = {"B": 0}
    specs = ["Beven"]  # 'Even' pages of a 1-page PDF -> Empty list

    # It seems shuffle doesn't raise ValueError, it just returns an empty PDF
    result = shuffle_pdfs(inputs, specs, opened_pdfs, aliases).pdf
    assert len(result.pages) == 0
