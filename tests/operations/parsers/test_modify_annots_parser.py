# tests/operations/parsers/test_modify_annots_parser.py

"""
Unit tests for the modify_annots_parser module.
Requires 'pytest' and 'hypothesis'.
"""

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

# We must import the module to test, aliased as 'map'
import pdftl.operations.parsers.modify_annots_parser as ma_parser

# --- -----------------------
# Tests for _unquote_string
# --- -----------------------


@pytest.mark.parametrize(
    "input_str, expected",
    [
        ("'foo'", "foo"),
        ('"bar"', "bar"),
        ("baz", "baz"),
        ("'mismatched\"", "'mismatched\""),
        ("''", ""),
        ('""', ""),
        ("", ""),
    ],
)
def test_unquote_string(input_str, expected):
    assert ma_parser._unquote_string(input_str) == expected


# --- -----------------------
# Tests for _parse_kv_pair
# --- -----------------------


@pytest.mark.parametrize(
    "input_str, expected_key, expected_val",
    [
        ("Border=null", "Border", "null"),
        (" T = (New Author) ", "T", "(New Author)"),
        ("Key='Value=with=equals'", "Key", "Value=with=equals"),
        ('Key="Value=with=equals"', "Key", "Value=with=equals"),
        ("Key=Value=with=equals", "Key", "Value=with=equals"),
        ("Key=", "Key", ""),
    ],
)
def test_parse_kv_pair_success(input_str, expected_key, expected_val):
    key, val = ma_parser._parse_kv_pair(input_str)
    assert key == expected_key
    assert val == expected_val


@pytest.mark.parametrize("input_str", ["NoEquals", " =NoKey", " "])
def test_parse_kv_pair_failure(input_str):
    with pytest.raises(ValueError):
        ma_parser._parse_kv_pair(input_str)


# --- -----------------------
# Tests for _parse_modification_string
# --- -----------------------


def test_parse_modification_string_simple():
    result = ma_parser._parse_modification_string("Border=null")
    assert result == [("Border", "null")]


def test_parse_modification_string_multiple():
    result = ma_parser._parse_modification_string("Border=null, Foo=bar, C=[1 0 0]")
    assert result == [("Border", "null"), ("Foo", "bar"), ("C", "[1 0 0]")]


def test_parse_modification_string_quotes_and_spaces():
    result = ma_parser._parse_modification_string(
        " T = 'Title, with comma' , Key=\"Value, with comma\" "
    )
    assert result == [("T", "Title, with comma"), ("Key", "Value, with comma")]


def test_parse_modification_string_trailing_comma():
    result = ma_parser._parse_modification_string("Key=Val,")
    assert result == [("Key", "Val")]


def test_parse_modification_string_empty_fails():
    with pytest.raises(ValueError, match="Empty modification list"):
        ma_parser._parse_modification_string("")


# --- -----------------------
# Tests for _parse_selector_string
# --- -----------------------


@pytest.mark.parametrize(
    "input_str, expected_page_spec, expected_type",
    [
        ("1-4/Link", "1-4", "/Link"),
        ("/Text", "1-end", "/Text"),
        ("odd", "odd", None),
        ("1-end", "1-end", None),
        ("", "1-end", None),
        ("1/Link", "1", "/Link"),
        ("even/Highlight", "even", "/Highlight"),
    ],
)
def test_parse_selector_string(input_str, expected_page_spec, expected_type):
    page_spec, type_spec = ma_parser._parse_selector_string(input_str)
    assert page_spec == expected_page_spec
    assert type_spec == expected_type


# --- -----------------------
# Tests for specs_to_modification_rules (Main Function)
# --- -----------------------


def test_parser_success_simple():
    """
    Tests basic rule parsing using the REAL page_numbers_matching_page_spec logic.
    """
    specs = ["1-4/Link(Border=null, Foo=bar)"]
    # We use total_pages=10 so 1-4 is valid.
    rules = ma_parser.specs_to_modification_rules(specs, total_pages=10)

    assert len(rules) == 1
    rule = rules[0]

    assert type(rule).__name__ == "ModificationRule"
    assert rule.page_numbers == [1, 2, 3, 4]
    assert rule.type_selector == "/Link"
    assert ("Border", "null") in rule.modifications
    assert rule.modifications == [("Border", "null"), ("Foo", "bar")]


