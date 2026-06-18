import math
import unittest
from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.exceptions import InvalidArgumentError, UserCommandLineError

# --- Import the module and functions to test ---
from pdftl.utils.page_specs import (
    PageSpec,
    PageTransform,
    _create_page_tuples_from_numbers,
    _expand_square_brackets,
    _filter_page_numbers,
    _flatten_spec_list,
    _handle_no_specs,
    _new_tuples_from_spec_str,
    _parse_omissions,
    _parse_qualifiers,
    _parse_range_part,
    _parse_rotation,
    _parse_scaling,
    _resolve_alias_and_spec,
    _resolve_page_token,
    expand_specs_to_pages,
    is_valid_page_spec,
    page_number_matches_page_spec,
    page_numbers_matching_page_spec,
    page_numbers_matching_page_specs,
    parse_specs,
    parse_sub_page_spec,
)
from pdftl.utils.page_specs.parser import SpecParser
from pdftl.utils.page_specs.resolver import _aspect_ratio_pass

# --- Total Pages constant for most tests ---
TOTAL_PAGES = 10


# --- Tests for Low-Level Private Helpers ---


@pytest.mark.parametrize(
    "token, is_reverse, total_pages, expected",
    [
        (None, False, TOTAL_PAGES, None),
        ("5", False, TOTAL_PAGES, 5),
        ("end", False, TOTAL_PAGES, 10),
        ("5", True, TOTAL_PAGES, 6),  # r5 = 10 - 5 + 1
        ("1", True, TOTAL_PAGES, 10),  # r1 = 10 - 1 + 1
        ("end", True, TOTAL_PAGES, 1),  # rend = 1
        (
            "0",
            True,
            TOTAL_PAGES,
            11,
        ),  # r0 = 10 - 0 + 1 = 11 (Correctly calculates, validation is later)
        ("0", False, TOTAL_PAGES, 0),  # 0 (Correctly calculates, validation is later)
    ],
)
def test_resolve_page_token(token, is_reverse, total_pages, expected):
    """Tests the _resolve_page_token logic."""
    assert _resolve_page_token(token, is_reverse, total_pages) == expected


@pytest.mark.parametrize(
    "modifier_str, expected_qualifiers, expected_remaining",
    [
        ("even", {"even"}, ""),
        ("odd", {"odd"}, ""),
        ("evenodd", {"even", "odd"}, ""),
        ("oddleft", {"odd"}, "left"),
        ("foobar", set(), "foobar"),
    ],
)
def test_parse_qualifiers(modifier_str, expected_qualifiers, expected_remaining):
    """Tests parsing 'even' and 'odd' qualifiers."""
    qualifiers, remaining = _parse_qualifiers(modifier_str)
    assert qualifiers == expected_qualifiers
    assert remaining == expected_remaining


@pytest.mark.parametrize(
    "modifier_str, expected_rotation, expected_remaining",
    [
        ("north", (0, False), ""),
        ("east", (90, False), ""),
        ("south", (180, False), ""),
        ("west", (270, False), ""),
        ("left", (-90, True), ""),
        ("right", (90, True), ""),
        ("down", (180, True), ""),
        ("foo", (0, False), "foo"),
        ("oddleft", (-90, True), "odd"),  # Finds "left" and removes it
    ],
)
def test_parse_rotation(modifier_str, expected_rotation, expected_remaining):
    """Tests parsing rotation keywords."""
    rotate, remaining = _parse_rotation(modifier_str)
    assert rotate == expected_rotation
    assert remaining == expected_remaining


@pytest.mark.parametrize(
    "modifier_str, expected_scale, expected_remaining",
    [
        ("x2.5", 2.5, ""),
        ("z1", math.sqrt(2), ""),  # z1 = sqrt(2)^1
        ("z-1", 1 / math.sqrt(2), ""),  # z-1 = sqrt(2)^-1
        ("x2z1", 2.0 * math.sqrt(2), ""),  # 2.0, not 2.5
        ("z1x2.5", 2.5 * math.sqrt(2), ""),  # Order shouldn't matter
        ("foo", 1.0, "foo"),
    ],
)
def test_parse_scaling(modifier_str, expected_scale, expected_remaining):
    """Tests 'x' and 'z' scaling modifiers."""
    scale, remaining = _parse_scaling(modifier_str)
    assert scale == pytest.approx(expected_scale)
    assert remaining == expected_remaining


def test_parse_scaling_invalid():
    """Tests that a non-positive scale value raises an error."""
    with pytest.raises(InvalidArgumentError, match="Invalid scaling: 0.0"):
        _parse_scaling("x0")
    with pytest.raises(InvalidArgumentError, match="Invalid scaling: -2.0"):
        _parse_scaling("x-2.0")


