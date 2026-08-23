# tests/operations/test_dump_text.py

import sys
from unittest.mock import MagicMock, patch

import pikepdf
import pytest
import io

import pdftl.operations.dump_text
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.dump_text import dump_text


@pytest.fixture
def mock_pdfium():
    mock_spec = MagicMock()
    mock_module = MagicMock(__spec__=mock_spec)
    return mock_module


def test_dump_text_missing_dependency(mock_pdfium):
    """Test missing dependency error."""
    # dump_text() calls ensure_dependencies() and imports pypdfium2 fresh on
    # every invocation, so patching sys.modules is enough - no reload needed.
    with patch.dict(sys.modules, {"pypdfium2": None}):
        with pytest.raises(InvalidArgumentError, match="requires pypdfium2"):
            pdftl.operations.dump_text.dump_text("dummy.pdf", "passwd123")


def test_dump_text_password_none(mock_pdfium):
    """Test None password handling."""
    with (
        patch.dict(sys.modules, {"pypdfium2": mock_pdfium}),
        patch(
            "pdftl.operations.dump_text._extract_text_from_pdf", return_value=[]
        ) as mock_extract,
    ):
        pdftl.operations.dump_text.dump_text("dummy.pdf", None)
        # Verify it was called (implies None check passed)
        mock_extract.assert_called_once()


def test_dump_text_real_iteration(two_page_pdf, mock_pdfium):
    """Test iteration logic using mocks."""
    in_pdf = pikepdf.open(two_page_pdf)
    mock_page = MagicMock()
    mock_page.get_textpage.return_value.get_text_range.return_value = "Text"

    mock_pdf = MagicMock()
    mock_pdf.__len__.return_value = 1
    mock_pdf.__iter__.return_value = iter([mock_page])

    with patch.dict(sys.modules, {"pypdfium2": mock_pdfium}):
        with patch("pypdfium2.PdfDocument") as MockDoc:
            MockDoc.return_value.__enter__.return_value = mock_pdf

            result = pdftl.operations.dump_text.dump_text(in_pdf, "pass")
            assert result.success is True
            assert "Text" in result.data


def test_dump_text_patches_type3_font_before_calling_pdfium(mock_pdfium):
    """The Type3 /ToUnicode patching must happen on the in-memory pike
    copy before pdfium ever sees the buffer, and pdfium's own extracted
    text is what's returned (no separate content-stream fallback path
    exists any more)."""
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Subtype=pikepdf.Name("/Type3"),
            Encoding=pikepdf.Dictionary(Differences=[65, pikepdf.Name("/A")]),
        )
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))

    mock_page = MagicMock()
    mock_page.get_textpage.return_value.get_text_range.return_value = "Text"
    mock_pdfium_pdf = MagicMock()
    mock_pdfium_pdf.__len__.return_value = 1
    mock_pdfium_pdf.__iter__.return_value = iter([mock_page])

    with patch.dict(sys.modules, {"pypdfium2": mock_pdfium}):
        with patch("pypdfium2.PdfDocument") as MockDoc:
            MockDoc.return_value.__enter__.return_value = mock_pdfium_pdf
            result = pdftl.operations.dump_text.dump_text(pdf, "pass")

    assert result.success is True
    assert "Text" in result.data
    # The Type3 font on the caller's own pike object was patched in place.
    assert "/ToUnicode" in font


def test_extract_text_from_pdf_no_type3_fonts_does_not_patch(mock_pdfium):
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Subtype=pikepdf.Name("/TrueType"), Encoding=pikepdf.Name("/WinAnsiEncoding")
        )
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))

    mock_page = MagicMock()
    mock_page.get_textpage.return_value.get_text_range.return_value = "Hello"
    mock_pdfium_pdf = MagicMock()
    mock_pdfium_pdf.__len__.return_value = 1
    mock_pdfium_pdf.__iter__.return_value = iter([mock_page])

    with patch.object(mock_pdfium, "PdfDocument") as MockDoc:
        MockDoc.return_value.__enter__.return_value = mock_pdfium_pdf
        texts = pdftl.operations.dump_text._extract_text_from_pdf(pdf, mock_pdfium, "")

    assert texts == ["Hello"]
    assert "/ToUnicode" not in font


def test_dump_text_type3_synthesis_end_to_end():
    """Validates unmocked text extraction from a Type 3 PDF missing /ToUnicode,
    verifying that synthesized CMaps are correctly interpreted by pdfium."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))

    # d1 sets glyph width (1000, 0) and bbox [0, 0, 1000, 1000]
    # '0 0 1000 1000 re f' draws geometry so PDFium computes a valid glyph box
    char_proc_stream = pdf.make_stream(b"1000 0 0 0 1000 1000 d1 0 0 1000 1000 re f")

    type3_font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name("/Font"),
            Subtype=pikepdf.Name("/Type3"),
            FontBBox=[0, 0, 1000, 1000],
            FontMatrix=[0.001, 0, 0, 0.001, 0, 0],
            CharProcs=pikepdf.Dictionary(A=char_proc_stream),
            Encoding=pikepdf.Dictionary(Differences=[65, pikepdf.Name("/A")]),
            FirstChar=65,
            LastChar=65,
            Widths=[1000],
        )
    )

    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=type3_font))
    page.Contents = pdf.make_stream(b"BT /F1 12 Tf 20 50 Td (A) Tj ET")

    pdf_bytes = io.BytesIO()
    pdf.save(pdf_bytes)
    pdf_bytes.seek(0)

    with pikepdf.open(pdf_bytes) as pdf_pike:
        result = dump_text(pdf_pike, None)

    assert "A" in result.data
