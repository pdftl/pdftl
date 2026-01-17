import logging
from unittest.mock import patch

import pikepdf
import pytest

from pdftl.operations.dump_annots import dump_annots, dump_data_annots


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


from pdftl.operations.dump_annots import (
    dump_annots_cli_hook,
    dump_data_annots_cli_hook,
)


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


def test_lines_from_datum_skips():
    from pdftl.operations.dump_annots import _lines_from_datum

    # 1. Test missing /Subtype (Line 213)
    datum_no_subtype = {"Properties": {}, "Page": 1, "AnnotationIndex": 1}
    assert _lines_from_datum(datum_no_subtype, lambda x: x) == []

    # 2. Test JavaScript skip (Line 226)
    datum_js = {
        "Properties": {"/Subtype": "/Widget", "/A": {"/S": "/JavaScript"}},
        "Page": 1,
        "AnnotationIndex": 1,
    }
    assert _lines_from_datum(datum_js, lambda x: x) == []

    # 3. Test Unknown Subtype (Line 224)
    datum_unknown = {"Properties": {"/Subtype": "/Unknown"}, "Page": 1}
    assert _lines_from_datum(datum_unknown, lambda x: x) == []


from unittest.mock import MagicMock

from pikepdf import Name

from pdftl.operations.dump_annots import _get_all_annots_data


def test_get_all_annots_with_named_destinations():
    """Hits line 171 by providing a PDF Root with Names and Dests."""
    mock_pdf = MagicMock()
    mock_pdf.pages = []

    # Setup the Root structure pikepdf expects
    mock_names = MagicMock()
    # Ensure Name.Dests exists in Root.Names
    mock_names.__contains__.side_effect = lambda key: key == Name.Dests

    mock_pdf.Root.Names = mock_names
    # This triggers: if Name.Names in pdf.Root and Name.Dests in pdf.Root.Names
    mock_pdf.Root.__contains__.side_effect = lambda key: key == Name.Names

    with patch("pikepdf.NameTree") as mock_tree:
        mock_tree.return_value = {"Dest1": "Obj1"}
        _get_all_annots_data(mock_pdf)

        # Verify NameTree was called, confirming we entered the block at 171
        mock_tree.assert_called_once()


import logging
from unittest.mock import Mock, patch

import pikepdf
import pytest

# Import the internal function directly for the unit test
from pdftl.operations.dump_annots import (
    _key_value_lines,
    dump_data_annots,
    dump_data_annots_cli_hook,
)


def test_dump_annots_pdftk_filters(annot_pdf, capsys):
    """
    Integration Test: Verify that dump_data_annots (compat=True) correctly
    filters out 'noise' like /Type, /Border, and /JavaScript actions.
    """
    # 1. Annotation with /Type (should be filtered)
    # 2. Annotation with /Border (should be filtered)
    # 3. Javascript Action (should be filtered)
    js_action = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Link,
        Rect=[0, 0, 10, 10],
        Border=[0, 0, 1],
        A=pikepdf.Dictionary(S=pikepdf.Name.JavaScript, JS=pikepdf.String("alert('hi')")),
    )

    annot_pdf.add_blank_page()
    annot_pdf.pages[1].Annots = annot_pdf.make_indirect([js_action])

    # Run the operation
    result = dump_data_annots(annot_pdf, output_file=None)

    # Run the CLI hook (which does the formatting)
    dump_data_annots_cli_hook(result, None)

    out = capsys.readouterr().out

    # Assertions: 'noise' keys should NOT be present
    assert "JavaScript" not in out
    assert "AnnotBorder" not in out
    assert "AnnotType" not in out
    # Valid keys SHOULD be present
    assert "AnnotSubtype: Link" in out


def test_dump_annots_error_handling(caplog):
    """
    Unit Test: Verify that _key_value_lines catches NotImplementedError
    and logs a warning instead of crashing.
    """
    caplog.set_level(logging.WARNING)

    # We mock the helper to raise the error we want to catch
    def side_effect(*args, **kwargs):
        raise NotImplementedError("Simulated Failure")

    with patch(
        "pdftl.operations.dump_annots._data_item_to_string_helper", side_effect=side_effect
    ):
        # We call the internal function directly.
        # We can use any key (like "FailMe") because we aren't restricted by the
        # CLI's 'compat=True' filter here.
        result = _key_value_lines(
            key="/FailMe", value="Trigger", prefix="Annot", string_convert=str
        )

    # 1. Result should be empty (it swallowed the error and returned [])
    assert result == []

    # 2. It should have logged the warning with our key and error message
    assert "Skipping unsupported annotation key" in caplog.text
    assert "/FailMe" in caplog.text
    assert "Simulated Failure" in caplog.text


# tests/test_dump_annots_coverage.py

import pytest

from pdftl.operations.dump_annots import _key_value_lines, _lines_from_datum


def test_lines_from_datum_compat_false():
    """
    Covers Line 242: _lines_from_datum with compat=False.
    The logic adds 'IndexInPage' which is normally suppressed in compat mode.
    """
    # Setup a datum dict that mimics what comes out of _annots_json_for_page
    datum = {
        "Page": 1,
        "AnnotationIndex": 99,
        "Properties": {
            "/Subtype": "/Text",  # Required to pass the subtype check
            "/Contents": "Test Annotation",
        },
    }

    # Helper for string conversion
    def simple_str(x):
        return str(x)

    # Execute with compat=False
    lines = _lines_from_datum(datum, simple_str, compat=False)

    # Verify line 242 logic execution
    # It should have added "AnnotIndexInPage: 99"
    assert any("AnnotIndexInPage: 99" in line for line in lines)


def test_key_value_lines_action_handling():
    """
    Covers Line 253 (Key == '/A') and Line 281 (Key == 'S' -> 'Subtype').
    """
    prefix = "Annot"

    def simple_str(x):
        return str(x)

    # Dictionary representing an Action (/A) with a Subtype (/S)
    # This structure triggers the recursion in line 253
    action_value = {"/S": "/URI", "/URI": "http://example.com"}

    # Execute
    lines = _key_value_lines("/A", action_value, prefix, simple_str, compat=True)

    # Verify Line 253 was entered (processing /A)
    # Verify Line 281 was executed (The 'S' key inside the action dict becomes 'Subtype')
    # Expected output string: "AnnotActionSubtype: URI"
    assert any("AnnotActionSubtype: URI" in line for line in lines)
    assert any("AnnotActionURI: http://example.com" in line for line in lines)


def test_key_value_lines_ignored_keys():
    """
    Covers Line 260: Keys that are ignored (/Type, /Border, or len < 4).
    """
    prefix = "Annot"

    def simple_str(x):
        return str(x)

    # Case 1: /Border (Explicitly ignored)
    lines_border = _key_value_lines("/Border", [0, 0, 0], prefix, simple_str)
    assert lines_border == []

    # Case 2: /Type (Explicitly ignored)
    lines_type = _key_value_lines("/Type", "/Annot", prefix, simple_str)
    assert lines_type == []

    # Case 3: Short keys (len < 4), e.g., /C (Color)
    lines_short = _key_value_lines("/C", [1, 0, 0], prefix, simple_str)
    assert lines_short == []
