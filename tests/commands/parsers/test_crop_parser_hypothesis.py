import pytest
from hypothesis import given
from hypothesis import strategies as st

import pdftl.commands.parsers.crop_parser as cp

# ---------------------------
# Reusable Strategies
# ---------------------------

# Strategy for a single, simple margin value string
st_margin_value_str = st.one_of(
    st.integers(min_value=0, max_value=1000).map(lambda x: f"{x}pt"),
    st.integers(min_value=0, max_value=100).map(lambda x: f"{x}cm"),
    st.integers(min_value=0, max_value=100).map(lambda x: f"{x}mm"),
    st.integers(min_value=0, max_value=10).map(lambda x: f"{x}in"),
    st.integers(min_value=0, max_value=100).map(lambda x: f"{x}%"),
    st.integers(min_value=0, max_value=1000).map(str),  # Default to 'pt'
)

# Strategy for known paper sizes from docs/parser
st_paper_sizes = st.sampled_from(["a4", "a4_l", "a5", "a5_l", "letter", "letter_l", "4x6"])

# ---------------------------
# Tests for Public Functions
# ---------------------------


@given(
    value=st.integers(min_value=0, max_value=100),
    dimension=st.integers(min_value=100, max_value=1000),
)
def test_parse_single_margin_value_property(value, dimension):
    """
    Tests the _parse_single_margin_value function with various units.
    Note: This is an internal function, but crucial to test.
    """
    # Test percentages
    percent_str = f"{value}%"
    expected_percent = (value / 100.0) * dimension
    assert pytest.approx(cp._parse_single_margin_value(percent_str, dimension)) == expected_percent

    # Test 'pt' (which is the default)
    pt_str = f"{value}"
    assert pytest.approx(cp._parse_single_margin_value(pt_str, dimension)) == value

    # Test 'in'
    in_str = f"{value}in"
    assert pytest.approx(cp._parse_single_margin_value(in_str, dimension)) == value * 72.0

    # Test 'cm'
    cm_str = f"{value}cm"
    assert pytest.approx(cp._parse_single_margin_value(cm_str, dimension)) == value * 28.35

    # Test 'mm'
    mm_str = f"{value}mm"
    assert pytest.approx(cp._parse_single_margin_value(mm_str, dimension)) == value * 2.835


@given(paper_spec=st_paper_sizes)
def test_parse_paper_spec_property(paper_spec):
    """
    Tests that known paper sizes are parsed correctly.
    """
    result = cp.parse_paper_spec(paper_spec)
    assert result is not None
    assert isinstance(result, tuple)
    assert len(result) == 2
    assert isinstance(result[0], (int, float))

    # Test landscape property
    if paper_spec.endswith("_l"):
        base_spec = paper_spec[:-2]
        base_result = cp.parse_paper_spec(base_spec)
        assert pytest.approx(result[0]) == base_result[1]
        assert pytest.approx(result[1]) == base_result[0]


@given(parts=st.lists(st_margin_value_str, min_size=1, max_size=4))
def test_parse_crop_margins_shorthand_property(parts):
    """
    Tests the 1, 2, 3, and 4-value shorthand logic for margins.
    """
    spec_str = ",".join(parts)
    page_width, page_height = 600, 800

    # We need to parse the values * ourselves* to check the logic
    # This is a bit complex but necessary to validate the test.
    parsed_parts = [cp._parse_single_margin_value(p, page_width) for p in parts]

    # Parse using the function
    left, top, right, bottom = cp.parse_crop_margins(spec_str, page_width, page_height)

    if len(parts) == 1:
        # 1 value: [all sides]
        expected = cp._parse_single_margin_value(parts[0], page_width)
        assert (left, top, right, bottom) == (expected, expected, expected, expected)
    elif len(parts) == 2:
        # 2 values: [left] [top] (right=left, bottom=top)
        expected_left = cp._parse_single_margin_value(parts[0], page_width)
        expected_top = cp._parse_single_margin_value(parts[1], page_width)
        assert (left, top, right, bottom) == (
            expected_left,
            expected_top,
            expected_left,
            expected_top,
        )
    elif len(parts) == 3:
        # 3 values: [left] [top] [right] (bottom=top)
        expected_left = cp._parse_single_margin_value(parts[0], page_width)
        expected_top = cp._parse_single_margin_value(parts[1], page_width)
        expected_right = cp._parse_single_margin_value(parts[2], page_width)
        assert (left, top, right, bottom) == (
            expected_left,
            expected_top,
            expected_right,
            expected_top,
        )
    elif len(parts) == 4:
        # 4 values: [left] [top] [right] [bottom]
        # Note: 'bottom' (parts[3]) uses page_height!
        expected_left = cp._parse_single_margin_value(parts[0], page_width)
        expected_top = cp._parse_single_margin_value(parts[1], page_width)
        expected_right = cp._parse_single_margin_value(parts[2], page_width)
        expected_bottom = cp._parse_single_margin_value(parts[3], page_height)  # Uses page_height
        assert (left, top, right, bottom) == (
            expected_left,
            expected_top,
            expected_right,
            expected_bottom,
        )


@given(
    page_range=st.one_of(st.just(""), st.just("1-3"), st.just("even"), st.just("2-8odd")),
    margin_spec=st.one_of(st.just("10pt,20pt"), st_paper_sizes),
    has_preview=st.booleans(),
    total_pages=st.integers(min_value=1, max_value=50),
)
def test_specs_to_page_rules_property(page_range, margin_spec, has_preview, total_pages):
    """
    Tests the main specs_to_page_rules function.
    """
    spec_str = f"{page_range}({margin_spec})"
    specs = [spec_str]
    if has_preview:
        specs.append("preview")

    page_rules, preview = cp.specs_to_page_rules(specs, total_pages)

    # Test preview flag
    assert preview == has_preview

    # Test that the margin string is correctly assigned
    for v in page_rules.values():
        assert v == margin_spec
