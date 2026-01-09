import logging
from unittest.mock import patch

import pikepdf
import pytest

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.add_text import add_text_pdf

from .sandbox import ModuleSandboxMixin


@pytest.fixture
def pdf():
    p = pikepdf.new()
    p.add_blank_page()  # Page 1
    p.add_blank_page()  # Page 2
    return p


class TestAddTextCoverage(ModuleSandboxMixin):
    def test_add_text_parser_error(self, pdf):
        """Test wrapping of parser ValueError."""
        with patch(
            "pdftl.operations.parsers.add_text_parser.parse_add_text_specs_to_rules"
        ) as mock_parse:
            mock_parse.side_effect = ValueError("Bad syntax")

            with pytest.raises(InvalidArgumentError, match="Error in add_text spec"):
                add_text_pdf(pdf, ["bad-spec"])

    def test_add_text_skip_page(self, pdf):
        """Test that pages with no rules are skipped."""
        spec = "1/Hello/"

        import pdftl.operations.helpers.text_drawer as drawer_module

        with patch.object(drawer_module, "TextDrawer") as MockDrawer:
            add_text_pdf(pdf, [spec])
            # Instantiated once for dependency check, once for Page 1.
            # Should NOT be instantiated for Page 2.
            assert MockDrawer.call_count == 2

    def test_add_text_overlay_exception(self, pdf, caplog):
        """Test handling exception during overlay application."""
        # Ensure we capture WARNING logs
        caplog.set_level(logging.WARNING)

        spec = "1/Hello/"

        with patch("pdftl.operations.helpers.text_drawer.TextDrawer") as MockDrawer:
            instance = MockDrawer.return_value
            instance.save.return_value = b"%PDF-1.0 dummy"

            # Make Pdf.open raise exception immediately to simulate corrupt overlay or IO error
            with patch("pikepdf.Pdf.open") as MockPdfOpen:
                MockPdfOpen.side_effect = pikepdf.PdfError("Corrupt overlay")

                add_text_pdf(pdf, [spec])

        assert "Failed to apply overlay" in caplog.text


from unittest.mock import MagicMock

import pytest

from pdftl.operations.add_text import _process_page


def test_process_page_empty_overlay_log():
    """Triggers line 340: Overlay PDF exists but has no pages."""
    mock_page = MagicMock()
    mock_page.trimbox = [0, 0, 100, 100]

    mock_drawer_instance = MagicMock()
    # Provide a valid-looking PDF header but no actual page objects
    mock_drawer_instance.save.return_value = b"%PDF-1.7\n%%EOF"
    mock_drawer_class = MagicMock(return_value=mock_drawer_instance)

    with patch("pikepdf.Pdf.open") as mock_pdf_open:
        # Mock a PDF object that has 0 pages
        mock_pdf_open.return_value.__enter__.return_value.pages = []

        _process_page(0, mock_page, {0: [MagicMock()]}, {}, mock_drawer_class)
        # Line 340 is now hit (logger.debug for empty overlay)


import pdftl.core.constants as c


def test_process_page_with_source_meta():
    mock_page = MagicMock()
    mock_page.trimbox = [0, 0, 100, 100]

    # Ensure the attribute name matches exactly what the code looks for
    source_data = {"/source_filename": "old.pdf", "/source_page": 5}
    setattr(mock_page, c.PDFTL_SOURCE_INFO_KEY, source_data)

    mock_drawer_instance = MagicMock()
    mock_drawer_instance.save.return_value = b"some_pdf_bytes"
    mock_drawer_class = MagicMock(return_value=mock_drawer_instance)

    mock_rule = MagicMock()
    # Pass a dummy static_context to avoid fallthrough issues
    _process_page(0, mock_page, {0: [mock_rule]}, {"filename": "new.pdf"}, mock_drawer_class)

    args, _ = mock_drawer_instance.draw_rule.call_args
    assert args[1]["source_filename"] == "old.pdf"
