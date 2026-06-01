# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/test_add_bookmarks.py

import pytest
import pikepdf

from pdftl.core.core_types import OpResult
from pdftl.operations.add_bookmarks import add_bookmarks, _parse_spec, _expand_title
from pdftl.utils.destinations import get_named_destinations, get_page_map, resolve_dest_to_page_num


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def setup_existing_outline(pdf: pikepdf.Pdf) -> None:
    """Adds two top-level bookmarks to pages 3 and 5."""
    with pdf.open_outline() as outline:
        outline.root.clear()
        outline.root.append(pikepdf.OutlineItem("Existing A", 2))  # page 3
        outline.root.append(pikepdf.OutlineItem("Existing B", 4))  # page 5


def get_outline_titles_and_pages(pdf: pikepdf.Pdf) -> list[tuple[str, int | None]]:
    """
    Returns a flat list of (title, 1-based page number) for every top-level
    bookmark in the outline.  page is None when the item has no destination.
    """
    page_map = get_page_map(pdf.pages)
    named_dests = get_named_destinations(pdf) or {}

    with pdf.open_outline() as outline:
        result = []
        for item in outline.root:
            resolved = resolve_dest_to_page_num(item, page_map, named_dests)
            result.append((item.title, resolved.page_num if resolved else None))
    return result


# ---------------------------------------------------------------------------
# _parse_spec unit tests
# ---------------------------------------------------------------------------


class TestParseSpec:
    def test_basic_slash_delimiter(self):
        page_range, title, position = _parse_spec("1/My Title/")
        assert page_range == "1"
        assert title == "My Title"
        assert position == "head"

    def test_hash_delimiter(self):
        page_range, title, position = _parse_spec("2-5#Chapter {page}#")
        assert page_range == "2-5"
        assert title == "Chapter {page}"
        assert position == "head"

    def test_explicit_position_tail(self):
        _, _, position = _parse_spec("1/Title/(position=tail)")
        assert position == "tail"

    def test_explicit_position_head(self):
        _, _, position = _parse_spec("1/Title/(position=head)")
        assert position == "head"

    def test_empty_title(self):
        page_range, title, position = _parse_spec("1//")
        assert page_range == "1"
        assert title == ""

    def test_title_with_spaces(self):
        _, title, _ = _parse_spec("1/Hello World/")
        assert title == "Hello World"

    def test_invalid_no_delimiter(self):
        with pytest.raises(ValueError, match="Invalid add_bookmarks spec"):
            _parse_spec("1 My Title")

    def test_invalid_position_value(self):
        with pytest.raises(ValueError, match="Invalid position="):
            _parse_spec("1/Title/(position=middle)")

    def test_unknown_option(self):
        with pytest.raises(ValueError, match="Unknown option"):
            _parse_spec("1/Title/(bold=true)")

    def test_malformed_option_no_equals(self):
        with pytest.raises(ValueError, match="expected key=value"):
            _parse_spec("1/Title/(tail)")


# ---------------------------------------------------------------------------
# _expand_title unit tests
# ---------------------------------------------------------------------------


class TestExpandTitle:
    def _make_pdf(self, num_pages=6, filename="test.pdf", filepath="/tmp/test.pdf"):
        pdf = pikepdf.new()
        for _ in range(num_pages):
            pdf.pages.append(
                pikepdf.Page(pikepdf.Dictionary(Type=pikepdf.Name.Page, MediaBox=[0, 0, 612, 792]))
            )
        pdf._source_filename = filename
        pdf._source_filepath = filepath
        return pdf

    def test_page_variable(self):
        pdf = self._make_pdf()
        assert _expand_title("Chapter {page}", 3, pdf) == "Chapter 3"

    def test_total_variable(self):
        pdf = self._make_pdf(num_pages=6)
        assert _expand_title("{total} pages", 1, pdf) == "6 pages"

    def test_filename_variable(self):
        pdf = self._make_pdf(filepath="/docs/report.pdf")
        assert _expand_title("{filename}", 1, pdf) == "report.pdf"

    def test_filename_base_variable(self):
        pdf = self._make_pdf(filepath="/docs/report.pdf")
        assert _expand_title("{filename_base}", 1, pdf) == "report"

    def test_filepath_variable(self):
        pdf = self._make_pdf(filepath="/docs/report.pdf")
        assert _expand_title("{filepath}", 1, pdf) == "/docs/report.pdf"

    def test_combined_variables(self):
        pdf = self._make_pdf(num_pages=6, filepath="/docs/report.pdf")
        result = _expand_title("{filename_base} - p.{page} of {total}", 2, pdf)
        assert result == "report - p.2 of 6"

    def test_unknown_variable_left_intact(self):
        pdf = self._make_pdf()
        assert _expand_title("{unknown_var}", 1, pdf) == "{unknown_var}"

    def test_no_variables(self):
        pdf = self._make_pdf()
        assert _expand_title("Static Title", 1, pdf) == "Static Title"


