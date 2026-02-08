import pikepdf
import pytest
from pikepdf import Array, Matrix

from pdftl.utils.superimpose import superimpose_page


def get_page_content_bytes(page) -> bytes:
    """Manually resolves the .Contents object into bytes."""
    contents = page.Contents
    if isinstance(contents, Array):
        # Concatenate all streams in the array
        return b"".join(bytes(s) for s in contents)
    # It's a single Stream
    return bytes(contents)


@pytest.fixture
def temp_pdfs():
    with pikepdf.new() as target_pdf, pikepdf.new() as source_pdf:
        target_pdf.add_blank_page(page_size=(612, 792))
        source_pdf.add_blank_page(page_size=(100, 100))
        yield target_pdf.pages[0], source_pdf.pages[0]


def test_superimpose_matrix_application(temp_pdfs):
    target, source = temp_pdfs
    matrix = Matrix(1, 0, 0, 1, 50, 100)

    superimpose_page(target, source, matrix)

    contents = get_page_content_bytes(target)

    # Assertions on the raw stream data
    assert b"50 100 cm" in contents or b"50.0 100.0 cm" in contents
    assert contents.startswith(b"q")
    assert contents.endswith(b"Q")


def test_multiple_overlays(temp_pdfs):
    target, source = temp_pdfs

    superimpose_page(target, source, Matrix(1, 0, 0, 1, 0, 0))
    superimpose_page(target, source, Matrix(1, 0, 0, 1, 10, 10))

    # Resource check
    assert len(target.Resources.XObject) == 2

    contents = get_page_content_bytes(target)
    # Verify we see two 'Do' commands in the combined streams
    assert contents.count(b"Do") == 2
