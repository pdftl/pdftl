import pikepdf
import pytest

from pdftl.commands.replace import replace_in_content_streams
from pdftl.exceptions import InvalidArgumentError


@pytest.fixture
def pdf_with_content():
    """Creates a PDF where page 1 has specific content stream text."""
    pdf = pikepdf.new()
    pdf.add_blank_page()

    # Set content stream to something predictable: "BT ... (Hello World) Tj ... ET"
    # We use a simple stream that survives normalization easily
    stream_data = b"BT /F1 12 Tf 100 100 Td (Hello World) Tj ET"
    pdf.pages[0].Contents = pdf.make_stream(stream_data)

    return pdf


def test_replace_basic(pdf_with_content):
    """Test simple string replacement on a specific page."""
    # Spec: 1/Hello/Hola/ -> Replace 'Hello' with 'Hola' on page 1
    # The last char '/' is the delimiter
    specs = ["1/Hello/Hola/"]

    replace_in_content_streams(pdf_with_content, specs)

    content = pdf_with_content.pages[0].Contents.read_bytes()
    assert b"(Hola World) Tj" in content


def test_replace_global_implicit(pdf_with_content):
    """Test replacement without page range (implies all pages)."""
    # Spec: /World/Earth/ -> Page range part is empty string (defaults to all)
    specs = ["/World/Earth/"]

    replace_in_content_streams(pdf_with_content, specs)

    content = pdf_with_content.pages[0].Contents.read_bytes()
    assert b"(Hello Earth) Tj" in content


def test_replace_with_count(pdf_with_content):
    """Test the count limit (trailing integer in spec)."""
    # Setup content with two occurrences
    stream_data = b"(Apple) Tj (Apple) Tj"
    pdf_with_content.pages[0].Contents = pdf_with_content.make_stream(stream_data)

    # Spec: /Apple/Banana/1 -> Count=1
    specs = ["/Apple/Banana/1"]

    replace_in_content_streams(pdf_with_content, specs)

    content = pdf_with_content.pages[0].Contents.read_bytes()
    # Expect one Banana, one Apple left
    assert content.count(b"Banana") == 1
    assert content.count(b"Apple") == 1


def test_replace_alternate_delimiter(pdf_with_content):
    """Test using a non-standard delimiter (determined by last char)."""
    # Spec: 1#Hello#Hola# -> Delimiter is '#'
    specs = ["1#Hello#Hola#"]

    replace_in_content_streams(pdf_with_content, specs)

    content = pdf_with_content.pages[0].Contents.read_bytes()
    assert b"(Hola World)" in content


def test_replace_regex_behavior(pdf_with_content):
    """Test that regex patterns work."""
    # Pattern: (H...o) matches Hello
    # Spec: /H...o/Hi/
    specs = ["/H...o/Hi/"]

    replace_in_content_streams(pdf_with_content, specs)

    content = pdf_with_content.pages[0].Contents.read_bytes()
    assert b"(Hi World)" in content


def test_replace_invalid_spec(pdf_with_content):
    """Test that malformed specs raise InvalidArgumentError."""
    # Missing delimiter at end implies incomplete parts
    # /foo/bar -> splits to ['', 'foo', 'bar'] (len=3), expects 4
    specs = ["/foo/bar"]

    with pytest.raises(InvalidArgumentError, match="expected 4 parts"):
        replace_in_content_streams(pdf_with_content, specs)


def test_replace_no_normalization(pdf_with_content):
    """Test disabling normalization."""
    # If we disable normalization, the replacement should still happen
    # if the raw bytes match.
    specs = ["/Hello/Hola/"]
    replace_in_content_streams(
        pdf_with_content, specs, normalize_input=False, normalize_output=False
    )

    content = pdf_with_content.pages[0].Contents.read_bytes()
    assert b"(Hola World)" in content
