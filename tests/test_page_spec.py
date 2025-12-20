# tests/test_page_specs.py

import pytest

from pdftl.exceptions import InvalidArgumentError

# We must import the PageSpec class to construct our expected results
from pdftl.utils.page_specs import page_numbers_matching_page_spec, parse_page_spec

# =======================================================================
#  Tests for page_numbers_matching_page_spec
#
#  - Signature: page_numbers_matching_page_spec(spec, num_pages)
#  - Output: 1-based page numbers
# =======================================================================

# Helper lists for even/odd expectations
odd_pages_20 = list(range(1, 21, 2))  # [1, 3, 5, 7, 9, 11, 13, 15, 17, 19]
even_pages_20 = list(range(2, 21, 2))  # [2, 4, 6, 8, 10, 12, 14, 16, 18, 20]


@pytest.mark.parametrize(
    "spec, num_pages, expected_pages",
    [
        # Note: Expected output is 1-indexed page numbers
        # === Simple Ranges ===
        ("1-5", 20, [1, 2, 3, 4, 5]),
        ("5-1", 20, [1, 2, 3, 4, 5]),  # Reverse range
        ("1-1", 20, [1]),
        # === 'end' Keyword ===
        ("18-end", 20, [18, 19, 20]),
        ("end-18", 20, [18, 19, 20]),
        ("end-end", 20, [20]),
        ("1-end", 20, list(range(1, 21))),  # All pages
        # === Single Numbers ===
        ("1", 20, [1]),
        ("10", 20, [10]),
        ("end", 20, [20]),
        # === Even/Odd Filters ===
        ("odd", 20, odd_pages_20),
        ("even", 20, even_pages_20),
        # === Combined Even/Odd and Ranges ===
        ("1-10even", 20, [2, 4, 6, 8, 10]),
        ("1-10odd", 20, [1, 3, 5, 7, 9]),
        ("5-endeven", 20, [6, 8, 10, 12, 14, 16, 18, 20]),
        ("5-endodd", 20, [5, 7, 9, 11, 13, 15, 17, 19]),
        # === Edge Cases ===
        ("1-10", 5, [1, 2, 3, 4, 5]),  # Range larger than num_pages
        ("odd", 1, [1]),  # Single odd page
        ("even", 1, []),  # Single even page (no page 2)
        ("1-", 5, [1]),
        ("r1-1", 5, [1, 2, 3, 4, 5]),
        ("evenodd", 20, []),
        ("8-18", 10, [8, 9, 10]),  # too many pages
        # omitted pages
        ("1-10~3~4~6-9", 10, [1, 2, 5, 10]),
        ("~2", 5, [1, 3, 4, 5]),
    ],
)
def test_page_specs_basic(spec, num_pages, expected_pages):
    """
    Tests all page spec combinations that don't require a PDF object.
    """
    result = page_numbers_matching_page_spec(spec, num_pages)
    assert result == expected_pages


def test_page_spec_none():
    """
    Tests that a None spec returns all pages.
    """
    assert page_numbers_matching_page_spec("", 10) == list(range(1, 11))
    assert page_numbers_matching_page_spec("", 0) == []


@pytest.mark.parametrize(
    "invalid_spec",
    [
        "foo",  # Completely invalid
        "1-foo",  # Invalid end of range
        "bar1-10",  # Invalid prefix
        "L",  # Rotation spec (invalid without a PDF)
        "1-10L",  # Rotation spec (invalid without a PDF)
        "FDF",  # FDF spec (invalid without a PDF)
        "PROMPT",  # PROMPT spec (invalid without context)
    ],
)
def test_page_spec_invalid(invalid_spec):
    """
    Tests that invalid or un-resolvable page specs correctly raise an Error.
    """
    with pytest.raises(InvalidArgumentError):
        page_numbers_matching_page_spec(invalid_spec, 20)


# =======================================================================
#  Tests for parse_page_spec (The Core Parser)
#
#  This tests the function that returns the PageSpec data structure.
# =======================================================================

# We must import the PageSpec class


@pytest.mark.parametrize(
    "spec, total_pages, expected_fields",
    [
        # spec, total_pages, {fields that change from default}
        ("1-5", 20, {"start": 1, "end": 5}),
        ("5-1", 20, {"start": 5, "end": 1}),
        ("18-end", 20, {"start": 18, "end": 20}),
        ("end-18", 20, {"start": 20, "end": 18}),
        ("even", 20, {"start": 1, "end": 20, "qualifiers": {"even"}}),
        ("1-10odd", 20, {"start": 1, "end": 10, "qualifiers": {"odd"}}),
        (
            "1-10~3~4~6-9",
            10,
            {"start": 1, "end": 10, "omissions": [(3, 3), (4, 4), (6, 9)]},
        ),
        ("~2", 5, {"start": 1, "end": 5, "omissions": [(2, 2)]}),
        ("r1-1", 5, {"start": 5, "end": 1}),
        ("evenodd", 5, {"start": 1, "end": 5, "qualifiers": {"even", "odd"}}),
    ],
)
def test_parse_page_spec(spec, total_pages, expected_fields):
    """
    Tests the core parser that returns the PageSpec data structure.
    """
    # 1. Get a "default" PageSpec object by parsing a simple case.
    #    This is safer than assuming the defaults.
    #    We use a known simple spec like "1-1" on 1 page.
    default_spec = parse_page_spec("1-1", 1)

    # 2. Get the actual result from the test spec
    result = parse_page_spec(spec, total_pages)

    # 3. Check all fields, comparing to the 'expected_fields' dict
    #    and falling back to the 'default_spec' for any field
    #    not in the dict.
    assert result.start == expected_fields.get("start", default_spec.start)
    assert result.end == expected_fields.get("end", default_spec.end)
    assert result.rotate == expected_fields.get("rotate", default_spec.rotate)
    assert result.scale == expected_fields.get("scale", default_spec.scale)
    assert result.qualifiers == expected_fields.get("qualifiers", default_spec.qualifiers)
    assert result.omissions == expected_fields.get("omissions", default_spec.omissions)
