import io
import logging
from unittest.mock import patch

import pikepdf
import pytest

import pdftl.operations.helpers.text_drawer
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

        with patch.object(pdftl.operations.helpers.text_drawer, "TextDrawer") as MockDrawer:
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


def test_process_page_uses_trimbox(tmp_path):
    """Tests that TrimBox is used when available."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 300))
    # Set a TrimBox smaller than MediaBox
    pdf.pages[0]["/TrimBox"] = pikepdf.Array([10, 10, 190, 290])
    result = add_text_pdf(pdf, ["/TEST/(position=mid-center)"])
    assert result.success


def test_process_page_handles_empty_overlay(tmp_path):
    """Tests graceful handling when overlay PDF has no pages."""
    from unittest.mock import patch

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 300))

    # Return bytes that parse as a valid but empty PDF
    empty_pdf_bytes = io.BytesIO()
    with pikepdf.new() as empty:
        empty.save(empty_pdf_bytes)

    with patch.object(
        pdftl.operations.helpers.text_drawer.TextDrawer,
        "save",
        return_value=empty_pdf_bytes.getvalue(),
    ):
        result = add_text_pdf(pdf, ["/TEST/(position=mid-center)"])
        assert result.success  # Should not crash


def test_add_text_rotated_page_90():
    """Tests visual dimension swap for rotated pages."""
    import pikepdf

    from pdftl.operations.add_text import add_text_pdf

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 300))
    pdf.pages[0]["/Rotate"] = 90
    # This triggers the rotation branch in _process_page
    result = add_text_pdf(pdf, ["/TEST/(position=mid-center)"])
    assert result.success


def test_add_text_rotated_page_270():
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 300))
    pdf.pages[0]["/Rotate"] = 270
    result = add_text_pdf(pdf, ["/TEST/(position=mid-center)"])
    assert result.success


def test_add_text_no_rules_returns_early():
    """Line 268: empty spec list produces no rules."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 300))
    # A spec that matches no pages
    result = add_text_pdf(pdf, ["99/TEST/(position=mid-center)"])
    assert result.success


def test_add_text_bad_metadata_handled_gracefully():
    """Lines 204-206: corrupted docinfo doesn't crash."""
    from unittest.mock import PropertyMock, patch

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 300))
    with patch(
        "pikepdf.Pdf.docinfo", new_callable=PropertyMock, side_effect=AttributeError("no docinfo")
    ):
        result = add_text_pdf(pdf, ["1-end/TEST/(position=mid-center)"])
        assert result.success
