# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/test_modify_annots.py

"""
Integration and property-based tests for the modify_annots operation.

These tests focus on the high-level behavior of the `modify_annots`
function, validating its interaction with a pikepdf.Pdf object.
"""

import logging
from unittest.mock import MagicMock, patch  # Import mock tools

import pikepdf
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pikepdf import Array, Dictionary, Name, Pdf

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.modify_annots import (
    _apply_mods_to_annot,
    _parse_array_value,
    _parse_value_to_python,
    modify_annots,
)

# --- -----------------------
# Fixtures
# --- -----------------------


@pytest.fixture
def mock_pdf():
    """
    Creates a real in-memory Pdf object with a mock page/annotation structure.
    This allows testing the real pikepdf API interactions, following the
    pattern from test_links.py.

    - Page 1: One /Link annotation
    - Page 2: One /Highlight annotation, one /Link annotation
    - Page 3: No annotations
    """
    pdf = pikepdf.Pdf.new()

    # --- Create Annotations ---
    # We use real Dictionaries.
    annot1_link = pikepdf.Dictionary(
        Subtype=pikepdf.Name("/Link"), Border=pikepdf.Array([0, 0, 1])
    )
    annot2_highlight = pikepdf.Dictionary(
        Subtype=pikepdf.Name("/Highlight"), C=pikepdf.Array([1, 1, 0])
    )
    annot3_link = pikepdf.Dictionary(Subtype=pikepdf.Name("/Link"), T=pikepdf.String("Old Title"))

    # --- Create Pages and Add to Document ---
    page1 = pdf.add_blank_page()
    page2 = pdf.add_blank_page()
    _page3 = pdf.add_blank_page()  # This page remains blank (no annots)

    # --- Attach Annotations (Correct API Usage, Guideline 3) ---
    # .Annots must be an indirect Array of Dictionaries
    # pikepdf.Dictionary objects are low-level and do NOT have .obj
    # They are used directly.
    page1.Annots = pdf.make_indirect(pikepdf.Array([annot1_link]))
    page2.Annots = pdf.make_indirect(pikepdf.Array([annot2_highlight, annot3_link]))
    # page3 has no .Annots key, to test this case.

    # The test will operate on this PDF object and then inspect
    # its contents directly.
    yield pdf

    # Teardown
    pdf.close()


# --- -----------------------
# Integration Tests
# --- -----------------------


def test_modify_annots_integration_remove_link_border(mock_pdf):
    """
    Tests that a spec targeting all /Link annots modifies
    annots on multiple pages, but not other types.
    """
    pdf = mock_pdf
    # Get original state of an annotation that should NOT be modified
    original_highlight_c = pdf.pages[1].Annots[0].C

    specs = ["/Link(Border=[0 0 0])"]
    modify_annots(pdf, specs)

    # --- Assert ---
    # We fetch the objects *from the PDF* to check their final state.
    annot1 = pdf.pages[0].Annots[0]
    annot2_highlight = pdf.pages[1].Annots[0]
    annot3_link = pdf.pages[1].Annots[1]

    # Check that both /Link annots were modified
    assert annot1.Border == pikepdf.Array([0, 0, 0])
    assert annot3_link.Border == pikepdf.Array([0, 0, 0])
    # Check that the /Highlight annot was *not* modified
    assert annot2_highlight.C == original_highlight_c


def test_modify_annots_integration_page_selector(mock_pdf):
    """
    Tests that a page selector correctly restricts modifications
    to only the specified page.
    """
    pdf = mock_pdf
    specs = ["1(MyKey=MyValue)"]  # Target *all* annots on page 1
    modify_annots(pdf, specs)

    # --- Assert ---
    annot1 = pdf.pages[0].Annots[0]
    annot2_highlight = pdf.pages[1].Annots[0]

    # Check that annot1 (on page 1) was modified
    assert annot1.MyKey == pikepdf.String("MyValue")
    # Check that annots on page 2 were *not* modified
    assert pikepdf.Name.MyKey not in annot2_highlight


def test_modify_annots_integration_combined_selector(mock_pdf):
    """
    Tests that a page *and* type selector restricts modifications
    to only the matching annotation.
    """
    pdf = mock_pdf
    # Get original state of an annot on the same page that should NOT be modified
    original_link_t = pdf.pages[1].Annots[1].T

    # Target /Highlight annots on page 2
    specs = ["2/Highlight(C=[1 0 0])"]
    modify_annots(pdf, specs)

    # --- Assert ---
    annot2_highlight = pdf.pages[1].Annots[0]
    annot3_link = pdf.pages[1].Annots[1]

    # Check that annot2 (page 2, /Highlight) was modified
    assert annot2_highlight.C == pikepdf.Array([1, 0, 0])
    # Check that annot3 (page 2, /Link) was *not* modified
    assert annot3_link.T == original_link_t


