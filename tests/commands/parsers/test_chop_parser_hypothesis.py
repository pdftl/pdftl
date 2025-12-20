import re

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from pikepdf import Array

import pdftl.commands.parsers.chop_parser as cp

# ---------------------------
# Reusable Strategies
# ---------------------------

# Strategy for fixed-size parts (non-fill)
st_fixed_part = st.one_of(
    st.integers(min_value=1, max_value=100).map(str),  # e.g., "100" (pt)
    st.just("10pt"),
    st.just("25%"),
    st.just("50d"),  # Test discarding
)

# Strategy for fixed-size parts *without* discard
st_fixed_part_no_discard = st.one_of(
    st.integers(min_value=1, max_value=100).map(str), st.just("10pt"), st.just("25%")
)

# A strategy for a list of parts that *must* contain "fill"
st_parts_with_fill = (
    st.lists(st_fixed_part_no_discard, min_size=0, max_size=5)
    .map(lambda l: l + ["fill"])  # Always add at least one "fill"
    .flatmap(st.permutations)  # Shuffle the list
)

# A strategy for a list of parts *without* "fill"
st_parts_without_fill = st.lists(
    st_fixed_part,  # This one *can* have discards
    min_size=1,  # Must have at least one part
    max_size=5,
)

# ---------------------------
# "Positive" Tests for parse_chop_spec
# ---------------------------


@given(
    direction=st.sampled_from(["cols", "rows"]),
    parts=st_parts_with_fill,  # Strategy guarantees "fill"
)
def test_parse_comma_spec_with_fill_property(direction, parts):
    """
    Tests the comma-spec parser for specs that ARE intended to
    fill the entire page dimension (e.g., "rows(100, fill)").
    """
    mock_page_rect = Array([0, 0, 600, 800])
    assume(len(parts) > 0)
    chop_spec = f"{direction}({','.join(map(str, parts))})"

    try:
        rects = cp.parse_chop_spec(chop_spec, mock_page_rect)
    except ValueError:
        assume(False)
        return

    if direction == "cols":
        page_width = mock_page_rect[2] - mock_page_rect[0]
        total_width = sum(r[2] - r[0] for r in rects)
        assert pytest.approx(total_width) == page_width
    elif direction == "rows":
        page_height = mock_page_rect[3] - mock_page_rect[1]
        total_height = sum(r[3] - r[1] for r in rects)
        assert pytest.approx(total_height) == page_height


@given(
    direction=st.sampled_from(["cols", "rows"]),
    parts=st_parts_without_fill,  # Strategy *guarantees no* "fill"
)
def test_parse_comma_spec_without_fill_property(direction, parts):
    """
    Tests the comma-spec parser for specs that ARE NOT intended
    to fill the page (e.g., "rows(100, 50d)").
    """
    mock_page_rect = Array([0, 0, 600, 800])
    assume(len(parts) > 0)
    chop_spec = f"{direction}({','.join(map(str, parts))})"

    try:
        rects = cp.parse_chop_spec(chop_spec, mock_page_rect)
    except ValueError:
        assume(False)
        return

    if direction == "cols":
        page_width = mock_page_rect[2] - mock_page_rect[0]
        total_width = sum(r[2] - r[0] for r in rects)
        assert total_width <= page_width
    elif direction == "rows":
        page_height = mock_page_rect[3] - mock_page_rect[1]
        total_height = sum(r[3] - r[1] for r in rects)
        assert total_height <= page_height


@given(
    direction=st.sampled_from(["cols", "rows"]),
    n=st.integers(min_value=1, max_value=20),  # Test "rows(N)"
)
def test_parse_integer_spec_property(direction, n):
    """
    Tests the integer-spec parser (e.g., "rows(3)").
    """
    mock_page_rect = Array([0, 0, 600, 800])
    chop_spec = f"{direction}({n})"
    rects = cp.parse_chop_spec(chop_spec, mock_page_rect)
    assert len(rects) == n

    if direction == "cols":
        width = float(mock_page_rect[2]) / n
        assert all(pytest.approx(float(r[2] - r[0])) == width for r in rects)
    elif direction == "rows":
        height = float(mock_page_rect[3]) / n
        assert all(pytest.approx(float(r[3] - r[1])) == height for r in rects)


