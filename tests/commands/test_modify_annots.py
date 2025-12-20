# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/commands/test_modify_annots.py

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

# We must import the module to test, aliased as 'ma'
import pdftl.commands.modify_annots as ma
from pdftl.exceptions import InvalidArgumentError

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
    page3 = pdf.add_blank_page()  # This page remains blank (no annots)

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
    ma.modify_annots(pdf, specs)

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
    ma.modify_annots(pdf, specs)

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
    ma.modify_annots(pdf, specs)

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
    ma.modify_annots(pdf, specs)

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
    ma.modify_annots(pdf, specs)

    # --- Assert ---
    # Page 1 (odd) should NOT be modified
    assert pikepdf.Name.Key not in pdf.pages[0].Annots[0]

    # Page 2 (even) SHOULD be modified
    assert pdf.pages[1].Annots[0].Key == pikepdf.String("EvenTest")
    assert pdf.pages[1].Annots[1].Key == pikepdf.String("EvenTest")

    # Page 3 (odd) should NOT be modified
    assert pikepdf.Name.Annots not in pdf.pages[2]


# Patch the parser to bypass its validation and test the function's own guard
@patch("pdftl.commands.modify_annots.specs_to_modification_rules")
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
        ma.modify_annots(pdf, specs)

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
    ma.modify_annots(pdf, specs)

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

    ma.modify_annots(pdf, [])  # Empty specs list

    assert pdf.pages[0].Annots[0].Border == original_border


def test_modify_annots_no_annots_on_page(mock_pdf):
    """
    Tests that the operation runs without error on a page (page 3)
    that has no /Annots key at all.
    """
    pdf = mock_pdf
    specs = ["3(Key=Value)"]

    # This should run without raising an AttributeError
    ma.modify_annots(pdf, specs)
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
        ma.modify_annots(pdf, specs)


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
        ma.modify_annots(pdf, specs)

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
        ma.modify_annots(pdf, specs)

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
            ma.modify_annots(pdf, specs)
        except InvalidArgumentError:
            # This is an acceptable failure (parser rejected the spec)
            pass
        except Exception as e:
            # Any *other* exception is a failure
            pytest.fail(f"modify_annots crashed on spec: '{spec}'\nError: {e}")
    finally:
        # Ensure the PDF is always closed
        pdf.close()
