from unittest.mock import MagicMock, patch

import pytest

from pdftl.utils.page_images import iter_pages_as_pil, render_page_to_pil


@pytest.fixture
def mock_pypdfium2():
    mock_pdfium = MagicMock()
    mock_doc = MagicMock()
    mock_pdfium.PdfDocument.return_value = mock_doc

    # Create 3 mock pages
    mock_pages = [MagicMock(), MagicMock(), MagicMock()]

    # Make the document iterable and indexable
    mock_doc.__iter__.return_value = iter(mock_pages)
    mock_doc.__getitem__.side_effect = lambda idx: mock_pages[idx]

    # Setup the render pipeline: page.render() -> bitmap.to_pil() -> "PIL_IMAGE_X"
    for i, page in enumerate(mock_pages):
        mock_bitmap = MagicMock()
        mock_bitmap.to_pil.return_value = f"PIL_IMAGE_{i}"
        page.render.return_value = mock_bitmap

    # Patch sys.modules to safely intercept 'import pypdfium2' inside functions
    with patch.dict("sys.modules", {"pypdfium2": mock_pdfium}):
        yield mock_pdfium, mock_doc


@patch("pdftl.utils.dependencies.ensure_dependencies")
def test_render_page_to_pil(mock_deps, mock_pypdfium2):
    mock_pdfium, mock_doc = mock_pypdfium2

    mock_pdf = MagicMock()
    mock_pdf.save = MagicMock()

    img = render_page_to_pil(mock_pdf, page_index=1, dpi=144.0)

    # Verify dependencies checked
    mock_deps.assert_called_once_with("page_images", ["pypdfium2", "PIL"], "render")

    # Verify pikepdf serialisation
    assert mock_pdf.save.called

    # Verify pypdfium2 execution
    mock_pdfium.PdfDocument.assert_called_once()

    # Scale should be dpi / 72.0 = 2.0
    mock_doc[1].render.assert_called_once_with(scale=2.0)

    # Should return what our mock bitmap.to_pil() returned
    assert img == "PIL_IMAGE_1"

    # Verify cleanup occurred
    assert mock_doc.close.called


@patch("pdftl.utils.dependencies.ensure_dependencies")
def test_iter_pages_as_pil_all(mock_deps, mock_pypdfium2):
    mock_pdfium, mock_doc = mock_pypdfium2
    mock_pdf = MagicMock()

    # Iterate without specifying page indices (should yield all 3)
    results = list(iter_pages_as_pil(mock_pdf, dpi=72.0))

    assert len(results) == 3
    assert results[0] == (0, "PIL_IMAGE_0")
    assert results[1] == (1, "PIL_IMAGE_1")
    assert results[2] == (2, "PIL_IMAGE_2")

    assert mock_doc.close.called


@patch("pdftl.utils.dependencies.ensure_dependencies")
def test_iter_pages_as_pil_with_indices(mock_deps, mock_pypdfium2):
    mock_pdfium, mock_doc = mock_pypdfium2
    mock_pdf = MagicMock()

    # Iterate with specific page indices (should skip page 1)
    results = list(iter_pages_as_pil(mock_pdf, dpi=72.0, page_indices={0, 2}))

    assert len(results) == 2
    assert results[0] == (0, "PIL_IMAGE_0")
    assert results[1] == (2, "PIL_IMAGE_2")

    assert mock_doc.close.called


@patch("pdftl.utils.dependencies.ensure_dependencies")
def test_iter_pages_as_pil_cleanup_on_error(mock_deps, mock_pypdfium2):
    mock_pdfium, mock_doc = mock_pypdfium2
    mock_pdf = MagicMock()

    # Inject an exception into PdfDocument instantiation
    mock_pdfium.PdfDocument.side_effect = Exception("Crash!")

    # Try iterating, expect exception
    generator = iter_pages_as_pil(mock_pdf, dpi=72.0)

    with pytest.raises(Exception, match="Crash!"):
        next(generator)

    assert not mock_doc.close.called