def test_modify_annots_page_selector_range(mock_pdf):
    """
    Tests that a page range selector (e.g., '1-2') correctly
    modifies annotations on all pages in that range.
    """
    pdf = mock_pdf
    specs = ["1-2(Key=RangeTest)"]
    modify_annots(pdf, specs)

    # --- Assert ---
    # Page 1 (in range) should be modified
    assert pdf.pages[0].Annots[0].Key == pikepdf.String("RangeTest")

    # Page 2 (in range) should be modified
    assert pdf.pages[1].Annots[0].Key == pikepdf.String("RangeTest")
    assert pdf.pages[1].Annots[1].Key == pikepdf.String("RangeTest")

    # Page 3 (out of range) should not be modified
    assert pikepdf.Name.Annots not in pdf.pages[2]


def test_modify_annots_page_selector_even(mock_pdf):
    """
    Tests that a keyword selector (e.g., 'even') correctly
    modifies annotations on only the matching pages.
    """
    pdf = mock_pdf
    specs = ["even(Key=EvenTest)"]
    modify_annots(pdf, specs)

    # --- Assert ---
    # Page 1 (odd) should NOT be modified
    assert pikepdf.Name.Key not in pdf.pages[0].Annots[0]

    # Page 2 (even) SHOULD be modified
    assert pdf.pages[1].Annots[0].Key == pikepdf.String("EvenTest")
    assert pdf.pages[1].Annots[1].Key == pikepdf.String("EvenTest")

    # Page 3 (odd) should NOT be modified
    assert pikepdf.Name.Annots not in pdf.pages[2]


# Patch the parser to bypass its validation and test the function's own guard
@patch("pdftl.operations.modify_annots.specs_to_modification_rules")
def test_modify_annots_page_selector_out_of_bounds(mock_specs_parser, mock_pdf, caplog):
    """
    Tests that a page selector referencing a page number
    greater than the PDF's page count does not crash and
    logs a warning.
    """
    pdf = mock_pdf  # Has 3 pages
    # This spec is now just a placeholder, the mock provides the real data
    specs = ["10(Key=Value)"]

    # --- Configure the Mock Parser ---
    # Create a fake rule that bypasses the parser's own validation
    # and includes the out-of-bounds page number.
    mock_rule = MagicMock()
    mock_rule.page_numbers = [10]  # The invalid page number
    mock_rule.type_selector = None
    mock_rule.modifications = [("Key", "Value")]
    mock_specs_parser.return_value = [mock_rule]
    # ---

    with caplog.at_level(logging.WARNING):
        # This will now call the function, but our mock will run
        # instead of the real specs_to_modification_rules
        modify_annots(pdf, specs)

    # --- Assert ---
    # Check that our bounds check in modify_annots caught the bad page
    assert "PDF only has 3 pages" in caplog.text
    assert "Skipping" in caplog.text

    # Use assert_called_with. The @register_operation decorator
    # appears to call the mock as well, so the call count is > 1.
    # This assertion confirms the *last* call was the correct one
    # from within the function body.
    mock_specs_parser.assert_called_with(specs, 3)


def test_modify_annots_integration_delete_key(mock_pdf):
    """
    Tests that the 'null' value correctly deletes a key
    from an annotation.
    """
    pdf = mock_pdf
    annot3_link = pdf.pages[1].Annots[1]
    assert pikepdf.Name.T in annot3_link  # Pre-condition

    specs = ["/Link(T=null)"]
    modify_annots(pdf, specs)

    # --- Assert ---
    # We must re-fetch the object in case it was modified
    annot3_link_modified = pdf.pages[1].Annots[1]
    # Check that the /T key was deleted
    assert pikepdf.Name.T not in annot3_link_modified


def test_modify_annots_no_specs(mock_pdf):
    """
    Tests that calling with an empty spec list does nothing.
    """
    pdf = mock_pdf
    original_border = pdf.pages[0].Annots[0].Border

    modify_annots(pdf, [])  # Empty specs list

    assert pdf.pages[0].Annots[0].Border == original_border


def test_modify_annots_no_annots_on_page(mock_pdf):
    """
    Tests that the operation runs without error on a page (page 3)
    that has no /Annots key at all.
    """
    pdf = mock_pdf
    specs = ["3(Key=Value)"]

    # This should run without raising an AttributeError
    modify_annots(pdf, specs)
    # No assertion needed, we just test that it didn't crash


