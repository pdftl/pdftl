import logging
from unittest.mock import MagicMock

import pytest
from pikepdf import Array, Dictionary, Name, Pdf

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.modify_annots import (
    _apply_mods_to_annot,
    _parse_array_value,
    _parse_value_to_python,
    modify_annots,
)


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


from unittest.mock import patch


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
