import pytest

import pdftl.commands.parsers.crop_parser as cp

# We now expect the parser to fail correctly, so we don't need this
# from pdftl.exceptions import InvalidArgumentError

# ---------------------------
# Tests for _parse_single_margin_value (internal helper)
# ---------------------------


@pytest.mark.parametrize(
    "val_str, total_dimension, expected_pts",
    [
        ("50", 800, 50.0),  # Default unit is pt
        ("50pt", 800, 50.0),  # Explicit pt
        ("0", 800, 0.0),  # Zero value
        ("", 800, 0.0),  # Empty value
        ("10%", 800, 80.0),  # Percentage
        ("1in", 100, 72.0),  # Inch
        ("2in", 100, 144.0),  # Multiple inches
        ("1cm", 100, 28.35),  # Centimeter
        ("10mm", 100, 28.35),  # Millimeter (10mm = 1cm)
    ],
)
def test_parse_single_margin_value(val_str, total_dimension, expected_pts):
    result = cp._parse_single_margin_value(val_str, total_dimension)
    assert pytest.approx(result) == expected_pts


def test_parse_single_margin_value_invalid():
    with pytest.raises(ValueError):
        cp._parse_single_margin_value("foo", 800)


# ---------------------------
# Tests for parse_paper_spec
# ---------------------------


@pytest.mark.parametrize(
    "spec_str, expected_dims",
    [
        ("4x6", (4 * 72, 6 * 72)),
        ("6x4", (6 * 72, 4 * 72)),
        ("8.5x11", (8.5 * 72, 11 * 72)),
        ("4x6_l", (6 * 72, 4 * 72)),
        ("8.5x11_l", (11 * 72, 8.5 * 72)),
        ("4X6", (4 * 72, 6 * 72)),
        ("4X6_L", (6 * 72, 4 * 72)),
        ("foo", None),
        ("100", None),
        # This is the corrected assertion from last time
        ("a4", (595, 842)),
    ],
)
def test_parse_paper_spec(spec_str, expected_dims):
    result = cp.parse_paper_spec(spec_str)
    if expected_dims is None:
        assert result is None
    else:
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert pytest.approx(result[0]) == expected_dims[0]
        assert pytest.approx(result[1]) == expected_dims[1]


# ---------------------------
# Tests for parse_crop_margins
# ---------------------------


def test_parse_crop_margins_shorthand():
    page_width, page_height = 600, 800
    # Test 1 value
    margins = cp.parse_crop_margins("10pt", page_width, page_height)
    assert margins == (10.0, 10.0, 10.0, 10.0)
    # Test 2 values
    margins = cp.parse_crop_margins("1cm, 2cm", page_width, page_height)
    assert pytest.approx(margins) == (28.35, 56.7, 28.35, 56.7)
    # Test 3 values
    margins = cp.parse_crop_margins("10, 20, 30", page_width, page_height)
    assert margins == (10.0, 20.0, 30.0, 20.0)
    # Test 4 values
    margins = cp.parse_crop_margins("10, 20, 30, 40", page_width, page_height)
    assert margins == (10.0, 20.0, 30.0, 40.0)
    # Test 4 values with percentages
    margins = cp.parse_crop_margins("10%, 5%, 10%, 5%", page_width, page_height)
    assert margins == (60.0, 30.0, 60.0, 40.0)


def test_parse_crop_margins_invalid():
    margins = cp.parse_crop_margins("", 600, 800)
    assert margins == (0.0, 0.0, 0.0, 0.0)
    with pytest.raises(ValueError, match="must have between 1 and 4"):
        cp.parse_crop_margins("1, 2, 3, 4, 5", 600, 800)


# ---------------------------
# Tests for specs_to_page_rules
# ---------------------------


def test_specs_to_page_rules_simple(mocker):
    mock_page_spec = mocker.patch(
        "pdftl.commands.parsers.crop_parser.page_numbers_matching_page_spec"
    )
    mock_page_spec.return_value = [1, 2, 3]
    specs = ["1-3(10pt)"]
    page_rules, preview = cp.specs_to_page_rules(specs, total_pages=3)
    mock_page_spec.assert_called_with("1-3", 3)
    assert preview is False
    assert page_rules == {0: "10pt", 1: "10pt", 2: "10pt"}


def test_specs_to_page_rules_with_preview(mocker):
    mock_page_spec = mocker.patch(
        "pdftl.commands.parsers.crop_parser.page_numbers_matching_page_spec"
    )
    mock_page_spec.return_value = [1]
    specs = ["1(a4)", "preview"]
    page_rules, preview = cp.specs_to_page_rules(specs, total_pages=1)
    mock_page_spec.assert_called_with("1", 1)
    assert preview is True
    assert page_rules == {0: "a4"}


def test_specs_to_page_rules_multiple_and_default_range(mocker):
    mock_page_spec = mocker.patch(
        "pdftl.commands.parsers.crop_parser.page_numbers_matching_page_spec"
    )
    mock_page_spec.side_effect = [[2], [1, 2]]
    specs = ["2(a5)", "(10pt)"]
    page_rules, preview = cp.specs_to_page_rules(specs, total_pages=2)
    assert preview is False
    assert page_rules == {1: "10pt", 0: "10pt"}
    mock_page_spec.assert_any_call("2", 2)
    mock_page_spec.assert_any_call("", 2)


def test_specs_to_page_rules_invalid_spec():
    """
    Tests that an invalid spec string (missing parentheses)
    raises a ValueError. This test will FAIL until the
    regex in crop_parser.py is fixed.
    """
    specs = ["1-3_10pt"]

    # REVERTED: This is the original, correct assertion.
    # It will fail until the regex bug in crop_parser.py is fixed.
    with pytest.raises(ValueError, match="Invalid crop specification format"):
        cp.specs_to_page_rules(specs, total_pages=3)
