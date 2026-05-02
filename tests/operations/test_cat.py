import pikepdf
import pytest

from pdftl.api import cat
from pdftl.exceptions import OperationError
from pdftl.operations.cat import cat_pages


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


def test_cat_step(twelve_page_pdf):
    in_pdf = pikepdf.open(twelve_page_pdf)
    assert len(in_pdf.pages) == 12

    out = cat(twelve_page_pdf, operation_args=["1-4"])
    assert len(out.pages) == 4
    for output_index in range(4):
        assert (
            out.pages[output_index].Contents.read_bytes()
            == in_pdf.pages[output_index].Contents.read_bytes()
        )

    out = cat(twelve_page_pdf, operation_args=["step3"])
    assert len(out.pages) == 4
    for output_index, input_page_number in enumerate([1, 4, 7, 10]):
        assert (
            out.pages[output_index].Contents.read_bytes()
            == in_pdf.pages[input_page_number - 1].Contents.read_bytes()
        )

    out = cat(twelve_page_pdf, operation_args=["step2"])
    assert len(out.pages) == 6
    for output_index, input_page_number in enumerate([1, 3, 5, 7, 9, 11]):
        assert (
            out.pages[output_index].Contents.read_bytes()
            == in_pdf.pages[input_page_number - 1].Contents.read_bytes()
        )

    out = cat(twelve_page_pdf, operation_args=["5-endstep2"])
    assert len(out.pages) == 4
    for output_index, input_page_number in enumerate([5, 7, 9, 11]):
        assert (
            out.pages[output_index].Contents.read_bytes()
            == in_pdf.pages[input_page_number - 1].Contents.read_bytes()
        )


def test_cat_simple(pdf_a):
    """Test concatenating a single file."""
    inputs = ["input.pdf"]
    opened_pdfs = [pdf_a]
    specs = ["1-end"]
    aliases = {}

    result = cat_pages(inputs, specs, opened_pdfs, aliases).pdf

    assert len(result.pages) == 2
    assert isinstance(result, pikepdf.Pdf)


def test_cat_multiple_with_handles(pdf_a, pdf_b):
    """Test concatenating two files using handles."""
    inputs = ["a.pdf", "b.pdf"]
    opened_pdfs = [pdf_a, pdf_b]

    # Aliases must map Handle -> Integer Index
    # "A" points to index 0 (pdf_a), "B" points to index 1 (pdf_b)
    aliases = {"A": 0, "B": 1}

    specs = ["A", "B"]

    result = cat_pages(inputs, specs, opened_pdfs, aliases).pdf

    # 2 pages from A + 1 page from B = 3 pages
    assert len(result.pages) == 3


def test_cat_no_pages_error(pdf_b):
    """Test error when valid specs result in ZERO pages."""
    inputs = ["input.pdf"]
    opened_pdfs = [pdf_b]  # pdf_b has 1 page (odd)

    # Asking for "even" pages from a 1-page PDF results in an empty list
    specs = ["1-endeven"]

    with pytest.raises(OperationError, match="Range specifications gave no pages"):
        cat_pages(inputs, specs, opened_pdfs, {})
