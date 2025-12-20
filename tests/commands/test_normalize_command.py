import pytest
from pikepdf import Array, Pdf

from pdftl.commands.normalize import normalize_content_streams
from pdftl.exceptions import InvalidArgumentError


@pytest.fixture
def test_pdf():
    """A simple PDF structure with multiple pages."""
    pdf = Pdf.new()
    pdf.pages.append(pdf.add_blank_page(page_size=(200, 300)))
    pdf.pages.append(pdf.add_blank_page(page_size=(300, 400)))
    pdf.pages.append(pdf.add_blank_page(page_size=(500, 600)))
    pdf.pages.append(pdf.add_blank_page(page_size=(700, 800)))
    test_stream = b"10 w 306 396\n m 306 594 l S (Hello) Tj"
    pdf.pages[2].Contents = Array([pdf.make_stream(test_stream)])
    pdf.pages[3].Contents = pdf.make_stream(test_stream)
    return pdf


def test_normalize_pdf_90_degrees(test_pdf):
    """Test that pages rotate correctly by 90 degrees."""
    specs = ["2-3"]  # Rotate all pages 90 degrees clockwise
    result = normalize_content_streams(test_pdf, specs)
    # breakpoint()
    assert result.pages[0].Contents.read_bytes() == b""
    assert result.pages[1].Contents.read_bytes() == b""
    assert result.pages[2].Contents.read_bytes() == b"\n".join(
        [b"10 w", b"306 396 m", b"306 594 l", b"S", b"(Hello) Tj"]
    )
    assert result.pages[3].Contents.read_bytes() == b"10 w 306 396\n m 306 594 l S (Hello) Tj"


def test_rotate_pdf_invalid_spec(test_pdf):
    """Test handling of an invalid rotation spec."""
    specs = ["invalid_spec"]
    with pytest.raises(InvalidArgumentError):
        normalize_content_streams(test_pdf, specs)