@pytest.mark.parametrize(
    "modifier_str, expected_rep, expected_remaining",
    [
        ("rep3", 3, ""),
        ("rep 4", 4, ""),
        ("rep12x2.0", 12, "x2.0"),
        ("evenrep2", 2, "even"),
        ("foo", 1, "foo"),  # Default is 1
    ],
)
def test_parse_rep(modifier_str, expected_rep, expected_remaining):
    """Tests the parsing of the rep modifier."""
    parser = SpecParser(total_pages=TOTAL_PAGES)
    rep, remaining = parser._parse_rep(modifier_str)
    assert rep == expected_rep
    assert remaining == expected_remaining


def test_parse_rep_invalid():
    """Tests that an invalid rep value (<1) raises an error."""
    parser = SpecParser(total_pages=TOTAL_PAGES)
    with pytest.raises(InvalidArgumentError, match="Invalid rep value 0. Should be at least 1."):
        parser._parse_rep("rep0")


@pytest.mark.parametrize(
    "modifier_str, expected_omissions, expected_remaining",
    [
        ("~1-5", [(1, 5)], ""),
        # '~even' is a recursive call, so we mock it
        ("~even", [(1, 10)], ""),
        ("~1-3~5-7", [(1, 3), (5, 7)], ""),
    ],
)
def test_parse_omissions(modifier_str, expected_omissions, expected_remaining):
    """Tests parsing omission strings like '~1-5'."""
    # We patch the main parse_sub_page_spec function that _parse_omissions
    # calls recursively.
    with patch("pdftl.utils.page_specs.parse_sub_page_spec") as mock_parse:
        # Define the side effects for the recursive calls
        if "~even" in modifier_str:
            mock_parse.return_value = PageSpec(1, 10, 1, 1, (0, False), 1.0, {"even"}, [])
        elif "~1-3~5-7" in modifier_str:
            mock_parse.side_effect = [
                PageSpec(1, 3, 1, 1, (0, False), 1.0, set(), []),
                PageSpec(5, 7, 1, 1, (0, False), 1.0, set(), []),
            ]
        else:  # ~1-5
            mock_parse.return_value = PageSpec(1, 5, 1, 1, (0, False), 1.0, set(), [])

        omissions, remaining = _parse_omissions(modifier_str, TOTAL_PAGES)

        assert omissions == expected_omissions
        assert remaining == expected_remaining


def test_parse_omissions_invalid():
    """Tests that a malformed omission string raises an error."""
    with pytest.raises(InvalidArgumentError, match="Invalid page spec modifier 'foo'"):
        _parse_omissions("~1-5foo", TOTAL_PAGES)


def test_parse_omissions_invalid_token():
    """Tests an invalid token inside the omission."""
    with pytest.raises(InvalidArgumentError, match="should start with ~"):
        _parse_omissions("foo", TOTAL_PAGES)


# --- Test for Core Parser: parse_sub_page_spec ---


