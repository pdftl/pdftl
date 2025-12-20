import logging
from unittest.mock import patch

import pikepdf
import pytest

from pdftl.commands.modify_annots import modify_annots


@pytest.fixture
def pdf():
    p = pikepdf.new()
    p.add_blank_page()
    annot = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Link, Rect=[0, 0, 10, 10]
    )
    p.pages[0].Annots = p.make_indirect([annot])
    return p


def test_modify_annots_array_mixed_types(pdf):
    """Test parsing array with numbers and strings."""
    spec = "1/Link(Border=[1.5 solid])"
    modify_annots(pdf, [spec])

    annot = pdf.pages[0].Annots[0]
    assert annot.Border[0] == 1.5
    assert str(annot.Border[1]) == "solid"


def test_modify_annots_mismatched_parens(pdf, caplog):
    """Test ValueError for mismatched parentheses is caught and logged."""
    # Ensure we capture WARNING logs
    caplog.set_level(logging.WARNING)

    # Passing a string with unbalanced parens inside the value part
    # The code catches ValueError and logs "Skipping invalid value..."
    spec = "1/Link(T=(Unbalanced)"

    modify_annots(pdf, [spec])

    # We verify the warning instead of expecting a crash
    assert "Skipping invalid value" in caplog.text


def test_modify_annots_name_value(pdf):
    """Test parsing a Name value."""
    spec = "1/Link(MyName=/Foo)"
    modify_annots(pdf, [spec])

    annot = pdf.pages[0].Annots[0]
    assert annot.MyName == "/Foo"
    assert isinstance(annot.MyName, pikepdf.Name)


def test_modify_annots_empty_rules_warning(pdf, caplog):
    """Test warning when parser returns no rules."""
    # Ensure we capture WARNING logs
    caplog.set_level(logging.WARNING)

    with patch("pdftl.commands.modify_annots.specs_to_modification_rules", return_value=[]):
        modify_annots(pdf, ["1/Link(A=B)"])

    assert "No modification rules parsed" in caplog.text
