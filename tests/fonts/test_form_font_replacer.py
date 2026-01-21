import io
from typing import List, cast
from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.fonts.form_font_replacer import (
    _patch_da_content,
    _update_field_font_recursive,
    replace_form_fonts,
)

## --- 1. Unit Tests for Logic Helpers ---


@pytest.mark.parametrize(
    "original, expected",
    [
        (b"/Helv 12 Tf 0 g", b"/PdftlReplacementFont 12 Tf 0 g"),
        (b"/Times-Roman 8.5 Tf", b"/PdftlReplacementFont 8.5 Tf"),
        (b"0.5 g /Courier 10 Tf", b"0.5 g /PdftlReplacementFont 10 Tf"),
        (b"NoFontHere", b"NoFontHere"),
    ],
)
def test_patch_da_content(original, expected):
    """Verify regex correctly replaces font name while preserving size."""
    alias = "PdftlReplacementFont"
    assert _patch_da_content(original, alias) == expected


## --- 2. Integration Tests with pikepdf Objects ---


@pytest.fixture
def mock_pdf():
    # We use a real pikepdf object but mock the Root to avoid needing a real file
    pdf = MagicMock(spec=pikepdf.Pdf)
    pdf.Root = pikepdf.Dictionary()
    return pdf


def test_replace_form_fonts_no_acroform(mock_pdf):
    """Ensure it returns early if no AcroForm exists."""
    # Root is empty, no AcroForm
    replace_form_fonts(mock_pdf, "dummy.ttf")
    assert not hasattr(mock_pdf.Root, "AcroForm")


@patch("pdftl.fonts.form_font_replacer.embed_truetype_font")
@patch("pdftl.fonts.form_font_replacer.ensure_dependencies")
def test_replace_form_fonts_full_flow(mock_ensure, mock_embed, mock_pdf):
    """Test the complete replacement logic on a structured AcroForm."""
    mock_embed.return_value = pikepdf.Dictionary()

    # Create real pikepdf dictionaries so bytes() and indexing work
    acroform = pikepdf.Dictionary(DA=pikepdf.String("/OldFont 10 Tf"), Fields=pikepdf.Array())
    mock_pdf.Root.AcroForm = acroform

    text_field = pikepdf.Dictionary(FT=pikepdf.Name("/Tx"), DA=pikepdf.String("/OldFont 10 Tf"))
    acroform.Fields.append(text_field)

    replace_form_fonts(mock_pdf, "font.ttf")

    # Check global DA
    assert b"PdftlReplacementFont" in bytes(acroform.DA)
    # Check field DA
    assert b"PdftlReplacementFont" in bytes(text_field.DA)
    # Check registration in DR
    assert "/PdftlReplacementFont" in acroform.DR.Font
    assert acroform.NeedAppearances is True


## --- 3. Recursive Traversal Tests ---


def test_recursive_field_update():
    """Verify font replacement reaches nested 'Kids'."""
    alias = "NewFont"

    child = pikepdf.Dictionary(FT=pikepdf.Name("/Tx"))
    parent = pikepdf.Dictionary(T="RadioGroup", Kids=pikepdf.Array([child]))

    _update_field_font_recursive(parent, alias)

    # Child should have received the default DA
    assert b"/NewFont 12 Tf 0 g" in bytes(child.DA)


def test_update_field_skips_non_text_fields():
    """Ensure it doesn't force /DA onto fields where it's not valid (e.g. Buttons)."""
    alias = "NewFont"
    btn_field = pikepdf.Dictionary(FT=pikepdf.Name("/Btn"))

    _update_field_font_recursive(btn_field, alias)

    assert "/DA" not in btn_field


@patch("pdftl.fonts.form_font_replacer.embed_truetype_font")
@patch("pdftl.fonts.form_font_replacer.ensure_dependencies")
def test_replace_form_fonts_initializes_missing_dr(mock_ensure, mock_embed, mock_pdf):
    """
    Covers lines 43-46: Verify that /DR and /Font dictionaries
    are created if they don't exist in AcroForm.
    """
    mock_embed.return_value = pikepdf.Dictionary()

    # Setup AcroForm WITHOUT /DR
    acroform = pikepdf.Dictionary()
    mock_pdf.Root.AcroForm = acroform

    replace_form_fonts(mock_pdf, "font.ttf")

    # Assertions
    assert "/DR" in acroform
    assert "/Font" in acroform.DR
    assert "/PdftlReplacementFont" in acroform.DR.Font