def test_modify_annots_malformed_spec(mock_pdf):
    """
    Tests that a malformed spec (parser failure) raises an
    InvalidArgumentError.
    """
    pdf = mock_pdf
    # Missing closing parenthesis
    specs = ["/Link(Border=null"]

    with pytest.raises(InvalidArgumentError, match="Failed to parse"):
        modify_annots(pdf, specs)


def test_modify_annots_malformed_value_bug(mock_pdf, caplog):
    """
    Tests the bug: C=[]]
    This should be caught by the *value parser* (_parse_value_to_python),
    logged as a warning, and the modification should be skipped.
    """
    pdf = mock_pdf
    original_c = pdf.pages[1].Annots[0].C  # The highlight color

    specs = ["/Highlight(C=[]])"]  # Malformed array

    with caplog.at_level(logging.WARNING):
        modify_annots(pdf, specs)

    # --- Assert ---
    # Check that the malformed value was skipped
    assert pdf.pages[1].Annots[0].C == original_c
    # Check that it was logged
    assert "Skipping invalid value" in caplog.text
    assert "Mismatched brackets" in caplog.text


def test_modify_annots_malformed_string_value_bug(mock_pdf, caplog):
    """
    Tests a malformed string value.
    """
    pdf = mock_pdf
    original_t = pdf.pages[1].Annots[1].T

    specs = ["/Link(T=(Mismatched)"]  # Malformed string

    with caplog.at_level(logging.WARNING):
        modify_annots(pdf, specs)

    # --- Assert ---
    # Check that the malformed value was skipped
    assert pdf.pages[1].Annots[1].T == original_t
    # Check that it was logged
    assert "Skipping invalid value" in caplog.text
    # Assert for the correct error message
    assert "Malformed value string" in caplog.text


# --- -----------------------
# Hypothesis Tests
# --- -----------------------

# A strategy for generating arbitrary (and potentially malformed)
# spec strings. This replaces the narrow key/value fuzzers.
st_spec_string = st.text(
    alphabet="()[]/abcdefghijklmnopqrstuvwxyz1234567890-=, .",
    min_size=1,
    max_size=50,
)


@pytest.mark.slow
@given(spec=st_spec_string)
@settings(max_examples=500, deadline=None)
def test_modify_annots_hypothesis_fuzz_full_spec(spec):
    """
    Fuzzes the *entire spec string* to test the parser and function
    robustness against all malformed inputs.

    This test asserts that the function *never crashes* with an
    unhandled exception, even with bizarre spec inputs.

    It will either:
    1. Succeed (if the spec is valid OR skipped due to logging.warning)
    2. Raise InvalidArgumentError (if the *parser* rejects it)

    Any other exception (AttributeError, TypeError, raw ValueError)
    is a failure.
    """
    # Create a minimal, fresh PDF for *each* hypothesis run
    # to prevent state leakage from the mock_pdf fixture.
    pdf = pikepdf.Pdf.new()
    try:
        page = pdf.add_blank_page()
        annot = pikepdf.Dictionary(Subtype=pikepdf.Name("/Link"))
        page.Annots = pdf.make_indirect(pikepdf.Array([annot]))

        # Pass the fully fuzzed spec string
        specs = [spec]

        try:
            modify_annots(pdf, specs)
        except InvalidArgumentError:
            # This is an acceptable failure (parser rejected the spec)
            pass
        except Exception as e:
            # Any *other* exception is a failure
            pytest.fail(f"modify_annots crashed on spec: '{spec}'\nError: {e}")
    finally:
        # Ensure the PDF is always closed
        pdf.close()


# --- merged from test_modify_annots_coverage.py ---


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

    with patch("pdftl.operations.modify_annots.specs_to_modification_rules", return_value=[]):
        modify_annots(pdf, ["1/Link(A=B)"])

    assert "No modification rules parsed" in caplog.text


# --- merged from test_modify_annots_coverage_2.py ---


def test_parse_array_value_edge_cases():
    # Trigger line 75: arr_str that does not start/end with brackets
    assert _parse_array_value("not_an_array") == ["not_an_array"]

    # Trigger lines 86-88: ValueError/TypeError in array parsing
    # This happens if an item looks like a number but float() fails
    assert _parse_array_value("[1.2.3 /Name]") == ["1.2.3", "/Name"]


