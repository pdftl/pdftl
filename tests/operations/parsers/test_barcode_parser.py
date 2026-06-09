import pytest
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.parsers.barcode_parser import (
    _extract_options_block,
    _extract_text_components,
    _split_barcode_spec,
    _parse_options_string,
    _validate_and_merge_options,
    parse_barcode_specs_to_rules,
)


def test_extract_options_block():
    assert _extract_options_block("!test!(foo=bar)") == ("(foo=bar)", "!test!")
    assert _extract_options_block("!test!") == ("", "!test!")
    assert _extract_options_block("!test(inner)!(foo=bar)") == ("(foo=bar)", "!test(inner)!")
    # Unmatched nested parentheses fallback
    assert _extract_options_block("!test!(foo") == ("", "!test!(foo")


def test_extract_text_components():
    assert _extract_text_components("!hello!") == ("1-end", "hello")
    assert _extract_text_components("1-5!hello!") == ("1-5", "hello")

    with pytest.raises(ValueError, match="Missing text string component"):
        _extract_text_components("")

    with pytest.raises(ValueError, match="Delimiter must be a non-alphanumeric character"):
        _extract_text_components("1-5xhellox")

    # FIX: Place the valid delimiter at the end so it passes the alphanumeric
    # check and hits the unmatched error block
    with pytest.raises(ValueError, match="Unmatched text delimiter"):
        _extract_text_components("hello!")


def test_split_barcode_spec():
    assert _split_barcode_spec("1-2!test!(a=b)") == ("1-2", "test", "(a=b)")
    with pytest.raises(ValueError, match="Empty barcode spec"):
        _split_barcode_spec("   ")


def test_parse_options_string(mocker):
    # Empty options
    assert _parse_options_string("") == {}

    # Happy path testing string stripping and normalization of 'center' -> 'mid-center'
    opts = _parse_options_string("(position='center', scale=\"5\")")
    assert opts == {"position": "mid-center", "scale": "5"}

    # Mocking InvalidArgumentError from parse_keyval_list
    mocker.patch(
        "pdftl.operations.parsers.barcode_parser.parse_keyval_list",
        side_effect=InvalidArgumentError("Bad key"),
    )
    with pytest.raises(ValueError, match="Bad key"):
        _parse_options_string("(invalid_key=5)")


def test_validate_and_merge_options():
    # Valid merge
    opts = _validate_and_merge_options({"scale": "20", "format": "Code128"})
    assert opts["scale"] == 20
    assert opts["format"] == "Code128"

    # Mutually exclusive coordinates
    with pytest.raises(ValueError, match="Cannot specify both 'position' and 'x'/'y'"):
        _validate_and_merge_options({"position": "top-left", "x": "10"})

    # Scale <= 0
    with pytest.raises(ValueError, match="Scale must be a positive integer"):
        _validate_and_merge_options({"scale": "0"})

    # Scale non-integer
    with pytest.raises(ValueError, match="Scale must be a positive integer"):
        _validate_and_merge_options({"scale": "abc"})


def test_parse_barcode_specs_to_rules(mocker):
    # Valid spec parsing
    mocker.patch(
        "pdftl.operations.parsers.barcode_parser.page_numbers_matching_page_spec",
        return_value=[1, 2],
    )

    rules = parse_barcode_specs_to_rules(["!https://example.com!(scale=5)"], 5)

    # Rule for page 0 (index 1-1) and page 1 (index 2-1)
    assert len(rules) == 2
    assert rules[0][0]["scale"] == 5
    assert rules[0][0]["n"] == 1
    assert rules[1][0]["n"] == 2

    # Catching inner ValueError and wrapping to InvalidArgumentError
    with pytest.raises(InvalidArgumentError, match="Invalid barcode spec"):
        parse_barcode_specs_to_rules(["   "], 5)


def test_barcode_parser_unmatched_options_parenthesis():
    """Targets line 47: Triggers the fallback return in _extract_options_block."""
    # The unmatched ')' prevents a valid options block extraction.
    # The parser then treats ')' as the text delimiter, which is illegal.
    with pytest.raises(
        InvalidArgumentError, match="Delimiter must be a non-alphanumeric character"
    ):
        parse_barcode_specs_to_rules(["1-end |text| (scale=5))"], total_pages=1)


def test_barcode_parser_empty_options_block():
    """Targets line 90: Content inside options parentheses evaluates to empty."""
    # Empty options block should safely pass through and apply defaults
    rules = parse_barcode_specs_to_rules(["1-end |text| (   )"], total_pages=1)
    assert 0 in rules
    assert rules[0][0]["scale"] == 10  # Verifies default value was merged
