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

# Mock the return type of parse_sub_page_spec
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
        # OLD: token = ("filename", "+", 1)
        # NEW: The parser now emits a 'master' token for all arithmetic
        # Token structure: (var_name, "master", (offset_int, format_string))
        token = ("filename", "master", (1, None))

        context = {"filename": "MyDoc.pdf"}  # Non-numeric value

        # The error message remains the same
        with pytest.raises(
            ValueError, match="Cannot apply arithmetic to non-numeric variable: filename"
        ):
            mock_evaluate(token, context)

    @patch(
        "pdftl.commands.parsers.add_text_parser._evaluate_token",
        wraps=__import__(
            "pdftl.commands.parsers.add_text_parser"
        ).commands.parsers.add_text_parser._evaluate_token,
    )
    def test_evaluate_token_arithmetic_add(self, mock_evaluate):
        """Covers line 512: return base_value + val"""
        # OLD: token = ("page", "+", 5)
        # NEW: Offset is positive 5
        token = ("page", "master", (5, None))

        context = {"page": 10}  # Numeric value
        assert mock_evaluate(token, context) == 15

    @patch(
        "pdftl.commands.parsers.add_text_parser._evaluate_token",
        wraps=__import__(
            "pdftl.commands.parsers.add_text_parser"
        ).commands.parsers.add_text_parser._evaluate_token,
    )
    def test_evaluate_token_arithmetic_sub(self, mock_evaluate):
        """Covers subtraction logic"""
        # OLD: token = ("page", "-", 2)
        # NEW: Subtraction is represented as a negative offset in the master token
        token = ("page", "master", (-2, None))

        context = {"page": 10}
        assert mock_evaluate(token, context) == 8


from pdftl.commands.parsers.add_text_parser import (
    _compile_text_renderer,
    _evaluate_token,
    _normalize_options,
    _parse_options_content,
    _parse_var_expression,
    _tokenize_text_string,
)


class TestAddTextParserExtended:
    """
    Targeted tests to close coverage gaps in add_text_parser.py
    """

    # --- Coverage: Lines 287-288 ---
    def test_parse_options_skips_empty_commas(self):
        """
        Tests that double commas or trailing commas in options don't cause crashes.
        Covers: if not part: continue
        """
        options_str = "align=center, , color=0 0 0,"
        result = _parse_options_content(options_str)
        assert result["align"] == "center"
        # Color normalizes to list
        assert result["color"] == [0.0, 0.0, 0.0, 1]

    # --- Coverage: Lines 324, 326-329, 331-332 ---
    def test_normalize_options_full_integration(self):
        """
        Tests the integration of layout, formatting, and positioning in one call.
        Also tests strict error raising for unknown options remains after normalization.
        """
        # 1. Test Success Path (Hitting lines 326-329)
        raw_options = {
            "rotate": "90",
            "offset-x": "10pt",
            "offset-y": "5mm",
            "font": "Helvetica",
            "size": "12",
            "color": "0",
            "align": "right",
        }
        normalized = _normalize_options(raw_options)
        assert normalized["rotate"] == 90.0
        assert normalized["font"] == "Helvetica"
        assert normalized["align"] == "right"

        # 2. Test Error Path (Hitting lines 331-332)
        with pytest.raises(ValueError, match="Unknown options: invalid_opt"):
            _normalize_options({"align": "left", "invalid_opt": "10"})

    # --- Coverage: Lines 472-476 ---
    def test_master_regex_unknown_variable(self):
        """
        Tests that a string matching MASTER_VAR_REGEX syntax but containing
        an unknown variable name raises ValueError.
        Covers: if var not in KNOWN_VARS check inside the regex block.
        """
        # Syntax is valid {var+num}, but 'ghost' is not in KNOWN_VARS
        expr = "ghost+1"
        with pytest.raises(ValueError, match="Unknown variable: {ghost}"):
            _parse_var_expression(expr)

    def test_tokenize_text_string_edge_cases(self):
        """
        Tests tokenizer with adjacent tokens to ensure empty strings
        are skipped correctly.
        Covers: if not part: continue
        """
        # "{page}{total}" splits to ['', '{page}', '', '{total}', '']
        input_str = "{page}{total}"
        parts = _tokenize_text_string(input_str)

        # Should contain parsed tuples, no empty strings
        assert len(parts) == 2
        assert parts[0][0] == "page"  # var name
        assert parts[1][0] == "total"  # var name

    def test_compile_text_renderer_literal_braces(self):
        """
        Tests escaping braces {{ }}.
        Covers the elif part.startswith("{{") branch in tokenizer.
        """
        input_str = "Value: {{page}}"
        renderer = _compile_text_renderer(input_str)
        result = renderer({"page": 99})

        # Should render literal {page}, not the value 99
        assert result == "Value: {page}"

    # --- Edge Case: Master Formatting Error (Line 532) ---
    def test_master_formatting_error(self):
        """
        Tests standard formatting {var:fmt} when format is invalid.
        """
        # :d expects number, but we give it a string context (if mocked)
        # or invalid syntax.
        # Let's use an invalid format type for an integer.

        # {page:z} -> 'z' is not a valid format type for integer
        token = ("page", "master", (0, "z"))
        context = {"page": 1}

        with pytest.raises(ValueError, match="Formatting error for {page:z}"):
            _evaluate_token(token, context)


from unittest.mock import patch

from pdftl.commands.parsers.add_text_parser import (
    parse_add_text_specs_to_rules,
)


class TestAddTextParserCoverage:

    def test_legacy_options_passed_through(self):
        """
        Covers Lines 314, 316-317.
        Even though we removed the 'call' syntax, _normalize_options still
        checks for 'format' and 'start' if passed in the options block.
        """
        # Input: 1/text/(format=xyz, start=10)
        # These options don't do anything in the renderer anymore,
        # but the parser still processes them.
        specs = ["1/mytext/(format=xyz, start=10)"]
        rules = parse_add_text_specs_to_rules(specs, 10)

        # Check page 0 (user page 1)
        rule = rules[0][0]
        assert rule["format"] == "xyz"
        assert rule["start"] == 10

    def test_legacy_option_start_invalid(self):
        """
        Covers Lines 318-319.
        Ensures passing a non-integer 'start' raises ValueError.
        """
        specs = ["1/mytext/(start=invalid)"]
        with pytest.raises(ValueError, match="Variable parameter 'start' must be an integer"):
            parse_add_text_specs_to_rules(specs, 10)

    def test_evaluate_token_fallback(self):
        """
        Covers Line 525.
        Tests the fallback return when a token has a valid var name
        but an unknown operation (neither 'master', 'total-page', nor 'meta').
        """
        context = {"myvar": "success"}
        # Manually create a token that the parser wouldn't naturally generate
        # Token format: (var_name, op_name, params)
        token = ("myvar", "unknown_op", None)

        result = _evaluate_token(token, context)
        assert result == "success"

    def test_options_regex_failure(self):
        """
        Covers Lines 277-278.
        Simulates a regex failure in _parse_options_content.
        This requires mocking because standard strings won't crash re.split.
        """
        specs = ["1/text/(a=b)"]

        # Patch the regex object used in the module
        with patch("pdftl.commands.parsers.add_text_parser.COMMA_SPLIT_REGEX") as mock_regex:
            # Force the split method to raise a ValueError
            mock_regex.split.side_effect = ValueError("Mocked regex failure")

            with pytest.raises(ValueError, match="Could not parse options"):
                parse_add_text_specs_to_rules(specs, 10)
