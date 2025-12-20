import logging
from unittest.mock import patch

import pikepdf
import pytest

from pdftl.commands.dump_annots import dump_annots, dump_data_annots


@pytest.fixture
def annot_pdf():
    """Creates a PDF with various annotations for testing."""
    pdf = pikepdf.new()
    pdf.add_blank_page()

    # 1. Root URI Base
    pdf.Root.URI = pikepdf.Dictionary(Base=pikepdf.String("http://example.com/"))

    # 2. Link Annotation with URI Action
    link_annot = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Link,
        Rect=[0, 0, 100, 100],
        A=pikepdf.Dictionary(S=pikepdf.Name.URI, URI=pikepdf.String("page1.html")),
    )

    # 3. Popup Annotation
    popup_annot = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Popup,
        Rect=[100, 100, 200, 200],
        Open=True,
    )

    # 4. Line Annotation (triggers exclusion in pdftk-style dump)
    line_annot = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Line,
        Rect=[50, 50, 150, 150],
        L=[50, 50, 150, 150],
    )

    pdf.pages[0].Annots = pdf.make_indirect([link_annot, popup_annot, line_annot])
    return pdf


def test_dump_data_annots_pdftk_style(annot_pdf, capsys):
    """Test the pdftk-style output (key: value pairs)."""
    dump_data_annots(annot_pdf, output_file=None)
    out = capsys.readouterr().out

    assert "PdfUriBase: http://example.com/" in out
    assert "AnnotSubtype: Link" in out
    assert "AnnotActionURI: page1.html" in out
    assert "AnnotSubtype: Popup" in out
    assert "AnnotSubtype: Line" not in out


def test_dump_annots_json(annot_pdf, capsys):
    """Test the JSON dump output."""
    dump_annots(annot_pdf, output_file=None)
    out = capsys.readouterr().out

    assert '"/Subtype": "/Line"' in out
    assert '"/Subtype": "/Link"' in out
    assert '"Page": 1' in out


def test_dump_annots_filters_and_errors(annot_pdf, capsys, caplog):
    """Test filtering logic and error handling in dump_data_annots."""
    caplog.set_level(logging.DEBUG)

    # 1. Annotation without Subtype (Line 152 coverage)
    no_subtype = pikepdf.Dictionary(Type=pikepdf.Name.Annot, Rect=[0, 0, 10, 10])

    # 2. JavaScript Action (Line 165 coverage)
    js_action = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Link,
        Rect=[0, 0, 10, 10],
        A=pikepdf.Dictionary(S=pikepdf.Name.JavaScript, JS=pikepdf.String("alert('hi')")),
    )

    # 3. Ignored Keys /Border (Lines 192-194 coverage)
    # 4. Trigger for NotImplementedError (Lines 201-202 coverage)
    # We add a custom key "FailMe" that falls through to the try/except block.
    border_annot = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Link,
        Rect=[0, 0, 10, 10],
        Border=[0, 0, 1],
        FailMe=pikepdf.String("Trigger"),
    )

    annot_pdf.add_blank_page()
    annot_pdf.pages[1].Annots = annot_pdf.make_indirect([no_subtype, js_action, border_annot])

    # Define a side effect that ONLY raises for our specific trigger key.
    # This prevents crashing valid calls (like those for Action dictionaries).
    def side_effect(key, value, prefix, convert):
        if key == "FailMe" or key == "/FailMe":
            raise NotImplementedError("Expected Failure")
        return f"{prefix}{key}: {value}"

    with patch(
        "pdftl.commands.dump_annots._data_item_to_string_helper",
        side_effect=side_effect,
    ):
        dump_data_annots(annot_pdf, output_file=None)

    out = capsys.readouterr().out

    # Verify Filters
    assert "JavaScript" not in out
    assert "AnnotBorder" not in out

    # Verify Error Handling
    assert "Expected Failure" in caplog.text
