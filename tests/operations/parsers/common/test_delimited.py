# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/parsers/common/test_delimited.py

from __future__ import annotations

import pytest
from unittest.mock import patch

from pdftl.operations.parsers.common.delimited import (
    extract_trailing_options,
    split_delimited_rule,
    parse_options_string,
)


# --- 1. Tests for extract_trailing_options ---


def test_extract_trailing_options_no_options():
    """Verifies that strings not ending with a parenthesis return empty options."""
    opt, remaining = extract_trailing_options("1-end/some_value")
    assert opt == ""
    assert remaining == "1-end/some_value"


def test_extract_trailing_options_simple():
    """Tests basic parenthesized options block extraction."""
    opt, remaining = extract_trailing_options("1-end/some_value/(opt1=val1, opt2=val2)")
    assert opt == "(opt1=val1, opt2=val2)"
    assert remaining == "1-end/some_value/"


def test_extract_trailing_options_nested_parentheses():
    """Verifies that nested parentheses inside the options block are tracked and matched correctly."""
    opt, remaining = extract_trailing_options("1-end/some_value/(nested=(val1,val2))")
    assert opt == "(nested=(val1,val2))"
    assert remaining == "1-end/some_value/"


def test_extract_trailing_options_unbalanced_parentheses():
    """Ensures unbalanced parenthetical trailing blocks fall back safely."""
    # Ends in ) but count is unbalanced (split_pos remains -1)
    opt, remaining = extract_trailing_options("1-end/some_value/opt1=val1)")
    assert opt == ""
    assert remaining == "1-end/some_value/opt1=val1)"


# --- 2. Tests for split_delimited_rule ---


def test_split_delimited_rule_success():
    """Tests successful decomposition of valid page-range delimited DSL rules."""
    # Standard rule format
    page_range, body, opt = split_delimited_rule("1-3/my_body_contents/(opt=1)", label="test")
    assert page_range == "1-3"
    assert body == "my_body_contents"
    assert opt == "(opt=1)"

    # Rule without page range defaults to "1-end"
    page_range, body, opt = split_delimited_rule("!my_body_contents!(opt=1)", label="test")
    assert page_range == "1-end"
    assert body == "my_body_contents"
    assert opt == "(opt=1)"

    # Rule without options block
    page_range, body, opt = split_delimited_rule("1-3/my_body_contents/", label="test")
    assert page_range == "1-3"
    assert body == "my_body_contents"
    assert opt == ""


def test_split_delimited_rule_errors():
    """Verifies that split_delimited_rule raises appropriate ValueErrors on syntax violations."""
    # Empty rule input
    with pytest.raises(ValueError, match="Empty raw rule for test_label"):
        split_delimited_rule("", label="test_label")

    with pytest.raises(ValueError, match="Empty raw rule for test_label"):
        split_delimited_rule("   ", label="test_label")

    # Missing main body (only options remain after option extraction)
    with pytest.raises(ValueError, match="Missing test_label body component"):
        split_delimited_rule("(opt=1)", label="test_label")

    # Alphanumeric delimiter
    with pytest.raises(ValueError, match="Invalid test_label delimiter 'a'"):
        split_delimited_rule("1-enda_bodya(opt=1)", label="test_label")

    # Delimiter is a parenthesis
    with pytest.raises(ValueError, match="Invalid test_label delimiter '\\)'"):
        split_delimited_rule("1-end)a_body)(opt=1)", label="test_label")

    # Unmatched delimiter
    with pytest.raises(ValueError, match="Unmatched test_label delimiter '#'"):
        split_delimited_rule("1-end/my_body#", label="test_label")


# --- 3. Tests for parse_options_string ---


def test_parse_options_string_success():
    """Tests dictionary normalization of valid option parentheses blocks."""
    # Empty inputs
    assert parse_options_string("") == {}
    assert parse_options_string("()") == {}

    # Basic keys and values, testing case-insensitivity on keys and space stripping
    opts = parse_options_string("(key1=val1, KEY2=val2)")
    assert opts == {"key1": "val1", "key2": "val2"}

    # Single/Double Quote stripping from values
    opts = parse_options_string("(key1='val1', key2=\"val2\")")
    assert opts == {"key1": "val1", "key2": "val2"}

    # Quoted commas preservation (using shared split_string_respecting_quotes utility)
    opts = parse_options_string("(key='val,with,commas')")
    assert opts == {"key": "val,with,commas"}

    # Coverage for line 108: Extra commas or spaces creating empty parts are ignored and skipped
    opts_with_empty_segments = parse_options_string("(key1=val1,,  ,key2=val2)")
    assert opts_with_empty_segments == {"key1": "val1", "key2": "val2"}


def test_parse_options_string_errors():
    """Verifies error handling for malformed or structurally invalid options blocks."""
    # Options block missing surrounding parentheses
    with pytest.raises(ValueError, match="Options block must be enclosed in parentheses"):
        parse_options_string("key=val")

    # Options block starting but not ending with parentheses
    with pytest.raises(ValueError, match="Options block must be enclosed in parentheses"):
        parse_options_string("(key=val")

    # Token lacking an equals sign
    with pytest.raises(ValueError, match="Invalid option format: 'no_equals'"):
        parse_options_string("(key=val, no_equals)")

    # Option token with empty or missing key
    with pytest.raises(ValueError, match="Option missing key in: '=val'"):
        parse_options_string("(=val)")

    # Quoting/string parsing exception fallback
    with patch(
        "pdftl.operations.parsers.common.delimited.split_string_respecting_quotes",
        side_effect=ValueError("Internal unclosed quote"),
    ):
        with pytest.raises(ValueError, match="Could not parse options block: key=val"):
            parse_options_string("(key=val)")
