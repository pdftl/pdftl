import logging
from unittest.mock import patch

import pikepdf
import pytest

from pdftl.commands.add_text import add_text_pdf
from pdftl.exceptions import InvalidArgumentError

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
            "pdftl.commands.parsers.add_text_parser.parse_add_text_specs_to_rules"
        ) as mock_parse:
            mock_parse.side_effect = ValueError("Bad syntax")

            with pytest.raises(InvalidArgumentError, match="Error in add_text spec"):
                add_text_pdf(pdf, ["bad-spec"])

    def test_add_text_skip_page(self, pdf):
        """Test that pages with no rules are skipped."""
        spec = "1/Hello/"

        import pdftl.commands.helpers.text_drawer as drawer_module

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

        with patch("pdftl.commands.helpers.text_drawer.TextDrawer") as MockDrawer:
            instance = MockDrawer.return_value
            instance.save.return_value = b"%PDF-1.0 dummy"

            # Make Pdf.open raise exception immediately to simulate corrupt overlay or IO error
            with patch("pikepdf.Pdf.open") as MockPdfOpen:
                MockPdfOpen.side_effect = pikepdf.PdfError("Corrupt overlay")

                add_text_pdf(pdf, [spec])

        assert "Failed to apply overlay" in caplog.text
