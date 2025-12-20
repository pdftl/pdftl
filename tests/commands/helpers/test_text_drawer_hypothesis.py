import math
import unittest

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pdftl.commands.helpers.text_drawer import _PageBox  # Import the helper tuple
from pdftl.commands.helpers.text_drawer import (
    _get_base_coordinates,
    _resolve_dimension,
)

# Import custom exception for testing
try:
    from pdftl.exceptions import InvalidArgumentError
except ImportError:
    InvalidArgumentError = ValueError


MockPageBox = _PageBox

# --- Strategies for generating valid inputs ---

st_floats = st.floats(min_value=0, max_value=10000, allow_nan=False, allow_infinity=False)

st_dim_rule = st.one_of(
    st.builds(lambda v: {"type": "pt", "value": v}, st_floats),
    st.builds(lambda v: {"type": "%", "value": v}, st.floats(min_value=0, max_value=100)),
    st_floats,  # Test raw floats
    st.just(None),
)

st_page_box_hypothesis = st.builds(
    MockPageBox,
    width=st.floats(min_value=1, max_value=2000),
    height=st.floats(min_value=1, max_value=2000),
)

st_align = st.one_of(st.just("left"), st.just("center"), st.just("right"), st.just(None))

st_position_preset = st.sampled_from(
    [
        "top-left",
        "top-center",
        "top-right",
        "mid-left",
        "mid-center",
        "mid-right",
        "bottom-left",
        "bottom-center",
        "bottom-right",
    ]
)

st_position_rule = st.one_of(
    st.builds(lambda p: {"position": p}, st_position_preset),
    st.builds(lambda x, y: {"x": x, "y": y}, st_dim_rule, st_dim_rule),
)


@st.composite
def st_full_rule(draw):
    """Generates a rule dict for testing coordinates and matrices."""
    rule = draw(st_position_rule)
    rule["align"] = draw(st_align)
    rule["offset-x"] = draw(st_dim_rule)
    rule["offset-y"] = draw(st_dim_rule)
    rule["rotate"] = draw(st.floats(min_value=-360, max_value=360))
    return rule


@pytest.mark.slow
class TestTextDrawerHypothesis(unittest.TestCase):
    """Property-based tests for the coordinate logic functions."""

    @given(dim_rule=st_dim_rule, page_dim=st_floats)
    @settings(max_examples=200)
    def test_resolve_dimension_hypothesis(self, dim_rule, page_dim):
        """Test that _resolve_dimension always returns a finite float."""
        result = _resolve_dimension(dim_rule, page_dim)
        self.assertIsInstance(result, float)
        self.assertTrue(math.isfinite(result))

    @given(
        rule=st_full_rule(),
        page_box=st_page_box_hypothesis,
    )
    @settings(max_examples=500, deadline=None)
    def test_get_base_coordinates_hypothesis(self, rule, page_box):
        """Test that _get_base_coordinates always returns valid coords."""
        x, y = _get_base_coordinates(rule, page_box)
        self.assertTrue(math.isfinite(x))
        self.assertTrue(math.isfinite(y))
