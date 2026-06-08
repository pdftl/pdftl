# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
Unit tests for the add_text_parser module.
Requires 'pytest' and 'hypothesis' to run.
"""

import unittest
from collections import namedtuple
from unittest.mock import patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis.errors import InvalidArgument

from pdftl.operations.parsers.add_text_parser import (  # Import new function for testing
    PRESET_POSITIONS,
    _normalize_formatting,
    _normalize_options,
    _parse_options_content,
    _parse_options_string,
    _split_spec_string,
    parse_add_text_specs_to_rules,
)
from pdftl.utils.text_templates import (
    _evaluate_token,
    _parse_var_expression,
    compile_text_renderer,
    tokenize_text_string,
)


def _render_text(rule_or_fn, context):
    """Extracts plain text from a renderer result (list of runs)."""
    fn = rule_or_fn["text"] if isinstance(rule_or_fn, dict) else rule_or_fn
    return "".join(text for text, _ in fn(context))


# --- Mocks for dependencies ---
# We mock the external dependencies of add_text_parser for isolated testing.

# Mock pdftl.core.constants
# 1 cm = 72 (pts/in) / 2.54 (cm/in) = 28.346...
UNITS = {"pt": 1.0, "in": 72.0, "cm": 72.0 / 2.54}

# Mock pdftl.utils.page_specs
# A simple mock of parse_sub_page_spec to return what the parser expects.
MockPageSpec = namedtuple("MockPageSpec", ["start", "end", "step", "qualifiers", "omissions"])

st_pos_preset = st.sampled_from([f"position={p}" for p in PRESET_POSITIONS])

st_delimiter = st.sampled_from(list("!@#$%^&*-+=:?/|"))

TEXT_ALPHABET = st.characters(min_codepoint=32, max_codepoint=126, blacklist_characters="(){}")


@st.composite
def st_text_content(draw, forbidden=""):
    return draw(st.text(TEXT_ALPHABET, min_size=0, max_size=50))


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
    options = draw(st.lists(st_option, min_size=0, max_size=3))

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
    text = draw(st_text_content(forbidden=delim))
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

MockPageSpec = namedtuple("MockPageSpec", ["start", "end", "step", "qualifiers", "omissions"])


def mock_parse_sub_page_spec(page_range_part, total_pages):
    """Mock implementation of parse_sub_page_spec."""
    qualifiers = None
    if page_range_part.endswith("even"):
        qualifiers = "even"
        page_range_part = page_range_part[:-4]
    elif page_range_part.endswith("odd"):
        qualifiers = "odd"
        page_range_part = page_range_part[:-3]

    if qualifiers and isinstance(qualifiers, str):
        qualifiers = {qualifiers}
    elif qualifiers is None:
        qualifiers = set()

    if not page_range_part or page_range_part == "1-end":
        return MockPageSpec(1, total_pages, 1, qualifiers, [])

    if page_range_part == "even":
        return MockPageSpec(1, total_pages, 1, {"even"}, [])
    if page_range_part == "odd":
        return MockPageSpec(1, total_pages, 1, {"odd"}, [])

    if "-" in page_range_part:
        start_str, end_str = page_range_part.split("-")
        start = 1 if not start_str else int(start_str)
        end = total_pages if end_str == "end" or not end_str else int(end_str)
        return MockPageSpec(start, end, 1, qualifiers, [])

    try:
        page_num = int(page_range_part)
        return MockPageSpec(page_num, page_num, 1, qualifiers, [])
    except ValueError:
        raise


def test_parse_options_content_error():
    with pytest.raises(ValueError):
        _parse_options_content("key='foo")


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
        self.patcher_units = patch("pdftl.operations.parsers.add_text_parser.UNITS", UNITS)
        self.patcher_pages = patch(
            "pdftl.utils.page_specs.parse_sub_page_spec", mock_parse_sub_page_spec
        )

        self.patcher_units.start()
        self.patcher_pages.start()

        # Ensure we clean up after the test
        self.addCleanup(self.patcher_units.stop)
        self.addCleanup(self.patcher_pages.stop)

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
        render_fn = compile_text_renderer(text_str)

        self.assertTrue(callable(render_fn))

        # Test with page 5
        context1 = {
            "page": 5,
            "total": 20,
            "filename_base": "doc",
            "metadata": {"Title": "My Report"},
        }
        self.assertEqual(
            _render_text(render_fn, context1),
            "Page 4 of 20. Report: My Report. File: doc. {Literal}",
        )

        # Test with page 1
        context2 = {
            "page": 1,
            "total": 20,
            "filename_base": "doc",
            "metadata": {"Title": "My Report"},
        }
        self.assertEqual(
            _render_text(render_fn, context2),
            "Page 0 of 20. Report: My Report. File: doc. {Literal}",
        )

        # Test complex var
        render_fn_complex = compile_text_renderer("{total-page} pages left")
        self.assertEqual(_render_text(render_fn_complex, context1), "15 pages left")

    # This is a new, explicit test for the logic in _evaluate_token
    def test_variable_renderer_fails_on_bad_arithmetic(self):
        # The parser *should* fail (correctly) when it sees math
        # on a non-numeric variable.
        with self.assertRaisesRegex(ValueError, "Cannot apply arithmetic"):
            compile_text_renderer("File: {filename-1}")

    def test_parse_specs_simple(self):
        """Test the main function with a simple spec."""
        specs = ["/Hello/ (position=top-left, size=10)"]
        rules = parse_add_text_specs_to_rules(specs, self.total_pages)

        # Rule should apply to all 20 pages (indices 0-19)
        self.assertEqual(len(rules), 20)

        # Check rule for page 0
        rule = rules[0][0]
        self.assertTrue(callable(rule["text"]))
        self.assertEqual(_render_text(rule, self.context), "Hello")
        self.assertEqual(rule["position"], "top-left")
        self.assertEqual(rule["size"], 10.0)

        # Check rule for page 19
        rule = rules[19][0]
        self.assertTrue(callable(rule["text"]))
        self.assertEqual(_render_text(rule, self.context), "Hello")

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
        self.assertEqual(_render_text(rules[0][0], self.context), "First Page")
        self.assertEqual(_render_text(rules[1][0], self.context), "Some Pages")
        self.assertEqual(_render_text(rules[4][0], self.context), "Some Pages")
        self.assertEqual(_render_text(rules[9][0], self.context), "Reversed")
        self.assertEqual(_render_text(rules[10][0], self.context), "Reversed")
        self.assertNotIn(6, rules)

    def test_parse_specs_qualifiers(self):
        # This test now uses the correct syntax that the parser supports.
        # The parser does NOT handle "odd 1-10...", but "1-10odd..."
        specs = ["1-10odd /Odd Pages/ (size=10)", "even /Even Pages/ (size=12)"]
        rules = parse_add_text_specs_to_rules(specs, self.total_pages)

        # 1-10 odd: 1, 3, 5, 7, 9 (indices 0, 2, 4, 6, 8)
        # all even: 2, 4, 6, 8, 10, 12, 14, 16, 18, 20 (indices 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)
        self.assertEqual(len(rules), 15)
        self.assertEqual(_render_text(rules[0][0], self.context), "Odd Pages")
        self.assertEqual(_render_text(rules[1][0], self.context), "Even Pages")
        self.assertEqual(_render_text(rules[2][0], self.context), "Odd Pages")

        # rules[3] is page 4, which is ONLY even.
        self.assertEqual(len(rules[3]), 1)
        self.assertEqual(_render_text(rules[3][0], self.context), "Even Pages")

        self.assertEqual(_render_text(rules[8][0], self.context), "Odd Pages")
        self.assertEqual(_render_text(rules[19][0], self.context), "Even Pages")
        self.assertNotIn(10, rules)  # Page 11 is odd, but not in 1-10

    def test_parse_specs_multiple_rules_on_page(self):
        specs = [
            "1 /Hello/ (position=top-left)",
            "1 /World/ (position=bottom-right)",
        ]
        rules = parse_add_text_specs_to_rules(specs, self.total_pages)

        self.assertEqual(len(rules), 1)
        self.assertEqual(len(rules[0]), 2)

        self.assertEqual(_render_text(rules[0][0], self.context), "Hello")
        self.assertEqual(rules[0][0]["position"], "top-left")

        self.assertEqual(_render_text(rules[0][1], self.context), "World")
        self.assertEqual(rules[0][1]["position"], "bottom-right")

    def test_parse_specs_grouped_qualifiers(self):
        # 'even' applies to the next spec
        specs = ["1-5even /Even 1-5/ (size=10)", "/All/ (size=12)"]
        rules = parse_add_text_specs_to_rules(specs, self.total_pages)

        # 'even 1-5' -> 2, 4 (indices 1, 3)
        # 'All' -> 1-20 (indices 0-19)
        self.assertEqual(len(rules), 20)
        self.assertEqual(_render_text(rules[0][0], self.context), "All")  # Page 1

        # Page 2 (index 1) has both
        self.assertEqual(len(rules[1]), 2)
        self.assertEqual(_render_text(rules[1][0], self.context), "Even 1-5")
        self.assertEqual(_render_text(rules[1][1], self.context), "All")

        # Page 3 (index 2) has 'All'
        self.assertEqual(len(rules[2]), 1)
        self.assertEqual(_render_text(rules[2][0], self.context), "All")

        # Page 4 (index 3) has both
        self.assertEqual(len(rules[3]), 2)
        self.assertEqual(_render_text(rules[3][0], self.context), "Even 1-5")
        self.assertEqual(_render_text(rules[3][1], self.context), "All")

    def test_parse_fail_missing_spec_after_qualifier(self):
        with self.assertRaisesRegex(ValueError, "Invalid add_text spec.*delimiter"):
            parse_add_text_specs_to_rules(["even"], self.total_pages)

    def test_parse_fail_invalid_spec_syntax(self):
        with self.assertRaisesRegex(ValueError, "Invalid add_text spec"):
            # This will fail at _split_spec_string with "Invalid text delimiter 'D'"
            parse_add_text_specs_to_rules(["1 /Missing Delim"], self.total_pages)


@st.composite
def st_invalid_specs(draw):
    """Builds a full, invalid spec string."""
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


class TestAddTextFiltering(unittest.TestCase):
    def test_line_135_omissions(self):
        """Tests filtering via page range omissions '~'"""
        # Spec: range 1 to 5, but omit page 3
        specs = ["1-5~3/Omit Test/"]
        total_pages = 10

        # Result should contain 0, 1, 3, 4 (Pages 1, 2, 4, 5)
        # 2 (Page 3) should be missing
        rules = parse_add_text_specs_to_rules(specs, total_pages)

        self.assertIn(0, rules)
        self.assertIn(1, rules)
        self.assertNotIn(2, rules)  # Page 3 omitted
        self.assertIn(3, rules)
        self.assertIn(4, rules)
        self.assertEqual(len(rules), 4)


class TestMiscAddTextParser(unittest.TestCase):
    def test_parse_color_variants(self):
        """Covers different branches in _parse_color."""
        from pdftl.operations.parsers.add_text_parser import _parse_color

        # Grayscale (1 part) -> RGBA (Line 408-410)
        self.assertEqual(_parse_color("0.5"), [0.5, 0.5, 0.5, 1])

        # RGBA (4 parts) (Line 414-415)
        self.assertEqual(_parse_color("0.1 0.2 0.3 0.5"), [0.1, 0.2, 0.3, 0.5])

        # Error: Invalid character (Line 404-405)
        with self.assertRaises(ValueError):
            _parse_color("red green blue")

        # Error: Invalid part count (Line 417-420)
        with self.assertRaisesRegex(ValueError, "must have 1, 3, or 4"):
            _parse_color("0.1 0.2")

    def test_variable_expression_errors(self):
        """Covers unknown variables, bad arithmetic, and bad formatting."""

        # Unknown variable (Line 445-446)
        with self.assertRaisesRegex(ValueError, "Unknown variable"):
            _parse_var_expression("not_a_var")

        # Arithmetic on non-numeric (Line 455-456)
        with self.assertRaisesRegex(ValueError, "non-numeric variable"):
            _parse_var_expression("filename+1")

        # Metadata parsing (Line 437-438)
        token = _parse_var_expression("meta:Author")
        self.assertEqual(token[0], "meta:Author")

        # Evaluate metadata (Line 475-477)
        res = _evaluate_token(token, {"metadata": {"Author": "Gemini"}})
        self.assertEqual(res, "Gemini")

        # Total-page logic (Line 472-473)
        token_tp = _parse_var_expression("total-page")
        res_tp = _evaluate_token(token_tp, {"total": 10, "page": 3})
        self.assertEqual(res_tp, 7)

        # Formatting error during evaluation (Line 499-501)
        # Using a string formatter 'd' on a string value
        token_fmt = ("filename", "master", (0, "d"))
        with self.assertRaises(ValueError):
            _evaluate_token(token_fmt, {"filename": "test.pdf"})

    def test_split_and_dimension_failures(self):
        """Covers empty specs and malformed dimensions."""
        # Empty spec (Line 170-171)
        with self.assertRaisesRegex(ValueError, "Empty add_text spec"):
            _split_spec_string("   ")

        # Missing text component (Line 176-177)
        # This happens if there is only an options block
        with self.assertRaisesRegex(ValueError, "Missing text string"):
            _split_spec_string("(size=10)")

        # Malformed dimension values (Line 380, 388, 393)
        from pdftl.operations.parsers.add_text_parser import _parse_dimension

        with self.assertRaises(ValueError):
            _parse_dimension("abc%")
        with self.assertRaises(ValueError):
            _parse_dimension("abcpt")
        with self.assertRaises(ValueError):
            _parse_dimension("not_a_number")

    def test_position_center_alias(self):
        """Covers line 324: 'center' mapping to 'mid-center'."""
        from pdftl.operations.parsers.add_text_parser import _parse_options_string

        spec = "(position=center)"
        result = _parse_options_string(spec)
        # Verify 'center' was normalized to 'mid-center'
        self.assertEqual(result["position"], "mid-center")

    def test_link_color_option(self):
        """Covers line 365: 'linkcolor' parsing."""
        from pdftl.operations.parsers.add_text_parser import _parse_options_string

        spec = "(linkcolor=0 0 1)"  # Blue
        result = _parse_options_string(spec)
        self.assertEqual(result["linkcolor"], [0.0, 0.0, 1.0, 1])

    def test_markdown_link_rendering(self):
        """Covers lines 545-552, 560-568, and 585: Markdown links with variables."""
        from pdftl.utils.text_templates import compile_text_renderer

        # A string with literal text and a Markdown link containing a variable
        text_str = "Visit [Page {page}](http://example.com/p{page})"
        renderer = compile_text_renderer(text_str)

        context = {"page": 5}
        runs = renderer(context)

        # Expected output:
        # 1. "Visit " (Plain text)
        # 2. ("Page 5", "http://example.com/p5") (The link run)

        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0], ("Visit ", None))
        self.assertEqual(runs[1], ("Page 5", "http://example.com/p5"))

    def test_render_parts_to_string_direct(self):
        """Directly covers line 560-568 by invoking the default renderer logic."""
        from pdftl.utils.text_templates import (
            render_parts_to_string,
            tokenize_text_string,
        )

        # Create a complex token structure with a nested link
        text_str = "[Go {page}](url)"
        parts = tokenize_text_string(text_str)

        # default_renderer is used when we need a plain string version
        # of a segment (e.g., to generate the final URL string)
        plain_text = render_parts_to_string(parts, {"page": 1})
        self.assertEqual(plain_text, "Go 1")


def test_normalize_formatting_bgcolor_and_padding():
    """Test that bgcolor and padding are normalized."""
    options = {"bgcolor": "1 0 0 .5", "padding": "10pt", "color": "0 0 0"}
    normalized = {}

    with (
        patch(
            "pdftl.operations.parsers.add_text_parser._parse_color",
            return_value=(1.0, 0.0, 0.0, 0.5),
        ) as mock_color,
        patch(
            "pdftl.operations.parsers.add_text_parser._parse_dimension",
            return_value=10.0,
        ) as mock_dim,
    ):
        _normalize_formatting(options, normalized)

        assert "bgcolor" not in options
        assert "padding" not in options

        assert normalized["bgcolor"] == (1.0, 0.0, 0.0, 0.5)
        assert normalized["padding"] == 10.0

        mock_color.assert_any_call("1 0 0 .5")
        mock_color.assert_any_call("0 0 0")
        assert mock_color.call_count == 2
        mock_dim.assert_called_once_with("10pt")


def test_normalize_formatting_defaults():
    """Test that missing bgcolor and padding don't cause errors."""
    options = {"size": "12"}
    normalized = {}

    _normalize_formatting(options, normalized)

    assert "bgcolor" not in normalized
    assert "padding" not in normalized
    assert normalized.get("size") == 12.0 or "size" not in normalized


