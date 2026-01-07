# tests/operations/parsers/test_modify_annots_parser.py

"""
Unit tests for the modify_annots_parser module.
Requires 'pytest' and 'hypothesis'.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

# We must import the module to test, aliased as 'map'
import pdftl.operations.parsers.modify_annots_parser as map
from pdftl.operations.parsers.modify_annots_parser import ModificationRule

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
    assert map._unquote_string(input_str) == expected


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
    key, val = map._parse_kv_pair(input_str)
    assert key == expected_key
    assert val == expected_val


@pytest.mark.parametrize("input_str", ["NoEquals", " =NoKey", " "])
def test_parse_kv_pair_failure(input_str):
    with pytest.raises(ValueError):
        map._parse_kv_pair(input_str)


# --- -----------------------
# Tests for _parse_modification_string
# --- -----------------------


def test_parse_modification_string_simple():
    result = map._parse_modification_string("Border=null")
    assert result == [("Border", "null")]


def test_parse_modification_string_multiple():
    result = map._parse_modification_string("Border=null, Foo=bar, C=[1 0 0]")
    assert result == [("Border", "null"), ("Foo", "bar"), ("C", "[1 0 0]")]


def test_parse_modification_string_quotes_and_spaces():
    result = map._parse_modification_string(
        " T = 'Title, with comma' , Key=\"Value, with comma\" "
    )
    assert result == [("T", "Title, with comma"), ("Key", "Value, with comma")]


def test_parse_modification_string_trailing_comma():
    result = map._parse_modification_string("Key=Val,")
    assert result == [("Key", "Val")]


def test_parse_modification_string_empty_fails():
    with pytest.raises(ValueError, match="Empty modification list"):
        map._parse_modification_string("")


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
    page_spec, type_spec = map._parse_selector_string(input_str)
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
    rules = map.specs_to_modification_rules(specs, total_pages=10)

    assert len(rules) == 1
    rule = rules[0]
    assert isinstance(rule, ModificationRule)

    assert rule.page_numbers == [1, 2, 3, 4]

    assert rule.type_selector == "/Link"
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
    rules = map.specs_to_modification_rules(specs, total_pages=10)

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
    rules = map.specs_to_modification_rules([spec_str], total_pages=10)

    assert len(rules) == 1
    assert len(rules[0].modifications) == len(kv_list)
    assert rules[0].modifications[0][0] == kv_list[0].split("=")[0]


@given(spec=st.text().filter(lambda s: not map.spec_pattern.match(s)))
def test_parser_hypothesis_invalid_specs(spec):
    """
    Tests that any string that does NOT match the spec pattern
    correctly raises a ValueError.
    """
    with pytest.raises(ValueError):
        map.specs_to_modification_rules([spec], total_pages=10)
