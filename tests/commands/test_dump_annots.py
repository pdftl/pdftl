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


from pdftl.commands.dump_annots import (
    dump_annots,
    dump_annots_cli_hook,
    dump_data_annots,
    dump_data_annots_cli_hook,
)
from pdftl.core.types import OpResult


def test_dump_data_annots_pdftk_style(annot_pdf, capsys):
    """Test the pdftk-style output (key: value pairs)."""
    # 1. Run the command to get the data
    result = dump_data_annots(annot_pdf, output_file=None)

    assert result.success
    # Verify data structure contains what we expect
    assert "PdfUriBase" in result.data
    assert str(result.data["PdfUriBase"]) == "http://example.com/"

    # 2. Run the hook to verify the text output formatting
    dump_data_annots_cli_hook(result, None)

    out = capsys.readouterr().out
    assert "PdfUriBase: http://example.com/" in out
    assert "NumberOfPages: 1" in out


def test_dump_annots_json(annot_pdf, capsys):
    """Test the JSON dump output."""
    result = dump_annots(annot_pdf, output_file=None)

    assert result.success
    # Check raw data first
    assert len(result.data) > 0
    assert result.data[0]["Properties"]["/Subtype"] == "/Link"

    # Run the hook to test JSON serialization to stdout
    dump_annots_cli_hook(result, None)

    out = capsys.readouterr().out
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
    def side_effect(key, value, prefix, convert):
        if key == "FailMe" or key == "/FailMe":
            raise NotImplementedError("Expected Failure")
        return f"{prefix}{key}: {value}"

    # Get the raw result first
    result = dump_data_annots(annot_pdf, output_file=None)

    # Now patch the helper used by the HOOK (since that's where formatting happens)
    with patch(
        "pdftl.commands.dump_annots._data_item_to_string_helper",
        side_effect=side_effect,
    ):
        # Invoke formatting logic via the hook
        dump_data_annots_cli_hook(result, None)

    out = capsys.readouterr().out

    # Verify Filters
    assert "JavaScript" not in out
    assert "AnnotBorder" not in out

    # Verify Error Handling
    assert "Expected Failure" in caplog.text