@pytest.mark.parametrize(
    "spec, total_pages, expected_spec",
    [
        # Simple ranges
        (
            "1-5",
            10,
            PageSpec(
                start=1,
                end=5,
                step=1,
                block_rep=1,
                rep=1,
                rotate=(0, False),
                scale=1.0,
                qualifiers=set(),
                omissions=[],
            ),
        ),
        (
            "1",
            10,
            PageSpec(
                start=1,
                end=1,
                step=1,
                block_rep=1,
                rep=1,
                rotate=(0, False),
                scale=1.0,
                qualifiers=set(),
                omissions=[],
            ),
        ),
        (
            "end",
            10,
            PageSpec(
                start=10,
                end=10,
                step=1,
                block_rep=1,
                rep=1,
                rotate=(0, False),
                scale=1.0,
                qualifiers=set(),
                omissions=[],
            ),
        ),
        (
            "",
            10,
            PageSpec(
                start=1,
                end=10,
                step=1,
                block_rep=1,
                rep=1,
                rotate=(0, False),
                scale=1.0,
                qualifiers=set(),
                omissions=[],
            ),
        ),  # Empty spec means all pages
        # Reverse ranges
        (
            "r1",
            10,
            PageSpec(
                start=10,
                end=10,
                step=1,
                block_rep=1,
                rep=1,
                rotate=(0, False),
                scale=1.0,
                qualifiers=set(),
                omissions=[],
            ),
        ),  # r1 = page 10
        (
            "r5",
            10,
            PageSpec(
                start=6,
                end=6,
                step=1,
                block_rep=1,
                rep=1,
                rotate=(0, False),
                scale=1.0,
                qualifiers=set(),
                omissions=[],
            ),
        ),  # r5 = 10 - 5 + 1 = 6
        (
            "r1-r5",
            10,
            PageSpec(
                start=10,
                end=6,
                step=1,
                block_rep=1,
                rep=1,
                rotate=(0, False),
                scale=1.0,
                qualifiers=set(),
                omissions=[],
            ),
        ),  # 10 down to 6
        (
            "5-1",
            10,
            PageSpec(
                start=5,
                end=1,
                step=1,
                block_rep=1,
                rep=1,
                rotate=(0, False),
                scale=1.0,
                qualifiers=set(),
                omissions=[],
            ),
        ),  # 5 down to 1
        (
            "rend-r1",
            10,
            PageSpec(
                start=1,
                end=10,
                step=1,
                block_rep=1,
                rep=1,
                rotate=(0, False),
                scale=1.0,
                qualifiers=set(),
                omissions=[],
            ),
        ),  # 1 to 10
        (
            "r0",
            10,
            PageSpec(
                start=11,
                end=11,
                step=1,
                block_rep=1,
                rep=1,
                rotate=(0, False),
                scale=1.0,
                qualifiers=set(),
                omissions=[],
            ),
        ),  # r0 = 10 - 0 + 1 = 11
        # Modifiers
        (
            "1-5even",
            10,
            PageSpec(
                start=1,
                end=5,
                step=1,
                block_rep=1,
                rep=1,
                rotate=(0, False),
                scale=1.0,
                qualifiers={"even"},
                omissions=[],
            ),
        ),
        (
            "odd",
            10,
            PageSpec(
                start=1,
                end=10,
                step=1,
                block_rep=1,
                rep=1,
                rotate=(0, False),
                scale=1.0,
                qualifiers={"odd"},
                omissions=[],
            ),
        ),
        (
            "1-endright",
            10,
            PageSpec(
                start=1,
                end=10,
                step=1,
                block_rep=1,
                rep=1,
                rotate=(90, True),
                scale=1.0,
                qualifiers=set(),
                omissions=[],
            ),
        ),
        (
            "1-10x2.0",
            10,
            PageSpec(
                start=1,
                end=10,
                step=1,
                block_rep=1,
                rep=1,
                rotate=(0, False),
                scale=2.0,
                qualifiers=set(),
                omissions=[],
            ),
        ),
        (
            "z-1",
            10,
            PageSpec(
                start=1,
                end=10,
                step=1,
                block_rep=1,
                rep=1,
                rotate=(0, False),
                scale=1 / math.sqrt(2),
                qualifiers=set(),
                omissions=[],
            ),
        ),
        (
            "1-3rep4",
            10,
            PageSpec(
                start=1,
                end=3,
                step=1,
                block_rep=1,
                rep=4,
                rotate=(0, False),
                scale=1.0,
                qualifiers=set(),
                omissions=[],
            ),
        ),
        # Complex combination
        (
            "r5-r1oddleftx1.5rep2~2-3",
            10,
            PageSpec(
                start=6,
                end=10,
                step=1,
                block_rep=1,
                rep=2,
                rotate=(-90, True),
                scale=1.5,
                qualifiers={"odd"},
                omissions=[(2, 3)],
            ),
        ),
    ],
)
def test_parse_sub_page_spec_valid(spec, total_pages, expected_spec):
    """Tests the main parse_sub_page_spec function with various valid inputs."""
    # We patch the _parse_omissions helper to simplify the test
    with patch("pdftl.utils.page_specs._parse_omissions") as mock_omissions:
        # Set the mock return for the complex case
        if "~" in spec:
            mock_omissions.return_value = ([(2, 3)], "")
        else:
            mock_omissions.return_value = ([], "")  # Default return

        result = parse_sub_page_spec(spec, total_pages)

        # Compare all fields of the dataclass
        assert result.start == expected_spec.start
        assert result.end == expected_spec.end
        assert result.step == expected_spec.step
        assert result.block_rep == expected_spec.block_rep
        assert result.rep == expected_spec.rep
        assert result.rotate == expected_spec.rotate
        assert result.scale == pytest.approx(expected_spec.scale)
        assert result.qualifiers == expected_spec.qualifiers
        assert result.omissions == expected_spec.omissions


# --- Removed 'r0' from this test ---
@pytest.mark.parametrize("spec", ["0-5", "0"])
def test_parse_sub_page_spec_invalid_range(spec):
    """Tests that a 0 or negative page number raises an error."""
    with pytest.raises(InvalidArgumentError, match="Valid page numbers start at 1"):
        parse_sub_page_spec(spec, TOTAL_PAGES)


def test_parse_range_part_invalid():
    """Tests that a spec that doesn't match the regex raises an error."""
    with patch("pdftl.utils.page_specs.SPEC_REGEX") as mock_regex:
        mock_regex.match.return_value = None
        with pytest.raises(InvalidArgumentError, match="Invalid page spec format"):
            _parse_range_part("!bad_spec!", TOTAL_PAGES)