# --- merged from test_add_text_parser_coverage.py ---

# Assume the module being tested is imported as 'parser'
# from pdftl.operations.parsers import add_text_parser as parser

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


@patch("pdftl.operations.parsers.add_text_parser.UNITS", MOCKED_UNITS, create=True)
class TestAddTextParser2:
    # =========================================================================
    # Test _split_spec_string (Covers Lines: 145, 174)
    # =========================================================================
    @patch(
        "pdftl.operations.parsers.add_text_parser._split_spec_string",
        wraps=__import__(
            "pdftl.operations.parsers.add_text_parser"
        ).operations.parsers.add_text_parser._split_spec_string,
    )
    def test_split_spec_string_raises_on_empty_spec(self, mock_split):
        """Covers line 145: raise ValueError("Empty add_text spec")"""
        with pytest.raises(ValueError, match="Empty add_text spec"):
            mock_split("")

    @patch(
        "pdftl.operations.parsers.add_text_parser._split_spec_string",
        wraps=__import__(
            "pdftl.operations.parsers.add_text_parser"
        ).operations.parsers.add_text_parser._split_spec_string,
    )
    def test_split_spec_string_raises_on_only_options_block(self, mock_split):
        """Covers line 174: raise ValueError("Missing text string component")"""
        # A spec that only contains an options block, leaving rest_of_spec empty.
        with pytest.raises(ValueError, match="Missing text string component"):
            mock_split("()")

    # =========================================================================
    # Test _parse_options_string (Covers Lines: 234, 242-243, 248, 255)
    # =========================================================================

    @patch("pdftl.operations.parsers.add_text_parser._normalize_options", return_value={})
    @patch(
        "pdftl.operations.parsers.add_text_parser._parse_options_string",
        wraps=__import__(
            "pdftl.operations.parsers.add_text_parser"
        ).operations.parsers.add_text_parser._parse_options_string,
    )
    def test_parse_options_string_empty_parentheses(self, mock_parse, mock_normalize):
        """Covers line 234: return {}"""
        assert mock_parse("()") == {}
        mock_normalize.assert_not_called()

    @patch(
        "pdftl.operations.parsers.add_text_parser._parse_options_string",
        wraps=__import__(
            "pdftl.operations.parsers.add_text_parser"
        ).operations.parsers.add_text_parser._parse_options_string,
    )
    def test_parse_options_string_invalid_option_format(self, mock_parse):
        """Covers line 255: raise ValueError for invalid key/value format"""
        # Old input: "(key='value, value, key2=value2)" relied on regex failure
        # New input: Just use a string that definitely has no '='

        with pytest.raises(ValueError, match="Invalid option format: 'just_a_value'"):
            mock_parse("(just_a_value)")

    @patch(
        "pdftl.operations.parsers.add_text_parser._normalize_options",
        return_value={"font": "Arial", "size": {"type": "pt", "value": 12.0}},
    )
    @patch(
        "pdftl.operations.parsers.add_text_parser._parse_options_string",
        wraps=__import__(
            "pdftl.operations.parsers.add_text_parser"
        ).operations.parsers.add_text_parser._parse_options_string,
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
        "pdftl.operations.parsers.add_text_parser._parse_dimension",
        wraps=__import__(
            "pdftl.operations.parsers.add_text_parser"
        ).operations.parsers.add_text_parser._parse_dimension,
    )
    def test_parse_dimension_already_parsed(self, mock_parse):
        """Covers line 353: return size_str (Already parsed, e.g., from a test)"""
        pre_parsed = {"type": "%", "value": 50.0}
        assert mock_parse(pre_parsed) is pre_parsed

    @patch(
        "pdftl.operations.parsers.add_text_parser._parse_dimension",
        wraps=__import__(
            "pdftl.operations.parsers.add_text_parser"
        ).operations.parsers.add_text_parser._parse_dimension,
    )
    def test_parse_dimension_invalid_percentage(self, mock_parse):
        """Covers lines 359-360: try/except for percentage float conversion"""
        with pytest.raises(ValueError, match="Invalid percentage value: '50a%'"):
            mock_parse("50a%")

    @patch(
        "pdftl.operations.parsers.add_text_parser._parse_dimension",
        wraps=__import__(
            "pdftl.operations.parsers.add_text_parser"
        ).operations.parsers.add_text_parser._parse_dimension,
    )
    def test_parse_dimension_invalid_unit_value(self, mock_parse):
        """Covers lines 368-369: try/except for unit value float conversion"""
        # Use a mocked unit ('pt') which is found via _find_unit
        with pytest.raises(ValueError, match="Invalid size value: '10bpt'"):
            mock_parse("10bpt")

    @patch(
        "pdftl.operations.parsers.add_text_parser._parse_dimension",
        wraps=__import__(
            "pdftl.operations.parsers.add_text_parser"
        ).operations.parsers.add_text_parser._parse_dimension,
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
        "pdftl.operations.parsers.add_text_parser._parse_color",
        wraps=__import__(
            "pdftl.operations.parsers.add_text_parser"
        ).operations.parsers.add_text_parser._parse_color,
    )
    def test_parse_color_invalid_characters(self, mock_parse):
        """Covers lines 395-397: try/except for float conversion of parts"""
        # Contains non-numeric characters: 'a'
        with pytest.raises(ValueError, match="Invalid characters in color string: '1 0 a'"):
            mock_parse("1 0 a")

    @patch(
        "pdftl.operations.parsers.add_text_parser._parse_color",
        wraps=__import__(
            "pdftl.operations.parsers.add_text_parser"
        ).operations.parsers.add_text_parser._parse_color,
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
        "pdftl.utils.text_templates._evaluate_token",
        wraps=__import__("pdftl.utils.text_templates").utils.text_templates._evaluate_token,
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
        "pdftl.utils.text_templates._evaluate_token",
        wraps=__import__("pdftl.utils.text_templates").utils.text_templates._evaluate_token,
    )
    def test_evaluate_token_arithmetic_add(self, mock_evaluate):
        """Covers line 512: return base_value + val"""
        # OLD: token = ("page", "+", 5)
        # NEW: Offset is positive 5
        token = ("page", "master", (5, None))

        context = {"page": 10}  # Numeric value
        assert mock_evaluate(token, context) == 15

    @patch(
        "pdftl.utils.text_templates._evaluate_token",
        wraps=__import__("pdftl.utils.text_templates").utils.text_templates._evaluate_token,
    )
    def test_evaluate_token_arithmetic_sub(self, mock_evaluate):
        """Covers subtraction logic"""
        # OLD: token = ("page", "-", 2)
        # NEW: Subtraction is represented as a negative offset in the master token
        token = ("page", "master", (-2, None))

        context = {"page": 10}
        assert mock_evaluate(token, context) == 8


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
        parts = tokenize_text_string(input_str)

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
        renderer = compile_text_renderer(input_str)
        result = renderer({"page": 99})

        # Should render literal {page}, not the value 99
        assert "".join(t for t, _ in result) == "Value: {page}"

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


