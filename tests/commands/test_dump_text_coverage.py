import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest

from pdftl.exceptions import InvalidArgumentError


def test_dump_text_missing_dependency():
    """Test missing dependency error."""
    with patch.dict(sys.modules, {"pypdfium2": None}):
        import pdftl.commands.dump_text

        importlib.reload(pdftl.commands.dump_text)

        # We must invoke the helper that checks the flag
        from pdftl.commands.dump_text import dump_text

        with pytest.raises(InvalidArgumentError, match="requires the 'pypdfium2' library"):
            dump_text("dummy.pdf", "passwd123")


def test_dump_text_password_none():
    """Test None password handling."""
    # Reload with mock success
    with patch.dict(sys.modules, {"pypdfium2": MagicMock()}):
        import pdftl.commands.dump_text

        importlib.reload(pdftl.commands.dump_text)

        with patch(
            "pdftl.commands.dump_text._extract_text_from_pdf", return_value=[]
        ) as mock_extract:
            pdftl.commands.dump_text.dump_text("dummy.pdf", None)
            # Verify it was called (implies None check passed)
            mock_extract.assert_called_once()


def test_dump_text_real_iteration():
    """Test iteration logic using mocks."""
    mock_page = MagicMock()
    mock_page.get_textpage.return_value.get_text_range.return_value = "Text"

    mock_pdf = MagicMock()
    mock_pdf.__len__.return_value = 1
    mock_pdf.__iter__.return_value = iter([mock_page])

    with patch.dict(sys.modules, {"pypdfium2": MagicMock()}):
        import pdftl.commands.dump_text

        importlib.reload(pdftl.commands.dump_text)

        with patch("pypdfium2.PdfDocument") as MockDoc:
            MockDoc.return_value.__enter__.return_value = mock_pdf

            with patch("pdftl.commands.dump_text.dump") as mock_dump:
                pdftl.commands.dump_text.dump_text("dummy.pdf", "pass")
                assert "Text" in mock_dump.call_args[0][0]
                mock_page.close.assert_called_once()