def test_page_spec_tuple():
    """Tests the __tuple__ method of the dataclass for completeness."""
    spec = PageSpec(
        start=1,
        end=10,
        step=1,
        block_rep=1,  # New field detected in the updated schema
        rep=3,  # This was the 4th positional argument previously
        rotate=(90, True),
        scale=2.0,
        qualifiers={"even"},
        omissions=[(2, 3)],
    )

    # Expected tuple now contains 9 elements matching the new dataclass field order
    expected = (1, 10, 1, 1, 3, (90, True), 2.0, {"even"}, [(2, 3)])

    assert spec.__tuple__() == expected


# --- Tests for Filtering/Matching Functions ---


@pytest.mark.parametrize(
    "n, spec, total_pages, expected_match",
    [
        (2, "1-5", 10, True),
        (6, "1-5", 10, False),
        (2, "even", 10, True),
        (3, "even", 10, False),
        (3, "1-5odd", 10, True),
        (2, "1-5odd", 10, False),
        (3, "1-10oddevery3", 10, False),
        (7, "1-10by3odd", 10, True),
        (9, "1-10oddby3", 10, False),
        (1, "1-10oddstep3", 10, True),
        (4, "1-10~3-5", 10, False),  # Page 4 is in omission
        (6, "1-10~3-5", 10, True),
        (8, "r5-r1", 10, True),  # Range 6-10
        (5, "r5-r1", 10, False),
        (11, "r0", 10, True),  # 'r0' parses to 11, so 11 is a match
        (10, "r0", 10, False),
    ],
)
def test_page_number_matches_page_spec(n, spec, total_pages, expected_match):
    """Tests the page_number_matches_page_spec function."""
    assert page_number_matches_page_spec(n, spec, total_pages) == expected_match


def test_page_numbers_matching_page_spec():
    """Tests the single-spec page number generator."""
    spec = "1-10even~4-6"  # Evens: 2, 4, 6, 8, 10. Omit 4, 6. -> [2, 8, 10]
    total_pages = 10
    expected = [2, 8, 10]
    assert page_numbers_matching_page_spec(spec, total_pages) == expected


def test_page_numbers_matching_page_specs():
    """Tests the multi-spec page number generator."""
    specs = ["1-3", "7-8", "10odd"]  # Page 10 is odd, so it's excluded
    total_pages = 10
    expected = [1, 2, 3, 7, 8]  # 1, 2, 3 from first spec, 7, 8 from second
    assert page_numbers_matching_page_specs(specs, total_pages) == expected


def test_filter_page_numbers():
    """Tests the _filter_page_numbers helper."""
    numbers = list(range(1, 11))  # [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    qualifiers = {"even"}
    omissions = [(4, 6)]  # Omit 4, 5, 6

    # Start with evens: [2, 4, 6, 8, 10]
    # Omit 4, 5, 6: [2, 8, 10]
    expected = [2, 8, 10]
    result = _filter_page_numbers(numbers, qualifiers, omissions)
    assert result == expected


# --- Tests for High-Level Orchestration (expand_specs_to_pages) ---


@pytest.fixture
def mock_pdfs_fixture():
    """Provides mock PDFs, aliases, and inputs for expand_specs_to_pages."""
    # Create mock Pdf objects
    pdf_A = MagicMock(spec=pikepdf.Pdf)

    # FIX: Use a list comprehension to guarantee 10 distinct instances
    pdf_A.pages = [MagicMock() for _ in range(10)]

    pdf_A.filename = "A.pdf"

    pdf_B = MagicMock(spec=pikepdf.Pdf)
    pdf_B.pages = [MagicMock() for _ in range(5)]
    pdf_B.filename = "B.pdf"

    inputs = ["A.pdf", "B.pdf"]
    opened_pdfs = {0: pdf_A, 1: pdf_B}
    aliases = {"A": 0, "B": 1}

    return {
        "inputs": inputs,
        "opened_pdfs": opened_pdfs,
        "aliases": aliases,
        "pdf_A": pdf_A,
        "pdf_B": pdf_B,
    }


def test_handle_no_specs(mock_pdfs_fixture):
    """Tests the _handle_no_specs helper."""
    inputs = mock_pdfs_fixture["inputs"]
    opened_pdfs = mock_pdfs_fixture["opened_pdfs"]
    pdf_A = mock_pdfs_fixture["pdf_A"]
    pdf_B = mock_pdfs_fixture["pdf_B"]

    result = _handle_no_specs(inputs, opened_pdfs)

    # Should contain all 10 pages from A, then all 5 from B
    assert len(result) == 15
    assert result[0] == PageTransform(pdf=pdf_A, index=0, rotation=(0, False), scale=1.0)
    assert result[9] == PageTransform(pdf=pdf_A, index=9, rotation=(0, False), scale=1.0)
    assert result[10] == PageTransform(pdf=pdf_B, index=0, rotation=(0, False), scale=1.0)
    assert result[14] == PageTransform(pdf=pdf_B, index=4, rotation=(0, False), scale=1.0)