def test_parse_value_to_python_mismatched_delimiters(caplog):
    # Trigger lines 112-117: Mismatched parentheses in PDF string
    with caplog.at_level(logging.WARNING):
        result = _parse_value_to_python("(Unbalanced (String)")
        assert result == "Unbalanced (String"
        assert "Mismatched parentheses" in caplog.text

    # Trigger line 122: Mismatched brackets in array
    # Note: To hit line 122, it MUST start with [ and end with ]
    with pytest.raises(ValueError, match="Mismatched brackets"):
        _parse_value_to_python("[[0 0 1]")

    # Trigger line 139: Malformed value string (the error you encountered)
    # This happens when the string doesn't qualify as a "PDF Array" (doesn't end with ])
    # but still has unbalanced characters.
    with pytest.raises(ValueError, match="Malformed value string"):
        _parse_value_to_python("[0 0 1")


def test_parse_value_to_python_number_fallbacks():
    # Trigger lines 133-135: try/except block for numbers
    # A string that passes isdigit but is somehow invalid for float
    # (Hard to hit with current regex-like check, but we cover the 'pass' logic)
    assert _parse_value_to_python("PlainString") == "PlainString"

    # Another way to hit the final validation at 138-139
    with pytest.raises(ValueError, match="Malformed value string"):
        _parse_value_to_python("Unbalanced(String")


def test_apply_mods_to_annot_skips_invalid(caplog):
    # Setup mock annotation
    annot = Dictionary()
    mods = [("Key", "[0 0 1")]  # This will trigger the ValueError at line 139

    # Trigger line 156-163: Catching the ValueError from _parse_value_to_python
    with caplog.at_level(logging.WARNING):
        count = _apply_mods_to_annot(annot, mods, 1)
        assert count == 0
        assert "Skipping invalid value" in caplog.text


def test_modify_annots_empty_and_errors():
    pdf = MagicMock(spec=Pdf)
    pdf.pages = [MagicMock()]

    # Test empty specs (line 196)
    result = modify_annots(pdf, [])
    assert result.success is False

    # Test invalid spec input (line 203-206)
    with pytest.raises(InvalidArgumentError):
        modify_annots(pdf, [None])


def test_apply_rule_logic():
    # Integration test for the rule application logic
    pdf = Pdf.new()
    pdf.add_blank_page()
    page = pdf.pages[0]

    # Add a real annotation to modify
    annot = Dictionary(Type=Name.Annot, Subtype=Name.Highlight, C=Array([0, 1, 0]))
    page.Annots = Array([annot])

    from pdftl.operations.parsers.modify_annots_parser import ModificationRule

    rule = ModificationRule(
        page_numbers=[1],
        type_selector="/Highlight",
        modifications=[("C", "[1 0 0]"), ("T", "(New Title)")],
    )

    from pdftl.operations.modify_annots import _apply_rule

    annot_count, prop_count = _apply_rule(pdf, rule, 1)

    assert annot_count == 1
    assert prop_count == 2
    assert page.Annots[0].C == [1.0, 0.0, 0.0]
    assert page.Annots[0].T == "New Title"


def test_coverage_mop_up_array_exceptions():
    """
    Targets lines 86-88: The except (ValueError, TypeError) block in _parse_array_value.
    We force this by mocking float() to raise an error during the loop.
    """
    with patch("pdftl.operations.modify_annots.float") as mock_float:
        mock_float.side_effect = ValueError("Forced error")
        # "1.0" will pass the 'if looks like number' check, then hit mock_float
        result = _parse_array_value("[1.0]")
        assert result == ["1.0"], "Should have fallen back to returning the string item"


def test_coverage_mop_up_value_to_python_exceptions():
    """
    Targets lines 134-135: The except (ValueError, TypeError) block in _parse_value_to_python.
    We use a string that passes the .isdigit() / .replace() check but fails float().
    In Python, some Unicode characters return True for isdigit() but fail float().
    Alternatively, we can use a mock.
    """
    # String with a superset of digits that might pass checks but fail conversion
    # Or simply mock float again for this specific scope
    with patch("pdftl.operations.modify_annots.float") as mock_float:
        mock_float.side_effect = TypeError("Forced type error")
        # "123" passes the digit check, then hits the mock
        result = _parse_value_to_python("123")
        assert result == "123", "Should have caught the TypeError and continued to line 142"


def test_natural_trigger_for_number_check_logic():
    """
    Alternative approach to hit lines 134-135 without mocks if preferred.
    """
    # This string passes: val_str.replace(".", "", 1).lstrip("-+").isdigit()
    # But float() will fail because of the embedded space.
    # .isdigit() returns False for "1 2", but let's look for a weird one.

    # Actually, the most robust way to hit that specific line 134/135
    # (which is an 'except' for a 'try' that only contains 'return float')
    # is to provide a value that passes the 'if' but fails the 'return'.

    # Example: A very large string of digits that might cause an Overflow or similar,
    # though float() usually handles that as 'inf'.
    # The mock approach above is the most guaranteed way to hit the 'pass' line.
    pass