@pytest.mark.parametrize(
    "invalid_spec, expected_error_match",
    [
        # Syntax & Delimiter errors
        ("   ", "Empty add_text spec"),
        ("1-endAtextA", "Invalid text delimiter"),  # Alphanumeric delim
        ("1-end/text", "Invalid text delimiter"),  # Missing closing delim
        # Options formatting errors
        ("1-end/text/ x=10", "Invalid text delimiter"),  # Missing parens
        ("1-end/text/(=10)", "Option missing key"),  # Empty key
        ("1-end/text/(x)", "Invalid option format"),  # Missing '='
        ("1-end/text/(fake_key=10)", "Unknown options"),  # Unrecognized key
        # Type & Value errors in specific options
        ("1-end/text/(start=abc)", "must be an integer"),
        ("1-end/text/(position=center, x=10)", "Cannot specify both 'position' and 'x'/'y'"),
        ("1-end/text/(position=moon)", "Unknown position"),
        ("1-end/text/(rotate=abc)", "Invalid rotate value"),
        ("1-end/text/(size=abc)", "Invalid size value"),
        ("1-end/text/(align=top)", "Invalid align value"),
        # Dimension parsing errors
        ("1-end/text/(x=abc%)", "Invalid percentage value"),
        ("1-end/text/(x=abcpt)", "Invalid size value"),
        ("1-end/text/(x=abc)", "Invalid size or unit"),
        # Color parsing errors
        ("1-end/text/(color=red)", "Invalid characters in color string"),
        ("1-end/text/(color=1 1)", "must have 1, 3, or 4"),
    ],
)
def test_add_text_parser_fast_validation_errors(invalid_spec, expected_error_match):
    """Deterministically hit all ValueError branches without needing Hypothesis fuzzing."""
    with pytest.raises(ValueError, match=expected_error_match):
        parse_add_text_specs_to_rules([invalid_spec], total_pages=1)