def test_resolve_alias_and_spec(mock_pdfs_fixture):
    """Tests the _resolve_alias_and_spec helper."""
    pdf_A = mock_pdfs_fixture["pdf_A"]
    pdf_B = mock_pdfs_fixture["pdf_B"]
    opened_pdfs_by_alias = {
        "A": pdf_A,
        "B": pdf_B,
        "DEFAULT": pdf_A,  # Let's say A is the default
    }

    # Case 1: Explicit alias 'B'
    pdf, spec, alias = _resolve_alias_and_spec("B1-2", opened_pdfs_by_alias, "DEFAULT")
    assert pdf is pdf_B
    assert spec == "1-2"
    assert alias == "B"

    # Case 2: Implicit default alias
    pdf, spec, alias = _resolve_alias_and_spec("1-5", opened_pdfs_by_alias, "DEFAULT")
    assert pdf is pdf_A
    assert spec == "1-5"
    assert alias == "DEFAULT"

    # Case 3: Explicit default alias '_'
    pdf, spec, alias = _resolve_alias_and_spec("_1-5", opened_pdfs_by_alias, "DEFAULT")
    assert pdf is pdf_A
    assert spec == "1-5"
    assert alias == "DEFAULT"

    # Case 4: Invalid alias. The code's logic *correctly* falls
    # back to the default alias, so this should NOT raise an error.
    pdf, spec, alias = _resolve_alias_and_spec("C1-5", opened_pdfs_by_alias, "DEFAULT")
    assert pdf is pdf_A
    assert spec == "C1-5"
    assert alias == "DEFAULT"

    # Case 5: No alias, default not found
    with pytest.raises(UserCommandLineError, match="Cannot determine a valid alias"):
        _resolve_alias_and_spec("1-5", {"A": pdf_A}, "INVALID_DEFAULT")


def test_create_page_tuples_from_numbers_out_of_range(mock_pdfs_fixture):
    """Tests that _create_page_tuples_from_numbers raises on invalid page num."""
    pdf = mock_pdfs_fixture["pdf_B"]  # 5 pages

    with pytest.raises(UserCommandLineError, match="includes page 6 but there are only 5"):
        _create_page_tuples_from_numbers(
            page_numbers=[1, 6],  # 6 is out of range
            pdf=pdf,
            rotate=(0, False),
            scale=1.0,
            spec_for_error="B1-6",
        )


# --- Test to prove 'r0' (which parses to 11) is caught ---
def test_create_page_tuples_from_numbers_catches_r0(mock_pdfs_fixture):
    """Tests that the error from 'r0' (page 11) is caught here."""
    pdf = mock_pdfs_fixture["pdf_A"]  # 10 pages

    with pytest.raises(UserCommandLineError, match="includes page 11 but there are only 10"):
        # This simulates the pipeline: 'r0' -> 11
        _create_page_tuples_from_numbers(
            page_numbers=[11],
            pdf=pdf,
            rotate=(0, False),
            scale=1.0,
            spec_for_error="Ar0",
        )


def test_expand_specs_to_pages_no_inputs():
    """Tests that a ValueError is raised if no inputs are provided."""
    with pytest.raises(ValueError, match="no inputs or opened pdfs were passed"):
        expand_specs_to_pages(specs=["1-5"])


def test_expand_specs_to_pages_no_specs(mock_pdfs_fixture, mocker):
    """Tests the main function's 'no specs' path."""
    mock_handle_no_specs = mocker.patch(
        "pdftl.utils.page_specs._handle_no_specs", return_value=["mock_page_1"]
    )

    result = expand_specs_to_pages(
        specs=[],
        aliases=mock_pdfs_fixture["aliases"],
        inputs=mock_pdfs_fixture["inputs"],
        opened_pdfs=mock_pdfs_fixture["opened_pdfs"],
    )

    mock_handle_no_specs.assert_called_once_with(
        mock_pdfs_fixture["inputs"], mock_pdfs_fixture["opened_pdfs"]
    )
    assert result == ["mock_page_1"]


