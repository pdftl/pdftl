import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest

from pdftl.exceptions import InvalidArgumentError


@pytest.fixture
def mock_pdfium():
    mock_spec = MagicMock()
    mock_module = MagicMock(__spec__=mock_spec)
    return mock_module


def test_dump_text_missing_dependency(mock_pdfium):
    """Test missing dependency error."""
    with patch.dict(sys.modules, {"pypdfium2": None}):
        import pdftl.operations.dump_text

        importlib.reload(pdftl.operations.dump_text)

        with pytest.raises(InvalidArgumentError, match="requires pypdfium2"):
            pdftl.operations.dump_text.dump_text("dummy.pdf", "passwd123")


def test_dump_text_password_none(mock_pdfium):
    """Test None password handling."""
    # Reload with mock success
    with patch.dict(sys.modules, {"pypdfium2": mock_pdfium}):
        import pdftl.operations.dump_text

        importlib.reload(pdftl.operations.dump_text)

        with patch(
            "pdftl.operations.dump_text._extract_text_from_pdf", return_value=[]
        ) as mock_extract:
            pdftl.operations.dump_text.dump_text("dummy.pdf", None)
            # Verify it was called (implies None check passed)
            mock_extract.assert_called_once()


def test_dump_text_real_iteration(two_page_pdf, mock_pdfium):
    """Test iteration logic using mocks."""
    import pikepdf

    in_pdf = pikepdf.open(two_page_pdf)
    mock_page = MagicMock()
    mock_page.get_textpage.return_value.get_text_range.return_value = "Text"

    mock_pdf = MagicMock()
    mock_pdf.__len__.return_value = 1
    mock_pdf.__iter__.return_value = iter([mock_page])

    with patch.dict(sys.modules, {"pypdfium2": mock_pdfium}):
        import pdftl.operations.dump_text

        importlib.reload(pdftl.operations.dump_text)

        with patch("pypdfium2.PdfDocument") as MockDoc:
            MockDoc.return_value.__enter__.return_value = mock_pdf

            result = pdftl.operations.dump_text.dump_text(in_pdf, "pass")
            assert result.success is True
            assert "Text" in result.data
