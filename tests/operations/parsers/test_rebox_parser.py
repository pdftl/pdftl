import pytest

import pdftl.operations.parsers.rebox_parser as cp
from pdftl.operations.parsers.rebox_parser import (
    parse_rebox_content,
    parse_smart_rebox_spec,
    specs_to_page_rules,
)

# We now expect the parser to fail correctly, so we don't need this
# from pdftl.exceptions import InvalidArgumentError


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
# Tests for parse_rebox_margins
# ---------------------------


def test_parse_rebox_margins_shorthand():
    page_width, page_height = 600, 800
    # Test 1 value
    margins = cp.parse_rebox_margins("10pt", page_width, page_height, "dummy_op")
    assert margins == (10.0, 10.0, 10.0, 10.0)
    # Test 2 values
    margins = cp.parse_rebox_margins("1cm, 2cm", page_width, page_height, "dummy_op")
    cm_pt = 28.34645669
    assert pytest.approx(margins) == (cm_pt, cm_pt * 2, cm_pt, cm_pt * 2)
    # Test 3 values
    margins = cp.parse_rebox_margins("10, 20, 30", page_width, page_height, "dummy_op")
    assert margins == (10.0, 20.0, 30.0, 20.0)
    # Test 4 values
    margins = cp.parse_rebox_margins("10, 20, 30, 40", page_width, page_height, "dummy_op")
    assert margins == (10.0, 20.0, 30.0, 40.0)
    # Test 4 values with percentages
    margins = cp.parse_rebox_margins("10%, 5%, 10%, 5%", page_width, page_height, "dummy_op")
    assert margins == (60.0, 40.0, 60.0, 40.0)


def test_parse_rebox_margins_invalid():
    margins = cp.parse_rebox_margins("", 600, 800, "dummy_op")
    assert margins == (0.0, 0.0, 0.0, 0.0)
    with pytest.raises(ValueError, match="must have between 1 and 4"):
        cp.parse_rebox_margins("1, 2, 3, 4, 5", 600, 800, "dummy_op")


# ---------------------------
# Tests for specs_to_page_rules
# ---------------------------


def test_specs_to_page_rules_simple(mocker):
    mock_page_spec = mocker.patch(
        "pdftl.operations.parsers.rebox_parser.page_numbers_matching_page_spec"
    )
    mock_page_spec.return_value = [1, 2, 3]
    specs = ["1-3(10pt)"]
    page_rules, preview = cp.specs_to_page_rules(specs, total_pages=3, operation="dummy_op")
    mock_page_spec.assert_called_with("1-3", 3)
    assert preview is False
    assert page_rules == {0: "10pt", 1: "10pt", 2: "10pt"}


def test_specs_to_page_rules_with_preview(mocker):
    mock_page_spec = mocker.patch(
        "pdftl.operations.parsers.rebox_parser.page_numbers_matching_page_spec"
    )
    mock_page_spec.return_value = [1]
    specs = ["1(a4)", "preview"]
    page_rules, preview = cp.specs_to_page_rules(specs, total_pages=1, operation="dummy_op")
    mock_page_spec.assert_called_with("1", 1)
    assert preview is True
    assert page_rules == {0: "a4"}


def test_specs_to_page_rules_with_local_preview(mocker):
    mock_page_spec = mocker.patch(
        "pdftl.operations.parsers.rebox_parser.page_numbers_matching_page_spec"
    )
    mock_page_spec.return_value = [1]
    specs = ["1(a4,preview)"]
    page_rules, preview = cp.specs_to_page_rules(specs, total_pages=1, operation="dummy_op")
    mock_page_spec.assert_called_with("1", 1)
    assert preview is True
    assert page_rules == {0: "a4"}


def test_specs_to_page_rules_abs(mocker):
    mock_page_spec = mocker.patch(
        "pdftl.operations.parsers.rebox_parser.page_numbers_matching_page_spec"
    )
    mock_page_spec.return_value = [2]
    specs = ["2(abs,100,150,400,500)"]
    page_rules, preview = cp.specs_to_page_rules(specs, total_pages=1, operation="dummy_op")
    mock_page_spec.assert_called_with("2", 1)
    assert preview is False
    assert page_rules == {1: "abs,100,150,400,500"}