def test_expand_specs_to_pages_with_specs(mock_pdfs_fixture):
    """
    Tests the main function's primary path by NOT mocking the helper.
    This is an integration test for the core logic.
    """
    specs = ["A1-2", "B1", "A5-4", "Aevenx2.0"]
    pdf_A = mock_pdfs_fixture["pdf_A"]
    pdf_B = mock_pdfs_fixture["pdf_B"]

    result = expand_specs_to_pages(
        specs=specs,
        aliases=mock_pdfs_fixture["aliases"],
        inputs=mock_pdfs_fixture["inputs"],
        opened_pdfs=mock_pdfs_fixture["opened_pdfs"],
    )

    # Check "A1-2"
    assert result[0] == PageTransform(pdf=pdf_A, index=0, rotation=(0, False), scale=1.0)
    assert result[1] == PageTransform(pdf=pdf_A, index=1, rotation=(0, False), scale=1.0)
    # Check "B1"
    assert result[2] == PageTransform(pdf=pdf_B, index=0, rotation=(0, False), scale=1.0)
    # Check "A5-4" (reverse)
    assert result[3] == PageTransform(pdf=pdf_A, index=4, rotation=(0, False), scale=1.0)
    assert result[4] == PageTransform(pdf=pdf_A, index=3, rotation=(0, False), scale=1.0)
    # Check "Aevenx2.0" (A has 10 pages)
    # Evens: 2, 4, 6, 8, 10
    assert result[5] == PageTransform(pdf=pdf_A, index=1, rotation=(0, False), scale=2.0)
    assert result[6] == PageTransform(pdf=pdf_A, index=3, rotation=(0, False), scale=2.0)
    assert result[7] == PageTransform(pdf=pdf_A, index=5, rotation=(0, False), scale=2.0)
    assert result[8] == PageTransform(pdf=pdf_A, index=7, rotation=(0, False), scale=2.0)
    assert result[9] == PageTransform(pdf=pdf_A, index=9, rotation=(0, False), scale=2.0)
    # Check total length
    assert len(result) == 10


def test_new_tuples_from_spec_str(mock_pdfs_fixture):
    """
    Directly tests the _new_tuples_from_spec_str helper.
    This is the core logic that was missed, updated with rep validation.
    """
    pdf_A = mock_pdfs_fixture["pdf_A"]
    opened_pdfs_by_alias = {"A": pdf_A, "DEFAULT": pdf_A}

    # Spec: pages 1-3, odd-numbered, rotate east (90 deg), rep 2
    spec_str = "A1-3oddeastrep2"

    result = _new_tuples_from_spec_str(spec_str, opened_pdfs_by_alias, "DEFAULT")

    # Range 1-3 -> [1, 2, 3]
    # Filter 'odd' -> [1, 3]
    # Rep '2' -> [1, 1, 3, 3]
    # Rotation 'east' -> (90, False)

    assert len(result) == 4
    assert result[0] == PageTransform(pdf=pdf_A, index=0, rotation=(90, False), scale=1.0)
    assert result[1] == PageTransform(pdf=pdf_A, index=0, rotation=(90, False), scale=1.0)
    assert result[2] == PageTransform(pdf=pdf_A, index=2, rotation=(90, False), scale=1.0)
    assert result[3] == PageTransform(pdf=pdf_A, index=2, rotation=(90, False), scale=1.0)


# --- merged from test_page_specs_coverage.py ---


def test_expand_square_brackets_logic():
    """
    Tests the internal group expansion logic to cover lines 130-149.
    """
    # 1. Test standard group expansion: [1, 3]r90 -> 1r90, 3r90
    specs = ["[1, 3]r90"]
    result = _expand_square_brackets(specs)
    assert result == ["1r90", "3r90"]

    # 2. Test mixed input (regular specs + groups)
    specs = ["5", "[1,2]x2"]
    result = _expand_square_brackets(specs)
    assert result == ["5", "1x2", "2x2"]


def test_expand_square_brackets_ambiguity_guardrail():
    """
    Tests that a comma in the suffix raises an error.
    Covers lines 136-142.
    """
    # This spec is ambiguous: does it mean ([1,2]x2), 3 OR [1,2](x2,3)?
    # The code forbids it to prevent user error.
    specs = ["[1, 2]x2, 3"]

    with pytest.raises(UserCommandLineError) as excinfo:
        _expand_square_brackets(specs)

    assert "Found a comma after the closing bracket" in str(excinfo.value)


def test_flatten_spec_list_ignores_none():
    """
    Tests that None entries are skipped.
    Covers line 240.
    """
    specs = ["1", None, "2,3"]
    result = _flatten_spec_list(specs)
    assert result == ["1", "2", "3"]


def test_flatten_spec_list_handles_empty_or_whitespace_only():
    """
    Tests that whitespace-only entries are converted to empty entries.
    """
    specs = ["1", "", "2,3"]
    result = _flatten_spec_list(specs)
    assert result == ["1", "", "2", "3"]

    specs = ["1", " ", "2,3"]
    result = _flatten_spec_list(specs)
    assert result == ["1", "", "2", "3"]


class TestPageSpecs(unittest.TestCase):
    def test_expand_square_brackets_with_none(self):
        # Input list containing a valid spec and a None value
        specs = ["[1,2]x2", None, "5"]

        # The expected behavior is that None is skipped,
        # and the valid specs are processed normally.
        expected = ["1x2", "2x2", "5"]

        try:
            result = _expand_square_brackets(specs)
            self.assertEqual(result, expected)
        except AttributeError:
            self.fail("_expand_square_brackets() raised AttributeError on None value!")


