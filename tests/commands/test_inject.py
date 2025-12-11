import pikepdf
import pytest

from pdftl.commands.inject import inject_pdf


def _read_page_content(page):
    """Helper to read page content whether it is a Stream or Array."""
    contents = page.Contents
    if isinstance(contents, pikepdf.Array):
        # Join all parts of the array
        return b"".join(stream.read_bytes() for stream in contents)
    else:
        return contents.read_bytes()


@pytest.fixture
def pdf():
    p = pikepdf.new()
    p.add_blank_page()
    # Initialize with a real stream so injection has something to modify
    p.pages[0].Contents = p.make_stream(b"0 g ")
    return p


def test_inject_head_basic(pdf):
    """Test injecting code at the head of all pages."""
    inject_args = ["head", "0.5 G"]

    inject_pdf(pdf, inject_args)

    # Use helper to read content safely
    stream = _read_page_content(pdf.pages[0])

    assert b"0.5 G" in stream
    # Should be at start (roughly)
    assert stream.strip().startswith(b"0.5 G")


def test_inject_tail_basic(pdf):
    """Test injecting code at the tail."""
    inject_args = ["tail", "Q"]

    inject_pdf(pdf, inject_args)

    stream = _read_page_content(pdf.pages[0])
    assert stream.strip().endswith(b"Q")


def test_inject_specific_page(pdf):
    """Test injecting only on specific page."""
    pdf.add_blank_page()  # Page 2
    # Ensure Page 2 also has a stream
    pdf.pages[1].Contents = pdf.make_stream(b"1 g ")

    inject_args = ["2", "head", "1 0 0 RG"]

    inject_pdf(pdf, inject_args)

    # Page 1 should NOT have it
    stream1 = _read_page_content(pdf.pages[0])
    assert b"1 0 0 RG" not in stream1

    # Page 2 SHOULD have it
    stream2 = _read_page_content(pdf.pages[1])
    assert b"1 0 0 RG" in stream2


def test_inject_invalid_args(pdf):
    """Test error when head/tail is missing."""
    inject_args = ["0.5 G"]

    with pytest.raises(ValueError, match="Did you forget"):
        inject_pdf(pdf, inject_args)
