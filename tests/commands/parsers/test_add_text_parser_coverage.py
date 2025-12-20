from collections import namedtuple
from unittest.mock import patch

import pytest

# Assume the module being tested is imported as 'parser'
# from pdftl.commands.parsers import add_text_parser as parser

# --- Setup Mocks for External Dependencies ---

# Mock the UNITS constant from pdftl.core.constants (Line 13)
MOCKED_UNITS = {
    "pt": 1.0,
    "mm": 2.83465,  # Example value
    "cm": 28.3465,  # Example value
    "in": 72.0,  # Example value
}

# Mock the return type of parse_page_spec
PageSpec = namedtuple("PageSpec", ["start", "end", "qualifiers"])


@patch("pdftl.commands.parsers.add_text_parser.UNITS", MOCKED_UNITS, create=True)
class TestAddTextParser:

    # =========================================================================
    # Test _split_spec_string (Covers Lines: 145, 174)
    # =========================================================================
    @patch(
        "pdftl.commands.parsers.add_text_parser._split_spec_string",
        wraps=__import__(
            "pdftl.commands.parsers.add_text_parser"
        ).commands.parsers.add_text_parser._split_spec_string,
    )
    def test_split_spec_string_raises_on_empty_spec(self, mock_split):
        """Covers line 145: raise ValueError("Empty add_text spec")"""
        with pytest.raises(ValueError, match="Empty add_text spec"):
            mock_split("")

    @patch(
        "pdftl.commands.parsers.add_text_parser._split_spec_string",
        wraps=__import__(
            "pdftl.commands.parsers.add_text_parser"
        ).commands.parsers.add_text_parser._split_spec_string,
    )
    def test_split_spec_string_raises_on_only_options_block(self, mock_split):
        """Covers line 174: raise ValueError("Missing text string component")"""
        # A spec that only contains an options block, leaving rest_of_spec empty.
        with pytest.raises(ValueError, match="Missing text string component"):
            mock_split("()")

    # =========================================================================
    # Test _parse_options_string (Covers Lines: 234, 242-243, 248, 255)
    # =========================================================================

    @patch("pdftl.commands.parsers.add_text_parser._normalize_options", return_value={})
    @patch(
        "pdftl.commands.parsers.add_text_parser._parse_options_string",
        wraps=__import__(
            "pdftl.commands.parsers.add_text_parser"
        ).commands.parsers.add_text_parser._parse_options_string,
    )
    def test_parse_options_string_empty_parentheses(self, mock_parse, mock_normalize):
        """Covers line 234: return {}"""
        assert mock_parse("()") == {}
        mock_normalize.assert_not_called()

    @patch(
        "pdftl.commands.parsers.add_text_parser._parse_options_string",
        wraps=__import__(
            "pdftl.commands.parsers.add_text_parser"
        ).commands.parsers.add_text_parser._parse_options_string,
    )
    def test_parse_options_string_invalid_option_format(self, mock_parse):
        """Covers line 255: raise ValueError for invalid key/value format"""
        # The input has a mismatched quote, leading to a split part that lacks an '=' (failing line 255).
        with pytest.raises(ValueError, match="Invalid option format: 'value'"):
            mock_parse("(key='value, value, key2=value2)")

    @patch(
        "pdftl.commands.parsers.add_text_parser._normalize_options",
        return_value={"font": "Arial", "size": {"type": "pt", "value": 12.0}},
    )
    @patch(
        "pdftl.commands.parsers.add_text_parser._parse_options_string",
        wraps=__import__(
            "pdftl.commands.parsers.add_text_parser"
        ).commands.parsers.add_text_parser._parse_options_string,
    )
    def test_parse_options_string_empty_part_after_comma(self, mock_parse, mock_normalize):
        """Covers line 248: continue (Skip empty parts, e.g., from "foo=bar,,baz=qux")"""
        # Input has an empty part: (key1=value1,,key2=value2) or (key1=value1, ,key2=value2).
        # We use non-conflicting options ('font' and 'size') to avoid internal validation errors.
        options = mock_parse("(font='Arial', ,size=12pt)")
        assert options["font"] == "Arial"
        # The size is normalized by _normalize_options (which we mocked to return the correct structure)
        assert options["size"] == {"type": "pt", "value": 12.0}
        # Verify that the parser correctly processed the raw options before normalization
        mock_normalize.assert_called_once_with({"font": "Arial", "size": "12pt"})

    # =========================================================================
    # Test _parse_dimension (Covers Lines: 353, 359-360, 368-369, 374-375)
    # =========================================================================

    @patch(
        "pdftl.commands.parsers.add_text_parser._parse_dimension",
        wraps=__import__(
            "pdftl.commands.parsers.add_text_parser"
        ).commands.parsers.add_text_parser._parse_dimension,
    )
    def test_parse_dimension_already_parsed(self, mock_parse):
        """Covers line 353: return size_str (Already parsed, e.g., from a test)"""
        pre_parsed = {"type": "%", "value": 50.0}
        assert mock_parse(pre_parsed) is pre_parsed

    @patch(
        "pdftl.commands.parsers.add_text_parser._parse_dimension",
        wraps=__import__(
            "pdftl.commands.parsers.add_text_parser"
        ).commands.parsers.add_text_parser._parse_dimension,
    )
    def test_parse_dimension_invalid_percentage(self, mock_parse):
        """Covers lines 359-360: try/except for percentage float conversion"""
        with pytest.raises(ValueError, match="Invalid percentage value: '50a%'"):
            mock_parse("50a%")

    @patch(
        "pdftl.commands.parsers.add_text_parser._parse_dimension",
        wraps=__import__(
            "pdftl.commands.parsers.add_text_parser"
        ).commands.parsers.add_text_parser._parse_dimension,
    )
    def test_parse_dimension_invalid_unit_value(self, mock_parse):
        """Covers lines 368-369: try/except for unit value float conversion"""
        # Use a mocked unit ('pt') which is found via _find_unit
        with pytest.raises(ValueError, match="Invalid size value: '10bpt'"):
            mock_parse("10bpt")

    @patch(
        "pdftl.commands.parsers.add_text_parser._parse_dimension",
        wraps=__import__(
            "pdftl.commands.parsers.add_text_parser"
        ).commands.parsers.add_text_parser._parse_dimension,
    )
    def test_parse_dimension_invalid_default_value(self, mock_parse):
        """Covers lines 374-375: try/except for default 'pt' float conversion"""
        # No unit found, tries to convert whole string to float (default 'pt')
        with pytest.raises(ValueError, match="Invalid size or unit in dimension: 'ten'"):
            mock_parse("ten")

    # =========================================================================
    # Test _parse_color (Covers Lines: 395-397, 416)
    # =========================================================================

    @patch(
        "pdftl.commands.parsers.add_text_parser._parse_color",
        wraps=__import__(
            "pdftl.commands.parsers.add_text_parser"
        ).commands.parsers.add_text_parser._parse_color,
    )
    def test_parse_color_invalid_characters(self, mock_parse):
        """Covers lines 395-397: try/except for float conversion of parts"""
        # Contains non-numeric characters: 'a'
        with pytest.raises(ValueError, match="Invalid characters in color string: '1 0 a'"):
            mock_parse("1 0 a")

    @patch(
        "pdftl.commands.parsers.add_text_parser._parse_color",
        wraps=__import__(
            "pdftl.commands.parsers.add_text_parser"
        ).commands.parsers.add_text_parser._parse_color,
    )
    def test_parse_color_invalid_num_parts(self, mock_parse):
        """Covers line 416: raise ValueError for incorrect number of parts (2)"""
        # Too few parts (2)
        with pytest.raises(ValueError, match="Color string '1 0' must have 1.*Got 2."):
            mock_parse("1 0")

        """Covers line 416: raise ValueError for incorrect number of parts (5)"""
        # Too many parts (5)
        with pytest.raises(ValueError, match="Color string '1 0 0 0 0' must have 1.*Got 5."):
            mock_parse("1 0 0 0 0")

    # =========================================================================
    # Test _evaluate_token (Covers Lines: 509, 512)
    # =========================================================================

    @patch(
        "pdftl.commands.parsers.add_text_parser._evaluate_token",
        wraps=__import__(
            "pdftl.commands.parsers.add_text_parser"
        ).commands.parsers.add_text_parser._evaluate_token,
    )
    def test_evaluate_token_arithmetic_on_non_numeric_variable(self, mock_evaluate):
        """Covers line 509: raise ValueError for arithmetic on non-numeric variable"""
        # Token for '{filename+1}' would be ('filename', '+', 1)
        token = ("filename", "+", 1)
        context = {"filename": "MyDoc.pdf"}  # Non-numeric value
        with pytest.raises(ValueError, match="Cannot apply arithmetic to variable: filename"):
            mock_evaluate(token, context)

    @patch(
        "pdftl.commands.parsers.add_text_parser._evaluate_token",
        wraps=__import__(
            "pdftl.commands.parsers.add_text_parser"
        ).commands.parsers.add_text_parser._evaluate_token,
    )
    def test_evaluate_token_arithmetic_add(self, mock_evaluate):
        """Covers line 512: return base_value + val"""
        # Token for '{page+5}' would be ('page', '+', 5)
        token = ("page", "+", 5)
        context = {"page": 10}  # Numeric value
        assert mock_evaluate(token, context) == 15