if __name__ == "__main__":
    unittest.main()


def test_parse_specs_pipeline_integration():
    """
    Covers page_specs.py lines 328-334:
    Verifies that parse_specs correctly chains _expand_square_brackets,
    _flatten_spec_list, and parse_sub_page_spec in a generator loop.
    """
    # Input contains:
    # 1. A standard range: "1-2"
    # 2. A comma-separated string that needs flattening: "4,5"
    # 3. A group syntax that needs expansion: "[6,7]x2"
    specs_input = ["1-2", "4,5", "[6,7]x2"]
    total_pages = 10

    # Execute the generator
    results = list(parse_specs(specs_input, total_pages))

    # Expectation:
    # 1. "1-2" -> 1 spec (start=1, end=2)
    # 2. "4"   -> 1 spec (start=4, end=4)
    # 3. "5"   -> 1 spec (start=5, end=5)
    # 4. "6x2" -> 1 spec (start=6, end=6, scale=2.0)
    # 5. "7x2" -> 1 spec (start=7, end=7, scale=2.0)
    assert len(results) == 5

    # Validate specific attributes to ensure the flow worked
    assert results[0].start == 1 and results[0].end == 2
    assert results[1].start == 4
    assert results[2].start == 5

    # Check scaling from the group expansion
    assert results[3].start == 6 and results[3].scale == 2.0
    assert results[4].start == 7 and results[4].scale == 2.0


def test_resolve_page_token_non_integer():
    parser = SpecParser(total_pages=10)
    with pytest.raises(InvalidArgumentError, match="Could not parse page token"):
        parser._resolve_page_token("abc", is_reverse=False)


def test_spec_parser_invalid_step_errors():
    parser = SpecParser(total_pages=10)
    with pytest.raises(InvalidArgumentError, match="Empty step value"):
        parser.parse("1-10stepeven")
    with pytest.raises(InvalidArgumentError, match="Empty step value"):
        parser.parse("1-10step")
    with pytest.raises(InvalidArgumentError, match="Invalid step value"):
        parser.parse("1-10step-4")
    with pytest.raises(InvalidArgumentError, match="Invalid step value"):
        parser.parse("1-10step0")


def test_handle_no_specs_returns_empty_when_inputs_none():
    from pdftl.utils.page_specs.resolver import _handle_no_specs

    result = _handle_no_specs(None, {})
    assert result == []


@patch("pdftl.utils.page_specs.resolver.get_visible_page_dimensions")
def test_aspect_ratio_pass_no_dimensions(mock_get_dims):
    """
    Covers line 63 in resolver.py.
    Tests the fallback when get_visible_page_dimensions returns None.
    """
    # Force the dimensions utility to return None
    mock_get_dims.return_value = None

    # Create a mock PDF with at least 1 page to bypass the line 59 check
    mock_pdf = MagicMock()
    mock_pdf.pages = [MagicMock()]

    # Evaluate a page number that exists in our mock
    result = _aspect_ratio_pass(p=1, portrait_q=True, landscape_q=False, pdf=mock_pdf)

    # If dims is None, the function should default to returning True
    assert result is True
    mock_get_dims.assert_called_once_with(mock_pdf.pages[0])


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
#  Tests for parse_sub_page_spec (The Core Parser)
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
def test_parse_sub_page_spec(spec, total_pages, expected_fields):
    """
    Tests the core parser that returns the PageSpec data structure.
    """
    # 1. Get a "default" PageSpec object by parsing a simple case.
    #    This is safer than assuming the defaults.
    #    We use a known simple spec like "1-1" on 1 page.
    default_spec = parse_sub_page_spec("1-1", 1)

    assert is_valid_page_spec(spec)

    # 2. Get the actual result from the test spec
    result = parse_sub_page_spec(spec, total_pages)

    # 3. Check all fields, comparing to the 'expected_fields' dict
    #    and falling back to the 'default_spec' for any field
    #    not in the dict.
    assert result.start == expected_fields.get("start", default_spec.start)
    assert result.end == expected_fields.get("end", default_spec.end)
    assert result.rotate == expected_fields.get("rotate", default_spec.rotate)
    assert result.scale == expected_fields.get("scale", default_spec.scale)
    assert result.qualifiers == expected_fields.get("qualifiers", default_spec.qualifiers)
    assert result.omissions == expected_fields.get("omissions", default_spec.omissions)


def test_is_invalid_page_spec_invalid():
    assert not is_valid_page_spec("foo")


# --- Tests for _parse_block_rep ---


