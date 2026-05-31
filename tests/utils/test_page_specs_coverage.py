import unittest
from unittest.mock import MagicMock, patch

import pytest

from pdftl.exceptions import InvalidArgumentError, UserCommandLineError
from pdftl.utils.page_specs import _expand_square_brackets, _flatten_spec_list, parse_specs
from pdftl.utils.page_specs.parser import SpecParser
from pdftl.utils.page_specs.resolver import _aspect_ratio_pass


def test_expand_square_brackets_logic():
    """
    Tests the internal group expansion logic to cover lines 130-149.
    """
    # 1. Test standard group expansion: [1, 3]r90 -> 1r90, 3r90
    specs = ["[1, 3]r90"]
    result = _expand_square_brackets(specs)
    assert result == ["1r90", "3r90"]

    # 2. Test mixed input (regular specs + groups)
    specs = ["5", "[1,2]x2"]
    result = _expand_square_brackets(specs)
    assert result == ["5", "1x2", "2x2"]


def test_expand_square_brackets_ambiguity_guardrail():
    """
    Tests that a comma in the suffix raises an error.
    Covers lines 136-142.
    """
    # This spec is ambiguous: does it mean ([1,2]x2), 3 OR [1,2](x2,3)?
    # The code forbids it to prevent user error.
    specs = ["[1, 2]x2, 3"]

    with pytest.raises(UserCommandLineError) as excinfo:
        _expand_square_brackets(specs)

    assert "Found a comma after the closing bracket" in str(excinfo.value)


def test_flatten_spec_list_ignores_none():
    """
    Tests that None entries are skipped.
    Covers line 240.
    """
    specs = ["1", None, "2,3"]
    result = _flatten_spec_list(specs)
    assert result == ["1", "2", "3"]


def test_flatten_spec_list_handles_empty_or_whitespace_only():
    """
    Tests that whitespace-only entries are converted to empty entries.
    """
    specs = ["1", "", "2,3"]
    result = _flatten_spec_list(specs)
    assert result == ["1", "", "2", "3"]

    specs = ["1", " ", "2,3"]
    result = _flatten_spec_list(specs)
    assert result == ["1", "", "2", "3"]


class TestPageSpecs(unittest.TestCase):
    def test_expand_square_brackets_with_none(self):
        # Input list containing a valid spec and a None value
        specs = ["[1,2]x2", None, "5"]

        # The expected behavior is that None is skipped,
        # and the valid specs are processed normally.
        expected = ["1x2", "2x2", "5"]

        try:
            result = _expand_square_brackets(specs)
            self.assertEqual(result, expected)
        except AttributeError:
            self.fail("_expand_square_brackets() raised AttributeError on None value!")


if __name__ == "__main__":
    unittest.main()


def test_parse_specs_pipeline_integration():
    """
    Covers page_specs.py lines 328-334:
    Verifies that parse_specs correctly chains _expand_square_brackets,
    _flatten_spec_list, and parse_sub_page_spec in a generator loop.
    """
    # Input contains:
    # 1. A standard range: "1-2"
    # 2. A comma-separated string that needs flattening: "4,5"
    # 3. A group syntax that needs expansion: "[6,7]x2"
    specs_input = ["1-2", "4,5", "[6,7]x2"]
    total_pages = 10

    # Execute the generator
    results = list(parse_specs(specs_input, total_pages))

    # Expectation:
    # 1. "1-2" -> 1 spec (start=1, end=2)
    # 2. "4"   -> 1 spec (start=4, end=4)
    # 3. "5"   -> 1 spec (start=5, end=5)
    # 4. "6x2" -> 1 spec (start=6, end=6, scale=2.0)
    # 5. "7x2" -> 1 spec (start=7, end=7, scale=2.0)
    assert len(results) == 5

    # Validate specific attributes to ensure the flow worked
    assert results[0].start == 1 and results[0].end == 2
    assert results[1].start == 4
    assert results[2].start == 5

    # Check scaling from the group expansion
    assert results[3].start == 6 and results[3].scale == 2.0
    assert results[4].start == 7 and results[4].scale == 2.0


def test_resolve_page_token_non_integer():
    parser = SpecParser(total_pages=10)
    with pytest.raises(InvalidArgumentError, match="Could not parse page token"):
        parser._resolve_page_token("abc", is_reverse=False)


def test_spec_parser_invalid_step_errors():
    parser = SpecParser(total_pages=10)
    with pytest.raises(InvalidArgumentError, match="Empty step value"):
        parser.parse("1-10stepeven")
    with pytest.raises(InvalidArgumentError, match="Empty step value"):
        parser.parse("1-10step")
    with pytest.raises(InvalidArgumentError, match="Invalid step value"):
        parser.parse("1-10step-4")
    with pytest.raises(InvalidArgumentError, match="Invalid step value"):
        parser.parse("1-10step0")


def test_handle_no_specs_returns_empty_when_inputs_none():
    from pdftl.utils.page_specs.resolver import _handle_no_specs

    result = _handle_no_specs(None, {})
    assert result == []


@patch("pdftl.utils.page_specs.resolver.get_visible_page_dimensions")
def test_aspect_ratio_pass_no_dimensions(mock_get_dims):
    """
    Covers line 63 in resolver.py.
    Tests the fallback when get_visible_page_dimensions returns None.
    """
    # Force the dimensions utility to return None
    mock_get_dims.return_value = None

    # Create a mock PDF with at least 1 page to bypass the line 59 check
    mock_pdf = MagicMock()
    mock_pdf.pages = [MagicMock()]

    # Evaluate a page number that exists in our mock
    result = _aspect_ratio_pass(p=1, portrait_q=True, landscape_q=False, pdf=mock_pdf)

    # If dims is None, the function should default to returning True
    assert result is True
    mock_get_dims.assert_called_once_with(mock_pdf.pages[0])
