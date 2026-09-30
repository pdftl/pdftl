import shlex

import pikepdf
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from pdftl.exceptions import InvalidArgumentError
from pdftl.gui.pagesel import insert_into_args, pages_to_spec, spec_to_pages

TOTAL = 12


@pytest.fixture(scope="module")
def marked_pdfs(tmp_path_factory):
    """A has page widths 101..112, B has 201..212: every output page names its source."""
    d = tmp_path_factory.mktemp("marked")
    paths = {}
    for handle, base in (("A", 100), ("B", 200)):
        pdf = pikepdf.new()
        for i in range(1, TOTAL + 1):
            pdf.add_blank_page(page_size=(base + i, 100))
        paths[handle] = d / f"{handle}.pdf"
        pdf.save(paths[handle])
    return paths


def _widths(path):
    with pikepdf.open(path) as pdf:
        return [int(p.mediabox[2]) for p in pdf.pages]


@pytest.mark.parametrize(
    ("pages", "total", "handle", "expected"),
    [
        ([], 5, "", ""),
        ([3], 5, "", "3"),
        ([5], 5, "", "end"),
        ([1], 1, "", "1"),
        ([3, 1, 2, 2], 5, "", "1-3"),
        ([1, 2, 3, 7, 10, 11, 12], 12, "", "1-3,7,10-end"),
        ([1, 2, 3, 4, 5], 5, "", "1-end"),
        ([2, 4], 6, "B", "B2,B4"),
        ([1, 2, 3, 5, 6], 6, "B", "B1-3,B5-end"),
    ],
)
def test_pages_to_spec(pages, total, handle, expected):
    assert pages_to_spec(pages, total, handle) == expected


@pytest.mark.parametrize("handle", ["a", "AB", "1", "_", "É"])
def test_pages_to_spec_bad_handle(handle):
    with pytest.raises(ValueError, match="handle"):
        pages_to_spec([1], 3, handle)


@pytest.mark.parametrize("page", [0, 4, -1])
def test_pages_to_spec_page_out_of_range(page):
    with pytest.raises(ValueError, match="outside"):
        pages_to_spec([1, page], 3)


@settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(pages=st.sets(st.integers(1, TOTAL), min_size=1), handle=st.sampled_from(["", "B"]))
def test_spec_selects_exactly_those_pages_on_the_real_cli(
    run_pdftl, marked_pdfs, tmp_path, pages, handle
):
    out = tmp_path / "out.pdf"
    spec = pages_to_spec(pages, TOTAL, handle)
    inputs = [f"A={marked_pdfs['A']}", f"B={marked_pdfs['B']}"]
    run_pdftl([*inputs, "cat", *shlex.split(spec), "output", str(out)])
    base = 200 if handle else 100
    assert _widths(out) == [base + p for p in sorted(pages)]


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("1,3,5-6", [1, 3, 5, 6]),
        ("3,1,1", [1, 3]),
        ("5-1", [1, 2, 3, 4, 5]),
        ("end", [6]),
        ("4-end", [4, 5, 6]),
        ("1-9", [1, 2, 3, 4, 5, 6]),
    ],
)
def test_spec_to_pages(spec, expected):
    assert spec_to_pages(spec, 6) == expected


def test_spec_to_pages_round_trips():
    for pages in ([1], [2, 3, 6], [1, 2, 3, 4, 5, 6], [5, 6]):
        assert spec_to_pages(pages_to_spec(pages, 6), 6) == pages


def test_spec_to_pages_invalid():
    with pytest.raises(InvalidArgumentError):
        spec_to_pages("0", 6)


@pytest.mark.parametrize(
    ("text", "cursor", "spec", "expected"),
    [
        ("", 0, "1-3", ("1-3", 3)),
        ("east", 0, "1-3", ("1-3 east", 3)),
        ("cat", 3, "1-3", ("cat 1-3", 7)),
        ("a b", 1, "2", ("a 2 b", 3)),
        ("a  b", 2, "2", ("a 2 b", 3)),
        ("x", 99, "2", ("x 2", 3)),
        ("x", -5, "2", ("2 x", 1)),
        ("x", 1, "", ("x", 1)),
    ],
)
def test_insert_into_args(text, cursor, spec, expected):
    assert insert_into_args(text, cursor, spec) == expected