def test_specs_to_page_rules_abs_invalid(mocker):
    mock_page_spec = mocker.patch(
        "pdftl.operations.parsers.rebox_parser.page_numbers_matching_page_spec"
    )
    mock_page_spec.return_value = [2]
    specs = ["2(abs,100,150,400)"]
    with pytest.raises(ValueError, match="4 comma-separated values"):
        cp.specs_to_page_rules(specs, total_pages=1, operation="dummy_op")
    specs = ["2(abs,100,150,400,500,12)"]
    with pytest.raises(ValueError, match="4 comma-separated values"):
        cp.specs_to_page_rules(specs, total_pages=1, operation="dummy_op")


def test_specs_to_page_rules_multiple_and_default_range(mocker):
    mock_page_spec = mocker.patch(
        "pdftl.operations.parsers.rebox_parser.page_numbers_matching_page_spec"
    )
    mock_page_spec.side_effect = [[2], [1, 2]]
    specs = ["2(a5)", "(10pt)"]
    page_rules, preview = cp.specs_to_page_rules(specs, total_pages=2, operation="dummy_op")
    assert preview is False
    assert page_rules == {1: "10pt", 0: "10pt"}
    mock_page_spec.assert_any_call("2", 2)
    mock_page_spec.assert_any_call("", 2)


def test_specs_to_page_rules_invalid_spec():
    """
    Tests that an invalid spec string (missing parentheses)
    raises a ValueError. This test will FAIL until the
    regex in rebox_parser.py is fixed.
    """
    specs = ["1-3_10pt"]

    # REVERTED: This is the original, correct assertion.
    # It will fail until the regex bug in rebox_parser.py is fixed.
    with pytest.raises(ValueError, match="Invalid dummy_op specification format"):
        cp.specs_to_page_rules(specs, total_pages=3, operation="dummy_op")


# --- merged from test_rebox_parser_coverage.py ---


class TestCropParserCoverage:
    # --- Covers lines 41-42 ---
    def test_specs_to_page_rules_validation_error(self):
        """
        Triggers the try/except block inside specs_to_page_rules (lines 39-44).
        We pass a syntactically valid outer spec '1(...)',
        but the inner content 'bad%' causes parse_rebox_content to fail.
        """
        specs = ["1(bad%)"]

        # Verify it raises the specific ValueError wrapping the underlying exception
        with pytest.raises(ValueError, match="Error parsing dummy_op content 'bad%'"):
            specs_to_page_rules(specs, total_pages=10, operation="dummy_op")

    # --- Covers line 67 ---
    def test_parse_rebox_content_returns_smart_crop(self):
        """
        Verifies line 67: if smart_crop is found, return it immediately.
        """
        result = parse_rebox_content("fit", 100, 100, "dummy_op")
        assert result["type"] == "fit"
        assert result["mode"] == "fit"

    # --- Covers lines 91-117 (Smart Crop Logic) ---
    def test_parse_smart_rebox_spec_details(self):
        # 1. Basic 'fit' (Lines 91, 101 checks)
        res = parse_smart_rebox_spec("fit", 100, 100, "dummy_op")
        assert res["mode"] == "fit"
        assert res["source"] is None
        # Lines 110-112: Default padding
        assert res["padding"] == (0.0, 0.0, 0.0, 0.0)

        # 2. 'fit-group' with source (Lines 95-100)
        res = parse_smart_rebox_spec("fit-group=1-5", 100, 100, "dummy_op")
        assert res["mode"] == "fit-group"
        assert res["source"] == "1-5"

        # 3. 'fit-group' without source (Lines 95-96, skip 97)
        res = parse_smart_rebox_spec("fit-group", 100, 100, "dummy_op")
        assert res["mode"] == "fit-group"
        assert res["source"] is None

        # 4. Partial match that should fail (Lines 101-104)
        # "fitting" starts with "fit" but != "fit" and doesn't start with "fit-group"
        res = parse_smart_rebox_spec("fitting", 100, 100, "dummy_op")
        assert res is None

        # 5. Padding Logic (Lines 106-108, 113-115)
        # "fit, 10pt" -> splits into ["fit", "10pt"] -> padding_str="10pt"
        res = parse_smart_rebox_spec("fit, 10pt", 100, 100, "dummy_op")
        assert res["padding"] == (10.0, 10.0, 10.0, 10.0)