# ---------------------------------------------------------------------------
# add_bookmarks integration tests — position and ordering
# ---------------------------------------------------------------------------


def test_add_bookmarks_no_args_raises(six_page_pdf):
    """Calling with no spec args should raise ValueError."""
    pdf = pikepdf.open(six_page_pdf)
    with pytest.raises(ValueError, match="requires at least one spec"):
        add_bookmarks(pdf, [])


def test_add_bookmarks_returns_op_result(six_page_pdf):
    """Operation always returns a successful OpResult."""
    pdf = pikepdf.open(six_page_pdf)
    result = add_bookmarks(pdf, ["1/Title/"])
    assert isinstance(result, OpResult)
    assert result.success is True


def test_add_single_bookmark_empty_outline(six_page_pdf):
    """Single spec on an empty outline produces exactly one bookmark."""
    pdf = pikepdf.open(six_page_pdf)
    add_bookmarks(pdf, ["3/Chapter One/"])
    items = get_outline_titles_and_pages(pdf)
    assert items == [("Chapter One", 3)]


def test_add_bookmarks_default_position_is_head(six_page_pdf):
    """Without position= the new bookmark is prepended before existing ones."""
    pdf = pikepdf.open(six_page_pdf)
    setup_existing_outline(pdf)

    add_bookmarks(pdf, ["1/New First/"])

    items = get_outline_titles_and_pages(pdf)
    assert items[0] == ("New First", 1)
    assert items[1] == ("Existing A", 3)
    assert items[2] == ("Existing B", 5)


def test_add_bookmarks_position_head_explicit(six_page_pdf):
    """position=head explicitly is identical to the default."""
    pdf = pikepdf.open(six_page_pdf)
    setup_existing_outline(pdf)

    add_bookmarks(pdf, ["1/New First/(position=head)"])

    items = get_outline_titles_and_pages(pdf)
    assert items[0][0] == "New First"
    assert items[1][0] == "Existing A"


def test_add_bookmarks_position_tail(six_page_pdf):
    """position=tail appends after all existing bookmarks."""
    pdf = pikepdf.open(six_page_pdf)
    setup_existing_outline(pdf)

    add_bookmarks(pdf, ["6/Appendix/(position=tail)"])

    items = get_outline_titles_and_pages(pdf)
    assert items[0] == ("Existing A", 3)
    assert items[1] == ("Existing B", 5)
    assert items[2] == ("Appendix", 6)


def test_add_multiple_specs_head_and_tail(six_page_pdf):
    """Multiple specs: head items prepended in order, tail items appended in order."""
    pdf = pikepdf.open(six_page_pdf)
    setup_existing_outline(pdf)

    add_bookmarks(
        pdf,
        [
            "1/Head One/(position=head)",
            "6/Tail One/(position=tail)",
            "2/Head Two/(position=head)",
        ],
    )

    items = get_outline_titles_and_pages(pdf)
    titles = [t for t, _ in items]
    # head specs in arg order, then existing, then tail specs in arg order
    assert titles == ["Head One", "Head Two", "Existing A", "Existing B", "Tail One"]