@given(
    direction=st.sampled_from(["cols", "rows"]),
    ratios=st.lists(st.integers(min_value=1, max_value=10), min_size=2, max_size=5),
)
def test_parse_ratio_spec_property(direction, ratios):
    """
    Tests the ratio-spec parser (e.g., "cols(1:2)").
    """
    mock_page_rect = Array([0, 0, 600, 800])
    ratio_str = ":".join(map(str, ratios))
    chop_spec = f"{direction}({ratio_str})"
    rects = cp.parse_chop_spec(chop_spec, mock_page_rect)
    assert len(rects) == len(ratios)

    if direction == "cols":
        sizes = [r[2] - r[0] for r in rects]
    else:
        sizes = [r[3] - r[1] for r in rects]

    first_ratio = ratios[0]
    first_size = sizes[0]
    for i in range(1, len(ratios)):
        expected_ratio = ratios[i] / first_ratio
        actual_ratio = sizes[i] / first_size
        assert pytest.approx(float(actual_ratio)) == expected_ratio


# ---------------------------
# "Positive" Test for parse_chop_specs_to_rules
# ---------------------------

st_page_range = st.one_of(
    st.just(""), st.just("1-3"), st.just("5"), st.just("2-end"), st.just("end-1")
)
st_qualifier = st.one_of(st.just(""), st.just("even"), st.just("odd"))
st_chop_part = st.one_of(
    st.just("cols"), st.just("rows(3)"), st.just("cols(1:2)"), st.just("rows(fill,10)")
)
st_full_spec_string = st.builds(
    lambda r, q, c: f"{r}{q}{c}", r=st_page_range, q=st_qualifier, c=st_chop_part
)


@given(
    specs=st.lists(st_full_spec_string, min_size=1, max_size=5),
    total_pages=st.integers(min_value=1, max_value=50),
)
def test_parse_chop_specs_to_rules_property(specs, total_pages):
    """
    Property-based test for parse_chop_specs_to_rules.
    """
    try:
        result = cp.parse_chop_specs_to_rules(specs, total_pages)
    except ValueError:
        return
    assert all(isinstance(page_index, int) and page_index >= 0 for page_index in result.keys())
    assert all(
        isinstance(rule, str) and re.search(r"^(cols|rows)", rule) for rule in result.values()
    )


# ---------------------------
# "Negative" Tests (Asserting Errors)
# ---------------------------


@given(
    direction=st.sampled_from(["cols", "rows"]),
    parts=st.lists(st.integers(min_value=401, max_value=800), min_size=2).map(
        lambda l: [f"{x}pt" for x in l]
    ),
)
def test_parse_comma_spec_exceeds_dimensions_raises(direction, parts):
    """
    Tests that a comma-spec with fixed parts (e.g., "pt")
    that sum to *more* than the page dimension correctly
    raises a ValueError. (e.g., rows(500pt, 500pt) on an 800pt page)
    """
    mock_page_rect = Array([0, 0, 600, 800])
    chop_spec = f"{direction}({','.join(parts)})"

    # Asserting the specific, unwrapped error
    with pytest.raises(
        ValueError, match="Sum of fixed sizes in chop spec exceeds page dimensions."
    ):
        cp.parse_chop_spec(chop_spec, mock_page_rect)


@given(
    direction=st.sampled_from(["cols", "rows"]),
    # Strategy: Test the "0:0" case, which leads to ZeroDivisionError
    ratios_str=st.just("0:0"),
)
def test_parse_invalid_ratio_raises(direction, ratios_str):
    """
    Tests that a ratio-spec that causes a ZeroDivisionError
    (like "0:0") is correctly caught and raised.
    """
    mock_page_rect = Array([0, 0, 600, 800])
    chop_spec = f"{direction}({ratios_str})"

    # Asserting the specific, unwrapped error
    with pytest.raises(ValueError, match="Invalid ratio format"):
        cp.parse_chop_spec(chop_spec, mock_page_rect)


@given(
    specs=st.lists(st.sampled_from(["even", "odd"]), min_size=1, max_size=3),
    total_pages=st.integers(min_value=1, max_value=10),
)
def test_parse_qualifier_only_spec_raises(specs, total_pages):
    """
    Tests that a spec list containing *only* qualifiers
    (e.g., ['even', 'odd']) and no actual chop command
    correctly raises a ValueError.
    """
    # This test asserts the error from _split_spec_string
    with pytest.raises(ValueError, match="(Invalid|Missing) chop spec"):
        cp.parse_chop_specs_to_rules(specs, total_pages)