@pytest.mark.parametrize(
    "modifier_str, expected_block_rep, expected_remaining",
    [
        ("copy3", 3, ""),
        ("copy 4", 4, ""),
        ("copy12x2.0", 12, "x2.0"),
        ("evencopy2", 2, "even"),
        ("foo", 1, "foo"),  # Default is 1
        ("rep2copy3", 3, "rep2"),  # copy consumed, rep left for _parse_rep
    ],
)
def test_parse_block_rep(modifier_str, expected_block_rep, expected_remaining):
    parser = SpecParser(total_pages=TOTAL_PAGES)
    block_rep, remaining = parser._parse_block_rep(modifier_str)
    assert block_rep == expected_block_rep
    assert remaining == expected_remaining


def test_parse_block_rep_invalid():
    parser = SpecParser(total_pages=TOTAL_PAGES)
    with pytest.raises(InvalidArgumentError, match="Invalid copy value 0. Should be at least 1."):
        parser._parse_block_rep("copy0")


# --- Tests for parse_sub_page_spec with copy ---


def test_parse_sub_page_spec_copy():
    result = parse_sub_page_spec("1-3copy2", TOTAL_PAGES)
    assert result.block_rep == 2


def test_parse_sub_page_spec_copy_default():
    result = parse_sub_page_spec("1-3", TOTAL_PAGES)
    assert result.block_rep == 1


# --- Behavioural tests via _new_tuples_from_spec_str ---


def test_new_tuples_from_spec_str_copy(mock_pdfs_fixture):
    """copy repeats the whole block: 1,2,3 -> 1,2,3,1,2,3"""
    pdf_A = mock_pdfs_fixture["pdf_A"]
    opened_pdfs_by_alias = {"A": pdf_A, "DEFAULT": pdf_A}

    result = _new_tuples_from_spec_str("A1-3copy2", opened_pdfs_by_alias, "DEFAULT")

    assert len(result) == 6
    assert [t.index for t in result] == [0, 1, 2, 0, 1, 2]


def test_new_tuples_from_spec_str_rep_vs_copy(mock_pdfs_fixture):
    """rep interleaves (1,1,2,2,3,3) while copy blocks (1,2,3,1,2,3)"""
    pdf_A = mock_pdfs_fixture["pdf_A"]
    opened_pdfs_by_alias = {"A": pdf_A, "DEFAULT": pdf_A}

    rep_result = _new_tuples_from_spec_str("A1-3rep2", opened_pdfs_by_alias, "DEFAULT")
    copy_result = _new_tuples_from_spec_str("A1-3copy2", opened_pdfs_by_alias, "DEFAULT")

    assert [t.index for t in rep_result] == [0, 0, 1, 1, 2, 2]
    assert [t.index for t in copy_result] == [0, 1, 2, 0, 1, 2]


def test_new_tuples_from_spec_str_rep_and_copy_combined(mock_pdfs_fixture):
    """rep interleaves first, then copy repeats the block:
    1-3rep2 -> 1,1,2,2,3,3 then copy2 -> 1,1,2,2,3,3,1,1,2,2,3,3"""
    pdf_A = mock_pdfs_fixture["pdf_A"]
    opened_pdfs_by_alias = {"A": pdf_A, "DEFAULT": pdf_A}

    result = _new_tuples_from_spec_str("A1-3rep2copy2", opened_pdfs_by_alias, "DEFAULT")

    assert len(result) == 12
    assert [t.index for t in result] == [0, 0, 1, 1, 2, 2, 0, 0, 1, 1, 2, 2]


# --- Tests for Whitespace Stripping / Sanitization ---


@pytest.mark.parametrize(
    "spaced_spec, total_pages, expected_start, expected_end",
    [
        ("1 - 5", 10, 1, 5),
        (" rep 2 ", 5, 1, 5),  # Strips spaces, falls back to full range, parses rep=2
        ("1-10 even step 2", 10, 1, 10),
        ("r1 - r5 x 2.0", 10, 10, 6),
        ("1 - end ~ 4", 10, 1, 10),
    ],
)
def test_spec_parser_whitespace_stripping(spaced_spec, total_pages, expected_start, expected_end):
    """
    Verifies that SpecParser.parse correctly sanitizes and strips internal
    and surrounding whitespace from tokens without breaking the modifier pipeline.
    """
    parser = SpecParser(total_pages=total_pages)
    result = parser.parse(spaced_spec)

    assert result.start == expected_start
    assert result.end == expected_end


def test_spec_parser_whitespace_logging(caplog):
    """
    Verifies that a debug message is triggered inside SpecParser.parse
    if and only if spaces were actually stripped from the original expression string.
    """
    import logging

    parser = SpecParser(total_pages=10)

    with caplog.at_level(logging.DEBUG):
        # Case 1: Has whitespace, should log
        parser.parse("1 - 3 rep 2")
        assert any("cleaned spaces: spec=" in record.message for record in caplog.records)

        caplog.clear()

        # Case 2: No whitespace, should not log the space-cleanup line
        parser.parse("1-3rep2")
        assert not any("cleaned spaces: spec=" in record.message for record in caplog.records)
