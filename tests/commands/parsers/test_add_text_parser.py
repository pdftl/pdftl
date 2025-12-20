# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
Unit tests for the add_text_parser module.
Requires 'pytest' and 'hypothesis' to run.
"""

import re
import unittest
from collections import namedtuple

import pytest

# --- Mocks for dependencies ---
# We mock the external dependencies of add_text_parser for isolated testing.

# Mock pdftl.core.constants
# 1 cm = 72 (pts/in) / 2.54 (cm/in) = 28.346...
UNITS = {"pt": 1.0, "in": 72.0, "cm": 72.0 / 2.54}

# Mock pdftl.utils.page_specs
# A simple mock of parse_page_spec to return what the parser expects.
MockPageSpec = namedtuple("MockPageSpec", ["start", "end", "qualifiers"])


def mock_parse_page_spec(page_range_part, total_pages):
    """Mock implementation of parse_page_spec."""
    qualifiers = None
    if page_range_part.endswith("even"):
        qualifiers = "even"
        page_range_part = page_range_part[:-4]
    elif page_range_part.endswith("odd"):
        qualifiers = "odd"
        page_range_part = page_range_part[:-3]

    if not page_range_part or page_range_part == "1-end":
        return MockPageSpec(1, total_pages, qualifiers)

    if page_range_part == "even":
        return MockPageSpec(1, total_pages, "even")
    if page_range_part == "odd":
        return MockPageSpec(1, total_pages, "odd")

    if "-" in page_range_part:
        start_str, end_str = page_range_part.split("-")
        start = 1 if not start_str else int(start_str)
        end = total_pages if end_str == "end" or not end_str else int(end_str)
        return MockPageSpec(start, end, qualifiers)

    try:
        page_num = int(page_range_part)
        return MockPageSpec(page_num, page_num, qualifiers)
    except ValueError:
        raise


import pdftl.commands.parsers.add_text_parser

# Now, we can import the functions to be tested from the *new* module path
# Changed '_parse_text_string_to_renderer' to '_compile_text_renderer'
from pdftl.commands.parsers.add_text_parser import (  # Import new function for testing
    _compile_text_renderer,
    _parse_options_string,
    _split_spec_string,
    parse_add_text_specs_to_rules,
)

# --- Monkey-patching the parser's imports ---
# We must replace the imported names *within the parser module*
pdftl.commands.parsers.add_text_parser.UNITS = UNITS
pdftl.commands.parsers.add_text_parser.parse_page_spec = mock_parse_page_spec
# We also need to give it the 're' module for the fixed _split_spec_string
pdftl.commands.parsers.add_text_parser.re = re


class TestAddTextParser(unittest.TestCase):
    """Traditional unit tests for specific inputs and error cases."""

    def setUp(self):
        self.total_pages = 20
        self.maxDiff = None
        self.context = {
            "page": 5,
            "total": 20,
            "filename": "test.pdf",
            "filename_base": "test",
            "metadata": {
                "Title": "My Report",
                "Author": "John Doe",
            },
        }

    def test_split_spec_string(self):
        """Test the robust, right-to-left spec string splitter."""
        test_cases = {
            # --- Standard cases ---
            "spaces": ("1-5 /Hello/ (options)", ("1-5", "Hello", "(options)")),
            "no_spaces": ("1-5/Hello/(options)", ("1-5", "Hello", "(options)")),
            "different_delim": ("10 !World! (options)", ("10", "World", "(options)")),
            # --- Default page range ---
            "default_page": ("/Hello/ (options)", ("1-end", "Hello", "(options)")),
            "default_page_no_spaces": (
                "/Hello/(options)",
                ("1-end", "Hello", "(options)"),
            ),
            "default_page_spaces": (
                " /Hello/ (options) ",
                ("1-end", "Hello", "(options)"),
            ),
            # --- No options ---
            "no_options": ("even /Hello/", ("even", "Hello", "")),
            "no_options_no_spaces": ("even/Hello/", ("even", "Hello", "")),
            "no_options_spaces_in_text": (
                "1 ! Hello / World ! ()",
                ("1", " Hello / World ", "()"),
            ),
            # --- Edge cases ---
            "qualifier_page_range": ("1-10odd /Hello/", ("1-10odd", "Hello", "")),
            "qualifier_no_spaces": ("1-10odd/Hello/", ("1-10odd", "Hello", "")),
            "text_with_parens_in_options": (
                "1 /text/ (font='Test(1,2)', size=10)",
                ("1", "text", "(font='Test(1,2)', size=10)"),
            ),
            "text_with_parens_in_options_no_spaces": (
                "1/text/(font='Test(1,2)', size=10)",
                ("1", "text", "(font='Test(1,2)', size=10)"),
            ),
            "spaces_around_delims": (
                "1-5 ! text ! (options)",
                ("1-5", " text ", "(options)"),
            ),
        }

        for name, (input_str, expected) in test_cases.items():
            with self.subTest(name=name, input=input_str):
                self.assertEqual(_split_spec_string(input_str), expected)

    def test_split_fail_invalid_delimiter(self):
        # This test is still valid. The parser will see 'o' as the delimiter
        # and correctly reject it as alphanumeric.
        with self.assertRaisesRegex(ValueError, "Invalid text delimiter 'o'"):
            _split_spec_string("1 Hello (options)")

    def test_split_fail_unmatched_delimiter(self):
        # The new parser identifies an unmatched delimiter when only one
        # is found (first_pos == last_pos).
        with self.assertRaisesRegex(ValueError, "Unmatched text delimiter '/'"):
            # The parser finds options, then sees "1-5 /"
            # It sees "/" as the delimiter, but first_pos == last_pos
            _split_spec_string("1-5 / (options)")

        with self.assertRaisesRegex(ValueError, "Unmatched text delimiter '!'"):
            # Same, but with no options
            _split_spec_string("1-5 !")

    def test_parse_options_string(self):
        """Test the parsing of the (key=value) options block."""
        spec = "(position=top-left, font=Helvetica, size=12, rotate=-90)"
        expected = {
            "position": "top-left",
            "font": "Helvetica",
            "size": 12.0,
            "rotate": -90.0,
        }
        self.assertEqual(_parse_options_string(spec), expected)

    def test_parse_options_with_quotes_and_spaces(self):
        spec = "(font=Times New Roman, color=0.5 0.5 0.5, align=center)"
        expected = {
            "font": "Times New Roman",
            "color": [0.5, 0.5, 0.5, 1],
            "align": "center",
        }
        self.assertEqual(_parse_options_string(spec), expected)

    def test_parse_options_dimensions(self):
        spec = "(x=1cm, y=2in, offset-x=10, offset-y=50%)"
        cm_in_pt = 1.0 * (72.0 / 2.54)
        in_in_pt = 2.0 * 72.0
        expected = {
            "x": {"type": "pt", "value": cm_in_pt},
            "y": {"type": "pt", "value": in_in_pt},
            "offset-x": {"type": "pt", "value": 10.0},
            "offset-y": {"type": "%", "value": 50.0},
        }
        self.assertEqual(_parse_options_string(spec), expected)

    def test_parse_options_fail_unknown(self):
        with self.assertRaisesRegex(ValueError, "Unknown options: foo"):
            _parse_options_string("(foo=bar)")

    def test_parse_options_fail_position_and_xy(self):
        with self.assertRaisesRegex(ValueError, "Cannot specify both 'position' and 'x'"):
            _parse_options_string("(position=top-left, x=10)")

    def test_parse_options_fail_unmatched_parens(self):
        with self.assertRaisesRegex(ValueError, "Options block must be enclosed"):
            _parse_options_string("(options")

    def test_parse_options_fail_invalid_format(self):
        """
        Test for options that are not valid key=value pairs, which the
        original re.findall() logic would silently ignore.
        This test will FAIL until the parser is fixed.
        """
        # Test the case you found
        with self.assertRaisesRegex(ValueError, "Invalid option format: 'foo'"):
            _parse_options_string("(foo)")

        # Test a mix of valid and invalid
        with self.assertRaisesRegex(ValueError, "Invalid option format: 'foo'"):
            _parse_options_string("(foo, size=10)")

        with self.assertRaisesRegex(ValueError, "Invalid option format: 'bar'"):
            _parse_options_string("(size=10, bar)")

        # Test a missing key
        with self.assertRaisesRegex(ValueError, "Option missing key: '=bar'"):
            _parse_options_string("(=bar)")

        # Test a missing key in a list
        with self.assertRaisesRegex(ValueError, "Option missing key: '=bar'"):
            _parse_options_string("(size=10, =bar)")

    def test_variable_parsing_and_rendering(self):
        text_str = (
            "Page {page-1} of {total}. Report: {meta:Title}. File: {filename_base}. {{Literal}}"
        )
        render_fn = _compile_text_renderer(text_str)

        self.assertTrue(callable(render_fn))

        # Test with page 5
        context1 = {
            "page": 5,
            "total": 20,
            "filename_base": "doc",
            "metadata": {"Title": "My Report"},
        }
        self.assertEqual(
            render_fn(context1), "Page 4 of 20. Report: My Report. File: doc. {Literal}"
        )

        # Test with page 1
        context2 = {
            "page": 1,
            "total": 20,
            "filename_base": "doc",
            "metadata": {"Title": "My Report"},
        }
        self.assertEqual(
            render_fn(context2), "Page 0 of 20. Report: My Report. File: doc. {Literal}"
        )

        # Test complex var
        render_fn_complex = _compile_text_renderer("{total-page} pages left")
        self.assertEqual(render_fn_complex(context1), "15 pages left")

    # This is a new, explicit test for the logic in _evaluate_token
    def test_variable_renderer_fails_on_bad_arithmetic(self):
        # The parser *should* fail (correctly) when it sees math
        # on a non-numeric variable.
        with self.assertRaisesRegex(ValueError, "Cannot apply arithmetic"):
            _compile_text_renderer("File: {filename-1}")

    def test_parse_specs_simple(self):
        """Test the main function with a simple spec."""
        specs = ["/Hello/ (position=top-left, size=10)"]
        rules = parse_add_text_specs_to_rules(specs, self.total_pages)

        # Rule should apply to all 20 pages (indices 0-19)
        self.assertEqual(len(rules), 20)

        # Check rule for page 0
        rule = rules[0][0]
        self.assertTrue(callable(rule["text"]))
        self.assertEqual(rule["text"](self.context), "Hello")
        self.assertEqual(rule["position"], "top-left")
        self.assertEqual(rule["size"], 10.0)

        # Check rule for page 19
        rule = rules[19][0]
        self.assertTrue(callable(rule["text"]))
        self.assertEqual(rule["text"](self.context), "Hello")

    def test_parse_specs_page_ranges(self):
        specs = [
            "1 /First Page/ (size=10)",
            "2-5 /Some Pages/ (size=12)",
            "11-10 /Reversed/ (size=14)",  # Should parse as 11, 10
        ]
        rules = parse_add_text_specs_to_rules(specs, self.total_pages)

        # Was 6, but pages 1, 2, 3, 4, 5, 10, 11 is 7 pages.
        self.assertEqual(len(rules), 7)
        self.assertTrue(callable(rules[0][0]["text"]))
        self.assertEqual(rules[0][0]["text"](self.context), "First Page")
        self.assertEqual(rules[1][0]["text"](self.context), "Some Pages")
        self.assertEqual(rules[4][0]["text"](self.context), "Some Pages")
        self.assertEqual(rules[9][0]["text"](self.context), "Reversed")
        self.assertEqual(rules[10][0]["text"](self.context), "Reversed")
        self.assertNotIn(6, rules)

    def test_parse_specs_qualifiers(self):
        # This test now uses the correct syntax that the parser supports.
        # The parser does NOT handle "odd 1-10...", but "1-10odd..."
        specs = ["1-10odd /Odd Pages/ (size=10)", "even /Even Pages/ (size=12)"]
        rules = parse_add_text_specs_to_rules(specs, self.total_pages)

        # 1-10 odd: 1, 3, 5, 7, 9 (indices 0, 2, 4, 6, 8)
        # all even: 2, 4, 6, 8, 10, 12, 14, 16, 18, 20 (indices 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)
        self.assertEqual(len(rules), 15)
        self.assertEqual(rules[0][0]["text"](self.context), "Odd Pages")
        self.assertEqual(rules[1][0]["text"](self.context), "Even Pages")
        self.assertEqual(rules[2][0]["text"](self.context), "Odd Pages")

        # rules[3] is page 4, which is ONLY even.
        self.assertEqual(len(rules[3]), 1)
        self.assertEqual(rules[3][0]["text"](self.context), "Even Pages")

        self.assertEqual(rules[8][0]["text"](self.context), "Odd Pages")
        self.assertEqual(rules[19][0]["text"](self.context), "Even Pages")
        self.assertNotIn(10, rules)  # Page 11 is odd, but not in 1-10

    def test_parse_specs_multiple_rules_on_page(self):
        specs = [
            "1 /Hello/ (position=top-left)",
            "1 /World/ (position=bottom-right)",
        ]
        rules = parse_add_text_specs_to_rules(specs, self.total_pages)

        self.assertEqual(len(rules), 1)
        self.assertEqual(len(rules[0]), 2)

        self.assertEqual(rules[0][0]["text"](self.context), "Hello")
        self.assertEqual(rules[0][0]["position"], "top-left")

        self.assertEqual(rules[0][1]["text"](self.context), "World")
        self.assertEqual(rules[0][1]["position"], "bottom-right")

    def test_parse_specs_grouped_qualifiers(self):
        # 'even' applies to the next spec
        specs = ["even", "1-5 /Even 1-5/ (size=10)", "/All/ (size=12)"]
        rules = parse_add_text_specs_to_rules(specs, self.total_pages)

        # 'even 1-5' -> 2, 4 (indices 1, 3)
        # 'All' -> 1-20 (indices 0-19)
        self.assertEqual(len(rules), 20)
        self.assertEqual(rules[0][0]["text"](self.context), "All")  # Page 1

        # Page 2 (index 1) has both
        self.assertEqual(len(rules[1]), 2)
        self.assertEqual(rules[1][0]["text"](self.context), "Even 1-5")
        self.assertEqual(rules[1][1]["text"](self.context), "All")

        # Page 3 (index 2) has 'All'
        self.assertEqual(len(rules[2]), 1)
        self.assertEqual(rules[2][0]["text"](self.context), "All")

        # Page 4 (index 3) has both
        self.assertEqual(len(rules[3]), 2)
        self.assertEqual(rules[3][0]["text"](self.context), "Even 1-5")
        self.assertEqual(rules[3][1]["text"](self.context), "All")

    def test_parse_fail_missing_spec_after_qualifier(self):
        with self.assertRaisesRegex(ValueError, "Missing spec after 'even'"):
            parse_add_text_specs_to_rules(["even"], self.total_pages)

    def test_parse_fail_invalid_spec_syntax(self):
        with self.assertRaisesRegex(ValueError, "Invalid add_text spec"):
            # This will fail at _split_spec_string with "Invalid text delimiter 'D'"
            parse_add_text_specs_to_rules(["1 /Missing Delim"], self.total_pages)


from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis.errors import InvalidArgument

# --- Strategies for generating valid-looking inputs ---
# A valid non-alphanumeric delimiter
st_delimiter = st.characters(min_codepoint=33, max_codepoint=126).filter(
    lambda c: not c.isalnum() and c not in "()'"
)

# Text string that won't contain the delimiter (usually)
st_text_content = st.text(
    st.characters(min_codepoint=32, max_codepoint=126),
    min_size=0,
    max_size=50,
).filter(lambda s: "(" not in s and ")" not in s and "{" not in s)

st_page_range = st.one_of(
    st.just(""),  # default
    st.just("1-10"),
    st.just("1"),
    st.just("5-10"),
    st.just("1-end"),
    st.just("even"),
    st.just("odd"),
    st.just("1-10even"),
    st.just("1-10odd"),
)

# --- Option Strategies ---
st_font = st.builds(
    lambda v: f"font={v}",
    st.one_of(st.just("Helvetica"), st.just("'Times New Roman'")),
)
st_size = st.builds(
    lambda v: f"size={v}",
    st.floats(min_value=1, max_value=100, allow_nan=False, allow_infinity=False),
)
st_align = st.builds(
    lambda v: f"align={v}",
    st.one_of(st.just("left"), st.just("center"), st.just("right")),
)
st_rotate = st.builds(
    lambda v: f"rotate={v}",
    st.floats(min_value=-360, max_value=360, allow_nan=False, allow_infinity=False),
)

st_pos_preset = st.just(
    f"position={st.one_of(st.sampled_from(pdftl.commands.parsers.add_text_parser.PRESET_POSITIONS))}"
)

st_dim_floats = st.floats(min_value=0, max_value=1000, allow_nan=False, allow_infinity=False)
st_unit = st.one_of(st.just("pt"), st.just("cm"), st.just("in"), st.just("%"), st.just(""))
st_dim_str = st.builds(lambda v, u: f"{v}{u}", st_dim_floats, st_unit)

st_pos_xy = st.builds(lambda x, y: f"x={x}, y={y}", st_dim_str, st_dim_str)

st_offsets = st.builds(lambda x, y: f"offset-x={x}, offset-y={y}", st_dim_str, st_dim_str)

# st_color_named = st.builds(
#     lambda v: f"color={v}",
#     st.one_of(st.just('red'), st.just('blue'))
# )
st_color_gray = st.just("color=0.1")
st_color_rgb = st.just("color=0.1 0.2 0.3")
st_color_rgba = st.just("color=0.1 0.2 0.3 0.4")

st_option = st.one_of(
    st_font,
    st_size,
    st_align,
    st_rotate,
    st_color_gray,
    st_color_rgb,
    st_color_rgba,
    st_offsets,
)


# A strategy for a list of options, ensuring position/xy are mutually exclusive
@st.composite
def st_options_list(draw):
    # Base options
    options = draw(st.lists(st_option, min_size=0, max_size=4, unique=True))

    # Add positioning
    if draw(st.booleans()):
        options.append(draw(st_pos_preset))
    else:
        options.append(draw(st_pos_xy))

    return options


# Strategy for the full options string "(key=value, ...)"
@st.composite
def st_options_string(draw):
    options = draw(st_options_list())
    if not options:
        return "()"
    return f"({', '.join(options)})"


# --- Full Spec Strategy ---
@st.composite
def st_full_spec(draw):
    page = draw(st_page_range)
    delim = draw(st_delimiter)
    # Ensure text doesn't contain the chosen delimiter
    text = draw(
        st_text_content.filter(
            lambda s: delim not in s and not s.startswith(" ") and not s.endswith(" ")
        )
    )
    opts = draw(st_options_string())

    # Build the string without fussy spaces
    page_part = page
    # Add a space only if page part is not empty
    if page:
        page_part = page + " "

    return f"{page_part}{delim}{text}{delim} {opts}"


# --- Strategies for INVALID specs ---

st_invalid_options = st.one_of(
    st.just("(size=foo)"),  # Not a float
    st.just("(rotate=bar)"),  # Not a float
    st.just("(position=top-left, x=10)"),  # Conflicting
    st.just("(unknown=key)"),  # Unknown key
    st.just("(position=top-left"),  # Missing paren
    st.just("position=top-left)"),  # Missing paren
    st.just("(align=middle)"),  # Invalid align value
    st.just("(position=top)"),  # Invalid position value
)

st_invalid_variables = st.one_of(
    st.just("{page+foo}"),  # Non-numeric math
    st.just("{meta:Title+1}"),  # Math on meta
    st.just("{foo}"),  # Unknown variable
    st.just("{page-bar}"),  # Non-numeric math
    st.just("{page*1}"),  # Invalid operator
)

st_invalid_structure = st.one_of(
    st.just("1 /no-end-delim (size=10)"),  # Unmatched delimiter
    st.just("1 /text/ no-parens-options"),  # Options not in parens
    st.just("1 bad-delimiter text / (options)"),  # Alphanumeric delimiter
)


@st.composite
def st_invalid_specs(draw):
    """Builds a full, invalid spec string."""
    base_spec = draw(st_full_spec())
    parts = base_spec.split(" ")

    case = draw(st.integers(0, 2))
    if case == 0:  # Replace options
        invalid_opts = draw(st_invalid_options)
        return f"1 /text/ {invalid_opts}"
    elif case == 1:  # Replace text
        invalid_text = draw(st_invalid_variables)
        return f"1 /{invalid_text}/ (size=10)"
    else:  # Replace structure
        return draw(st_invalid_structure)


@pytest.mark.slow
class TestAddTextParserHypothesis(unittest.TestCase):
    """Property-based tests for robustness."""

    @given(spec=st_full_spec())
    @settings(max_examples=200, deadline=None)
    def test_parser_does_not_crash_on_valid_input(self, spec):
        """Test that the parser can handle a wide variety of
        valid-looking inputs without raising an unhandled exception.
        """
        try:
            parse_add_text_specs_to_rules([spec], total_pages=20)
        except ValueError:
            # ValueErrors are expected for some generated inputs
            # (e.g., if text filtering fails)
            pass
        except InvalidArgument:
            # Hypothesis can throw this
            pass
        except Exception as e:
            # Catch any other unexpected exceptions
            self.fail(f"Parser crashed on spec: '{spec}'\nError: {e}")

    @given(specs=st.lists(st_full_spec(), min_size=1, max_size=5))
    @settings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.filter_too_much],
    )
    def test_parser_returns_dict_with_list_values(self, specs):
        """Test that the parser's return structure is correct."""
        try:
            rules = parse_add_text_specs_to_rules(specs, total_pages=20)

            self.assertIsInstance(rules, dict)
            for page_index, rule_list in rules.items():
                self.assertIsInstance(page_index, int)
                self.assertIsInstance(rule_list, list)
                self.assertTrue(page_index >= 0 and page_index < 20)
                for rule in rule_list:
                    self.assertIsInstance(rule, dict)
                    self.assertIn("text", rule)
                    # Test the new structure: 'text' is a function
                    self.assertTrue(callable(rule["text"]))

        except ValueError:
            pass  # Expected
        except InvalidArgument:
            pass
        except Exception as e:
            # Catch any other unexpected exceptions
            self.fail(f"Parser crashed on specs: '{specs}'\nError: {e}")

    @given(invalid_spec=st_invalid_specs())
    @settings(max_examples=200, deadline=None)
    def test_parser_raises_valueerror_on_invalid_syntax(self, invalid_spec):
        """
        Tests that the parser correctly raises ValueError for
        syntax that is known to be invalid.
        """
        with self.assertRaises(ValueError):
            parse_add_text_specs_to_rules([invalid_spec], total_pages=10)


if __name__ == "__main__":
    unittest.main()
