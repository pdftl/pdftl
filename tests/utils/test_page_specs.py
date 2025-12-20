import math
from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.exceptions import InvalidArgumentError, UserCommandLineError

# --- Import the module and functions to test ---
from pdftl.utils.page_specs import (
    PageSpec,
    PageTransform,
    _create_page_tuples_from_numbers,
    _filter_page_numbers,
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
    page_number_matches_page_spec,
    page_numbers_matching_page_spec,
    page_numbers_matching_page_specs,
    parse_page_spec,
)

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
    # We patch the main parse_page_spec function that _parse_omissions
    # calls recursively.
    with patch("pdftl.utils.page_specs.parse_page_spec") as mock_parse:
        # Define the side effects for the recursive calls
        if "~even" in modifier_str:
            mock_parse.return_value = PageSpec(1, 10, (0, False), 1.0, {"even"}, [])
        elif "~1-3~5-7" in modifier_str:
            mock_parse.side_effect = [
                PageSpec(1, 3, (0, False), 1.0, set(), []),
                PageSpec(5, 7, (0, False), 1.0, set(), []),
            ]
        else:  # ~1-5
            mock_parse.return_value = PageSpec(1, 5, (0, False), 1.0, set(), [])

        omissions, remaining = _parse_omissions(modifier_str, TOTAL_PAGES)

        assert omissions == expected_omissions
        assert remaining == expected_remaining


def test_parse_omissions_invalid():
    """Tests that a malformed omission string raises an error."""
    with pytest.raises(InvalidArgumentError, match="Invalid part 'foo'"):
        _parse_omissions("~1-5foo", TOTAL_PAGES)


def test_parse_omissions_invalid_token():
    """Tests an invalid token inside the omission."""
    with pytest.raises(InvalidArgumentError, match="should start with ~"):
        _parse_omissions("foo", TOTAL_PAGES)


# --- Test for Core Parser: parse_page_spec ---


@pytest.mark.parametrize(
    "spec, total_pages, expected_spec",
    [
        # Simple ranges
        ("1-5", 10, PageSpec(1, 5, (0, False), 1.0, set(), [])),
        ("1", 10, PageSpec(1, 1, (0, False), 1.0, set(), [])),
        ("end", 10, PageSpec(10, 10, (0, False), 1.0, set(), [])),
        (
            "",
            10,
            PageSpec(1, 10, (0, False), 1.0, set(), []),
        ),  # Empty spec means all pages
        # Reverse ranges
        ("r1", 10, PageSpec(10, 10, (0, False), 1.0, set(), [])),  # r1 = page 10
        ("r5", 10, PageSpec(6, 6, (0, False), 1.0, set(), [])),  # r5 = 10 - 5 + 1 = 6
        ("r1-r5", 10, PageSpec(10, 6, (0, False), 1.0, set(), [])),  # 10 down to 6
        ("5-1", 10, PageSpec(5, 1, (0, False), 1.0, set(), [])),  # 5 down to 1
        ("rend-r1", 10, PageSpec(1, 10, (0, False), 1.0, set(), [])),  # 1 to 10
        (
            "r0",
            10,
            PageSpec(11, 11, (0, False), 1.0, set(), []),
        ),  # r0 = 10 - 0 + 1 = 11
        # Modifiers
        ("1-5even", 10, PageSpec(1, 5, (0, False), 1.0, {"even"}, [])),
        ("odd", 10, PageSpec(1, 10, (0, False), 1.0, {"odd"}, [])),
        ("1-endright", 10, PageSpec(1, 10, (90, True), 1.0, set(), [])),
        ("1-10x2.0", 10, PageSpec(1, 10, (0, False), 2.0, set(), [])),
        ("z-1", 10, PageSpec(1, 10, (0, False), 1 / math.sqrt(2), set(), [])),
        # Complex combination
        (
            "r5-r1oddleftx1.5~2-3",
            10,
            PageSpec(6, 10, (-90, True), 1.5, {"odd"}, [(2, 3)]),
        ),
    ],
)
def test_parse_page_spec_valid(spec, total_pages, expected_spec):
    """Tests the main parse_page_spec function with various valid inputs."""
    # We patch the _parse_omissions helper to simplify the test
    with patch("pdftl.utils.page_specs._parse_omissions") as mock_omissions:
        # Set the mock return for the complex case
        if "~" in spec:
            mock_omissions.return_value = ([(2, 3)], "")
        else:
            mock_omissions.return_value = ([], "")  # Default return

        result = parse_page_spec(spec, total_pages)

        # Compare all fields of the dataclass
        assert result.start == expected_spec.start
        assert result.end == expected_spec.end
        assert result.rotate == expected_spec.rotate
        assert result.scale == pytest.approx(expected_spec.scale)
        assert result.qualifiers == expected_spec.qualifiers
        assert result.omissions == expected_spec.omissions


# --- Removed 'r0' from this test ---
@pytest.mark.parametrize("spec", ["0-5", "0"])
def test_parse_page_spec_invalid_range(spec):
    """Tests that a 0 or negative page number raises an error."""
    with pytest.raises(InvalidArgumentError, match="Valid page numbers start at 1"):
        parse_page_spec(spec, TOTAL_PAGES)


def test_parse_range_part_invalid():
    """Tests that a spec that doesn't match the regex raises an error."""
    with patch("pdftl.utils.page_specs.SPEC_REGEX") as mock_regex:
        mock_regex.match.return_value = None
        with pytest.raises(InvalidArgumentError, match="Invalid page spec format"):
            _parse_range_part("!bad_spec!", TOTAL_PAGES)


def test_page_spec_tuple():
    """Tests the __tuple__ method of the dataclass for completeness."""
    spec = PageSpec(1, 10, (90, True), 2.0, {"even"}, [(2, 3)])
    expected = (1, 10, (90, True), 2.0, {"even"}, [(2, 3)])
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
    pdf_A.pages = [MagicMock()] * 10  # 10 pages
    pdf_A.filename = "A.pdf"

    pdf_B = MagicMock(spec=pikepdf.Pdf)
    pdf_B.pages = [MagicMock()] * 5  # 5 pages
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
    aliases = mock_pdfs_fixture["aliases"]
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
    with pytest.raises(ValueError, match="inputs were not passed"):
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
    This is the core logic that was missed.
    """
    pdf_A = mock_pdfs_fixture["pdf_A"]
    opened_pdfs_by_alias = {"A": pdf_A, "DEFAULT": pdf_A}

    # Spec: pages 1-3, odd-numbered, rotate east (90 deg)
    spec_str = "A1-3oddeast"

    result = _new_tuples_from_spec_str(spec_str, opened_pdfs_by_alias, "DEFAULT")

    # Range 1-3 -> [1, 2, 3]
    # Filter 'odd' -> [1, 3]
    # Rotation 'east' -> (90, False)

    assert len(result) == 2
    assert result[0] == PageTransform(pdf=pdf_A, index=0, rotation=(90, False), scale=1.0)
    assert result[1] == PageTransform(pdf=pdf_A, index=2, rotation=(90, False), scale=1.0)
