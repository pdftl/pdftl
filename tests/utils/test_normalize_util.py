import pikepdf
import pytest
from pikepdf import Name

# --- Import the functions to test ---
from pdftl.utils.normalize import (
    get_normalized_page_content_stream,
    normalize_page_content_stream,
)

# A "messy" content stream with multiple operators per line
MESSY_STREAM_BYTES = b"q 1 0 0 1 10 10 cm BT /F1 12 Tf (Hello)Tj ET Q"

# The expected "clean" output after normalization by pikepdf.
# pikepdf.unparse_content_stream puts each operator on a new line.
CLEAN_STREAM_BYTES = b"q\n1 0 0 1 10 10 cm\nBT\n/F1 12 Tf\n(Hello) Tj\nET\nQ"


@pytest.fixture
def messy_pdf_setup():
    """
    Creates a PDF with one page and a "messy" content stream.
    Yields the pdf, page, messy bytes, and expected clean string.
    """
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()
    page = pdf.pages[0]

    # Add a minimal Font resource, otherwise the /F1 Tf operator is invalid
    # and pikepdf.parse_content_stream might fail or discard it.
    font_dict = pikepdf.Dictionary(
        {
            "/Type": Name.Font,
            "/Subtype": Name.Type1,
            "/BaseFont": Name.Helvetica,
        }
    )
    font_ref = pdf.make_indirect(font_dict)
    page.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font_ref})})

    # Set the page's content to our messy stream
    page.Contents = pdf.make_stream(MESSY_STREAM_BYTES)

    # Yield the necessary components to the tests
    yield pdf, page, MESSY_STREAM_BYTES, CLEAN_STREAM_BYTES

    # Clean up after the test
    pdf.close()


def test_get_normalized_page_content_stream(messy_pdf_setup):
    """
    Tests that get_normalized_page_content_stream returns the
    correctly formatted "clean" string.
    """
    # Arrange
    pdf, page, _messy, clean_bytes = messy_pdf_setup

    # Act
    normalized_bytes = get_normalized_page_content_stream(page)

    # Assert
    assert normalized_bytes == clean_bytes


def test_normalize_page_content_stream(messy_pdf_setup):
    """
    Tests that normalize_page_content_stream correctly replaces
    the page's content stream with a new, normalized stream.
    """
    # Arrange
    pdf, page, messy_bytes, clean_bytes = messy_pdf_setup
    original_stream_obj = page.Contents
    original_stream_bytes = original_stream_obj.read_raw_bytes()

    # Act
    normalize_page_content_stream(pdf, page)

    # Assert
    new_stream_obj = page.Contents
    new_stream_bytes = new_stream_obj.read_raw_bytes()

    # 1. Check that the stream object itself is new
    assert new_stream_obj.objgen != original_stream_obj.objgen

    # 2. Check that the original content was indeed the messy one
    assert original_stream_bytes == messy_bytes

    # 3. Check that the new content is the clean, normalized version
    assert new_stream_bytes == clean_bytes