def test_parser_success_multiple_specs():
    """
    Tests multiple specs, including type-only and page-only selectors.
    """
    specs = [
        "odd(C=[1 0 0])",
        "/Text(T='(New Author)')",
        "(Key=Val)",  # Empty selector -> 1-end
    ]
    rules = ma_parser.specs_to_modification_rules(specs, total_pages=10)

    assert len(rules) == 3

    # Rule 1: "odd" -> List [1, 3, 5, 7, 9]
    assert rules[0].page_numbers == [1, 3, 5, 7, 9]
    assert rules[0].type_selector is None
    assert rules[0].modifications == [("C", "[1 0 0]")]

    # Rule 2: "/Text" -> "1-end" -> List [1..10]
    assert rules[1].page_numbers == list(range(1, 11))
    assert rules[1].type_selector == "/Text"
    assert rules[1].modifications == [("T", "(New Author)")]

    # Rule 3: "" -> "1-end" -> List [1..10]
    assert rules[2].page_numbers == list(range(1, 11))
    assert rules[2].type_selector is None
    assert rules[2].modifications == [("Key", "Val")]


# --- -----------------------
# Hypothesis Property-Based Tests
# --- -----------------------

st_key = st.text(
    alphabet=st.characters(min_codepoint=65, max_codepoint=122, whitelist_categories=("L", "N")),
    min_size=1,
    max_size=10,
).filter(lambda s: not s.startswith("=") and not s.startswith("/"))

st_value = st.one_of(
    st.just("null"),
    st.just("true"),
    st.just("false"),
    st.just("[0 0 1]"),
    st.just("(Some String)"),
    st.just("/Name"),
    st.text(alphabet="abc 123", min_size=1, max_size=20),
)

st_kv_list = st.lists(
    st.tuples(st_key, st_value).map(lambda kv: f"{kv[0]}={kv[1]}"),
    min_size=1,
    max_size=5,
)

st_selector = st.one_of(
    st.just(""),
    st.just("1-5"),
    st.just("odd"),
    st.just("/Link"),
    st.just("even/Text"),
)


@given(selector=st_selector, kv_list=st_kv_list)
def test_parser_hypothesis_valid_specs(selector, kv_list):
    """
    Tests that the parser can handle a wide variety of valid
    specs without crashing.
    """
    mod_str = ", ".join(kv_list)
    spec_str = f"{selector}({mod_str})"

    # Using the real logic is fine as long as total_pages is sufficient for "1-5"
    rules = ma_parser.specs_to_modification_rules([spec_str], total_pages=10)

    assert len(rules) == 1
    assert len(rules[0].modifications) == len(kv_list)
    assert rules[0].modifications[0][0] == kv_list[0].split("=")[0]


def test_split_spec_missing_closing_paren():
    """A spec with an opening paren but no closing paren should raise."""
    with pytest.raises(ValueError, match="Invalid modification spec format"):
        ma_parser._split_spec("selector(Key=Value")


@given(spec=st.text().filter(lambda s: "(" in s and not s.strip().endswith(")")))
@settings(suppress_health_check=[HealthCheck.filter_too_much])
def test_split_spec_hypothesis_unclosed_paren(spec):
    """Any string with an opening paren but no closing paren should raise."""
    with pytest.raises(ValueError):
        ma_parser._split_spec(spec)


@given(spec=st.text().filter(lambda s: "(" not in s and s.strip()))
def test_modification_rules_hypothesis_no_parens_require_keyval(spec):
    """Any non-empty string without parens should raise when require_keyval=True."""
    with pytest.raises((ValueError, TypeError)):
        ma_parser.specs_to_modification_rules([spec], total_pages=10, require_keyval=True)
