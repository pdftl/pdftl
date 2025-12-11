from hypothesis import given
from hypothesis import strategies as st

import pdftl.commands.parsers.inject_parser as cp

# ---------------------------
# Reusable Strategies
# ---------------------------

# A strategy for a single page spec
st_page_spec = st.one_of(
    st.just("1-5"),
    st.just("even"),
    st.just("odd"),
    st.just("2-end"),
    st.just("1"),
    st.just("end-1"),
)

# A list of 0-5 page specs
st_page_spec_list = st.lists(st_page_spec, min_size=0, max_size=5)

# A simple "code" string
st_code = st.text(alphabet="abc", min_size=1, max_size=5).map(lambda s: f"{s}_code")

# ---------------------------
# Tests for parse_inject_args
# ---------------------------


@given(specs=st_page_spec_list, code=st_code)
def test_parser_ends_with_code_property(specs, code):
    """
    Tests the main success case:
    [specs...] + 'head' + 'code'
    """
    # 1. Test 'head'
    args_head = specs + ["head", code]
    heads_h, tails_h, remaining_h = cp.parse_inject_args(args_head)

    # The default spec is used *only if* the specs list is empty
    expected_specs = specs if specs else ["1-end"]

    assert heads_h == [{"specs": expected_specs, "code": code}]
    assert tails_h == []
    assert remaining_h == []

    # 2. Test 'tail'
    args_tail = specs + ["tail", code]
    heads_t, tails_t, remaining_t = cp.parse_inject_args(args_tail)

    assert heads_t == []
    assert tails_t == [{"specs": expected_specs, "code": code}]
    assert remaining_t == []


@given(specs=st_page_spec_list)
def test_parser_ends_with_command_property(specs):
    """
    Tests the "incomplete command" case:
    [specs...] + 'head'
    """
    # 1. Test 'head'
    args_head = specs + ["head"]
    heads_h, tails_h, remaining_h = cp.parse_inject_args(args_head)

    # The parser collects the specs, but the loop ends
    # while in the "just saw head" state.
    # The collected specs are correctly returned as "remaining".
    assert heads_h == []
    assert tails_h == []
    assert remaining_h == specs

    # 2. Test 'tail'
    args_tail = specs + ["tail"]
    heads_t, tails_t, remaining_t = cp.parse_inject_args(args_tail)

    assert heads_t == []
    assert tails_t == []
    assert remaining_t == specs


@given(specs=st_page_spec_list)
def test_parser_ends_with_specs_property(specs):
    """
    Tests the "leftover specs" case:
    [specs...]
    """
    args = specs
    heads, tails, remaining = cp.parse_inject_args(args)

    # No commands were seen, so everything is "remaining".
    assert heads == []
    assert tails == []
    assert remaining == specs


@given(args=st.lists(st.text(min_size=1, max_size=20), min_size=0, max_size=10))
def test_parser_handles_all_strings_without_crashing(args):
    """
    A simple "smoke test" property. The parser is designed
    as a state machine that should never crash, even on
    nonsensical input.
    """
    heads, tails, remaining_specs = cp.parse_inject_args(args)

    # Assert that the types are always correct
    assert isinstance(heads, list)
    assert isinstance(tails, list)
    assert isinstance(remaining_specs, list)