def test_add_page_range_multiple_bookmarks(six_page_pdf):
    """A range spec produces one bookmark per matched page."""
    pdf = pikepdf.open(six_page_pdf)

    add_bookmarks(pdf, ["1-3/Chapter {page}/(position=tail)"])

    items = get_outline_titles_and_pages(pdf)
    assert items == [
        ("Chapter 1", 1),
        ("Chapter 2", 2),
        ("Chapter 3", 3),
    ]


def test_add_page_range_reversed_order(six_page_pdf):
    """A reversed range produces bookmarks in reverse page order."""
    pdf = pikepdf.open(six_page_pdf)

    add_bookmarks(pdf, ["3-1/p{page}/"])

    items = get_outline_titles_and_pages(pdf)
    pages = [p for _, p in items]
    assert pages == [3, 2, 1]


def test_add_bookmarks_on_empty_outline_tail(six_page_pdf):
    """Tail position on a PDF with no existing bookmarks still works."""
    pdf = pikepdf.open(six_page_pdf)

    add_bookmarks(pdf, ["4/Only/(position=tail)"])

    items = get_outline_titles_and_pages(pdf)
    assert items == [("Only", 4)]


def test_add_bookmarks_page_destination_correct(six_page_pdf):
    """The destination page number matches what was requested."""
    pdf = pikepdf.open(six_page_pdf)

    add_bookmarks(pdf, ["5/Target Page/"])

    items = get_outline_titles_and_pages(pdf)
    assert len(items) == 1
    assert items[0][1] == 5


def test_add_bookmarks_does_not_affect_existing_subtrees(six_page_pdf):
    """Existing children of top-level bookmarks are preserved."""
    pdf = pikepdf.open(six_page_pdf)

    # Build a 2-level outline: Page1 -> [Page2, Page3]
    with pdf.open_outline() as outline:
        outline.root.clear()
        parent = pikepdf.OutlineItem("Parent", 0)
        parent.children.append(pikepdf.OutlineItem("Child", 1))
        parent.children.append(pikepdf.OutlineItem("Child2", 2))
        outline.root.append(parent)

    add_bookmarks(pdf, ["4/New Top/(position=tail)"])

    with pdf.open_outline() as outline:
        assert len(outline.root) == 2
        assert outline.root[0].title == "Parent"
        assert len(outline.root[0].children) == 2
        assert outline.root[1].title == "New Top"


def test_add_bookmarks_odd_pages(six_page_pdf):
    """Page spec 'odd' is respected — one bookmark per odd page."""
    pdf = pikepdf.open(six_page_pdf)

    add_bookmarks(pdf, ["odd/p{page}/"])

    items = get_outline_titles_and_pages(pdf)
    pages = [p for _, p in items]
    assert pages == [1, 3, 5]


def test_add_bookmarks_multiple_specs_all_tail_order_preserved(six_page_pdf):
    """Multiple tail specs are appended in argument order."""
    pdf = pikepdf.open(six_page_pdf)

    add_bookmarks(
        pdf,
        [
            "2/Second/(position=tail)",
            "1/First/(position=tail)",
        ],
    )

    items = get_outline_titles_and_pages(pdf)
    # Tail specs in arg order: spec-1 page 2, spec-2 page 1
    assert items[0] == ("Second", 2)
    assert items[1] == ("First", 1)


# ---------------------------------------------------------------------------
# Integration test via CLI runner
# ---------------------------------------------------------------------------


def test_add_bookmarks_cli_pipeline(runner, six_page_pdf, temp_dir):
    """Verifies end-to-end command line syntax via the pdftl execution harness."""
    in_pdf = temp_dir / "input.pdf"
    out_pdf = temp_dir / "output.pdf"

    # Prepare: give the file a simple existing outline
    with pikepdf.open(six_page_pdf) as pdf:
        with pdf.open_outline() as outline:
            outline.root.clear()
            outline.root.append(pikepdf.OutlineItem("Existing", 2))
        pdf.save(in_pdf)

    runner.run("pdftl", [str(in_pdf), "add_bookmarks", "1/New Cover/", "output", str(out_pdf)])

    with pikepdf.open(out_pdf) as pdf:
        items = get_outline_titles_and_pages(pdf)
    # "New Cover" prepended before "Existing"
    assert items[0] == ("New Cover", 1)
    assert items[1] == ("Existing", 3)
