# tests/operations/test_burst.py
import io
import logging
from unittest.mock import MagicMock, call, patch

import pikepdf
import pytest

from pdftl.core.core_types import OpResult
from pdftl.exceptions import InvalidArgumentError, OperationError
from pdftl.operations.burst import (
    _generate_burst_chunks,
    _make_chunk_pdf,
    burst_cli_hook,
    burst_pdf,
    get_chunk_size,
    get_effective_specs,
)
from pdftl.utils.page_labels import get_all_page_label_dicts


def test_burst_basic(two_page_pdf):
    """Test standard bursting of a 2-page PDF."""
    # The fixture returns a path, so we must open it
    with pikepdf.open(two_page_pdf) as pdf:
        # burst_pdf now returns an OpResult
        result = burst_pdf([pdf])

        assert isinstance(result, OpResult)
        assert result.success

        # The generator is inside result.data
        results = list(result.data)

        assert len(results) == 2

        filename1, pdf1 = results[0]
        assert filename1 == "pg_0001.pdf"
        assert len(pdf1.pages) == 1

        filename2, pdf2 = results[1]
        assert filename2 == "pg_0002.pdf"
        assert len(pdf2.pages) == 1


def test_burst_custom_pattern(two_page_pdf):
    """Test that output_pattern argument works."""
    with pikepdf.open(two_page_pdf) as pdf:
        result = burst_pdf([pdf], output_pattern="page_%d.pdf")
        assert result.success

        # Unwrap result.data to access the generator
        results = list(result.data)

        assert len(results) == 2
        assert results[0][0] == "page_1.pdf"
        assert results[1][0] == "page_2.pdf"


def test_burst_invalid_pattern(two_page_pdf):
    """Test that the InvalidArgumentError is raised for bad patterns."""
    with pikepdf.open(two_page_pdf) as pdf:
        result = burst_pdf([pdf], output_pattern="bad_filename.pdf")

        # The ValueError is raised inside the generator, so we must access
        # result.data (the result pdf) and try to iterate it to trigger the error.
        with pytest.raises(InvalidArgumentError, match="Output pattern must include"):
            list(result.data)


def test_burst_multiple_inputs(two_page_pdf):
    """Test passing multiple PDF documents at once."""
    with pikepdf.open(two_page_pdf) as pdf:
        # Pass the same PDF object twice to simulate multiple inputs
        result = burst_pdf([pdf, pdf])
        assert result.success

        # Unwrap result.data (the result pdf)
        results = list(result.data)

        # 2 pages * 2 inputs = 4 output files
        assert len(results) == 4
        # Counter should increment continuously
        assert results[0][0] == "pg_0001.pdf"
        assert results[3][0] == "pg_0004.pdf"


def test_burst_pdf_default_pattern(two_page_pdf):
    """
    Covers line 78: pattern = "pg_%04d.pdf" when pattern is None.
    """
    with pikepdf.open(two_page_pdf) as pdf:
        result = burst_pdf([pdf], output_pattern=None)

        # We must iterate the generator to execute the body of the function
        generated_files = list(result.data)

        # Verify the default pattern was applied (pg_0001.pdf)
        assert generated_files[0][0] == "pg_0001.pdf"


def test_burst_cli_hook_success():
    """Test that the hook iterates the generator and calls pipeline.save_pdf_file."""
    # 1. Setup mocks
    mock_pipeline = MagicMock()
    mock_stage = MagicMock()

    # Create dummy data to be yielded by the generator
    dummy_pdf_1 = MagicMock()
    dummy_pdf_2 = MagicMock()
    dummy_input_pdf = pikepdf.new()

    def mock_generator():
        yield ("page_1.pdf", dummy_pdf_1)
        yield ("page_2.pdf", dummy_pdf_2)

    # Wrap in OpResult
    result = OpResult(success=True, data=mock_generator(), pdf=dummy_input_pdf)

    # 2. Execute hook
    with patch("pdftl.operations.burst.pdftl.api.dump_data") as mock_dump:
        burst_cli_hook(result, mock_stage, mock_pipeline)
        mock_dump.assert_called_once_with(result.pdf, output="doc_data.txt", run_cli_hook=True)

    # 3. Assertions
    # Verify pipeline.save_pdf_file was called for each yielded item
    expected_calls = [
        call(dummy_pdf_1, "page_1.pdf", mock_stage),
        call(dummy_pdf_2, "page_2.pdf", mock_stage),
    ]
    mock_pipeline.save_pdf_file.assert_has_calls(expected_calls)
    assert mock_pipeline.save_pdf_file.call_count == 2


def test_burst_cli_hook_no_generator(caplog):
    """Test the 'if not burst_generator' guard clause (Line 45)."""
    mock_pipeline = MagicMock()
    mock_stage = MagicMock()

    # Result with no data
    result = OpResult(success=True, data=None)

    with caplog.at_level("DEBUG"):
        burst_cli_hook(result, mock_stage, mock_pipeline)

    # Assertions
    assert "No burst_generator" in caplog.text
    mock_pipeline.save_pdf_file.assert_not_called()


def test_burst_cli_hook_empty_generator():
    mock_pipeline = MagicMock()
    mock_stage = MagicMock()
    # Create a mock that satisfies isinstance(obj, pikepdf.Pdf)
    mock_pdf = MagicMock(spec=pikepdf.Pdf)

    def empty_gen():
        if False:
            yield

    # Pass the mock_pdf here!
    result = OpResult(success=True, data=empty_gen(), pdf=mock_pdf)

    with patch("pdftl.operations.burst.pdftl.api.dump_data") as mock_dump:
        burst_cli_hook(result, mock_stage, mock_pipeline)
        mock_dump.assert_called_once_with(result.pdf, output="doc_data.txt", run_cli_hook=True)

    # Assertions
    mock_pipeline.save_pdf_file.assert_not_called()


# --- Imports from your module ---


@pytest.fixture
def mock_pdf():
    """Provides a dummy PDF object for the tests."""
    return MagicMock(name="SourcePDF")


# --- 1. Testing Valid Specs ---
@pytest.mark.parametrize(
    "input_specs, mock_pages, expected_calls, expected_output",
    [
        # Case 1: Standard specs are ignored and passed through
        (["1-5", "odd", "even"], [], [], ["1-5", "odd", "even"]),
        # Case 2: Basic "level" spec is processed (case-insensitive)
        (["level2"], [1, 5, 12], [(2, False)], ["1,5,12"]),
        (["LEVEL2"], [1, 5, 12], [(2, False)], ["1,5,12"]),
        # Case 3: "level<n>onl" spec is processed (last_level_only=True)
        (["level3only"], [4, 8], [(3, True)], ["4,8"]),
        (["Level3Only"], [4, 8], [(3, True)], ["4,8"]),
        # Case 4: Mixed list of standard specs and level specs
        (["1-3", "level1", "end-5"], [10, 20], [(1, False)], ["1-3", "10,20", "end-5"]),
        # Case 5: Multiple level specs in the same list
        (["level1", "level2only"], [7, 9], [(1, False), (2, True)], ["7,9", "7,9"]),
    ],
)
@patch("pdftl.operations.burst.get_outlines_to_level_pages")
def test_get_effective_specs_valid(
    mock_get_pages, mock_pdf, input_specs, mock_pages, expected_calls, expected_output
):
    # Setup the mock to return our predetermined list of pages
    mock_get_pages.return_value = mock_pages

    # Run the function
    result = get_effective_specs(mock_pdf, input_specs)

    # Assert the output matches exactly what we expect
    assert result == expected_output

    # Assert the mocked helper was called the correct number of times
    assert mock_get_pages.call_count == len(expected_calls)

    # Assert the mocked helper was called with the correct arguments
    for level, eq_flag in expected_calls:
        mock_get_pages.assert_any_call(mock_pdf, level, last_level_only=eq_flag)


# --- 2. Testing Invalid Inputs and Exceptions ---
@pytest.mark.parametrize(
    "bad_spec, expected_error_match",
    [
        ("level0", "must be at least 1"),
        ("level-5", "must be at least 1"),
        ("levelabc", "invalid literal for int"),
        ("levelXYZonly", "invalid literal for int"),
    ],
)
@patch("pdftl.operations.burst.get_outlines_to_level_pages")
def test_get_effective_specs_invalid(mock_get_pages, mock_pdf, bad_spec, expected_error_match):
    # We don't expect the helper to ever be called because it should fail during parsing
    with pytest.raises(InvalidArgumentError, match=expected_error_match):
        get_effective_specs(mock_pdf, [bad_spec])

    mock_get_pages.assert_not_called()


def test_burst_multiple_sizes_raises_error():
    # You may need to mock 'opened_pdfs' depending on your existing fixtures.
    # A simple list with a dummy object usually works for argument routing tests.
    mock_pdfs = ["dummy_pdf_object"]

    with pytest.raises(InvalidArgumentError, match="More than one `size` spec passed"):
        burst_pdf(mock_pdfs, operation_args=["size5M", "size10M"])


@pytest.fixture
def dummy_5_page_pdf():
    """Helper fixture to generate a real, empty 5-page PDF in memory."""
    pdf = pikepdf.Pdf.new()
    for _ in range(5):
        pdf.add_blank_page(page_size=(612, 792))
    return pdf


def test_burst_page_exceeds_max_size_warning(dummy_5_page_pdf, caplog):
    """Tests lines 240-250 where a single page is bigger than max_bytes."""

    # Force max_bytes to be ridiculously small (10 bytes) so even 1 page fails the limit
    max_bytes = 10

    with caplog.at_level(logging.WARNING):
        generator = _generate_burst_chunks(
            opened_pdfs=[dummy_5_page_pdf],
            specs=["1"],
            output_pattern="foo%03d.pdf",
            max_bytes=max_bytes,
        )
        results = list(generator)

    # CHANGED: It processed a 5 page PDF where every page failed the size check,
    # so it should fallback to yielding all 5 individual pages.
    assert len(results) == 5
    assert len(results[0][1].pages) == 1

    # Ensure the warning was triggered
    assert "exceeds the maximum limit" in caplog.text


def test_burst_by_size_binary_search(dummy_5_page_pdf):
    # PDF libraries heavily compress/deduplicate identical blank pages.
    # To guarantee a max of 2 pages, we measure the exact sizes dynamically.
    two_page_size = get_chunk_size(dummy_5_page_pdf, 0, 1)
    three_page_size = get_chunk_size(dummy_5_page_pdf, 0, 2)

    # Set max_bytes right between a 2-page and 3-page document
    max_bytes = (two_page_size + three_page_size) // 2

    generator = _generate_burst_chunks(
        opened_pdfs=[dummy_5_page_pdf],
        specs=["1"],
        output_pattern="chunk_%02d.pdf",
        max_bytes=max_bytes,
    )

    results = list(generator)

    # We expect 5 pages split into chunks of max 2 pages -> 3 total chunks (2, 2, 1)
    assert len(results) == 3

    assert results[0][0] == "chunk_01.pdf"
    assert len(results[0][1].pages) == 2

    assert results[1][0] == "chunk_02.pdf"
    assert len(results[1][1].pages) == 2

    assert results[2][0] == "chunk_03.pdf"
    assert len(results[2][1].pages) == 1


def test_burst_pdf_standard_spec_routing():
    """Covers line 165 by routing a standard, non-size spec through the wrapper."""
    mock_pdfs = ["dummy_pdf_object"]

    # Passing "1-3" triggers the `else` block (line 165)
    # Passing "size5M" triggers the `if` block
    result = burst_pdf(mock_pdfs, operation_args=["1-3", "size5M"])

    assert result.success is True
    assert result.pdf == "dummy_pdf_object"


def test_burst_cli_hook_raises_on_missing_pdf():
    # Pass a result where success is True but pdf is None
    result = OpResult(success=True, data=[("file.pdf", MagicMock())], pdf=None)
    with pytest.raises(OperationError, match="Invalid result: not a PDF"):
        burst_cli_hook(result, MagicMock(), MagicMock())


# --- Regression tests: burst must preserve outlines/links (via add_pages) ---
#
# Prior to the fix, `_make_chunk_pdf` built each chunk with a raw
# `new_pdf.pages.extend(pages[start:end])`. That silently dropped document
# structures that live OUTSIDE the page tree -- /Root/Outlines (bookmarks)
# and /Root/Names/Dests (named destinations) -- even when they pointed
# entirely within a single chunk. (On-page link annotations with an inline
# *array* destination, e.g. `D=[page_obj, /Fit]`, already survived raw page
# slicing reasonably well, since that's handled natively by qpdf's
# page-selection logic; those tests below mainly guard against future
# regressions in `add_pages` itself, rather than reproducing the original
# bug.)


@pytest.fixture
def pdf_with_internal_navigation():
    """
    A 4-page PDF with:
      - Bookmarks: 'Chapter 1' -> page 0, 'Chapter 2' -> page 2
      - An "intra-chunk" array-destination link on page 0 pointing to page 1
      - A "cross-chunk" array-destination link on page 1 pointing to page 3

    Bursting this with split points at pages 1 and 3 produces two
    2-page chunks: [page0, page1] and [page2, page3]. So 'Chapter 1'
    and the intra-chunk link stay fully within chunk 1, while the
    cross-chunk link's target (page 3) ends up in chunk 2.
    """
    pdf = pikepdf.Pdf.new()
    for _ in range(4):
        pdf.add_blank_page(page_size=(200, 200))

    with pdf.open_outline() as outline:
        outline.root.append(
            pikepdf.OutlineItem("Chapter 1", destination=[pdf.pages[0].obj, pikepdf.Name.Fit])
        )
        outline.root.append(
            pikepdf.OutlineItem("Chapter 2", destination=[pdf.pages[2].obj, pikepdf.Name.Fit])
        )

    intra_link = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Link,
        Rect=pikepdf.Array([0, 0, 50, 50]),
        A=pikepdf.Dictionary(S=pikepdf.Name.GoTo, D=[pdf.pages[1].obj, pikepdf.Name.Fit]),
    )
    pdf.pages[0].Annots = pdf.make_indirect(pikepdf.Array([pdf.make_indirect(intra_link)]))

    cross_link = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Link,
        Rect=pikepdf.Array([0, 0, 50, 50]),
        A=pikepdf.Dictionary(S=pikepdf.Name.GoTo, D=[pdf.pages[3].obj, pikepdf.Name.Fit]),
    )
    pdf.pages[1].Annots = pdf.make_indirect(pikepdf.Array([pdf.make_indirect(cross_link)]))

    return pdf


@pytest.fixture
def pdf_with_named_destination_link():
    """
    A 2-page PDF where page 0 has a link that points to a NAMED
    destination ("GoToPage1", stored in /Root/Names/Dests) rather than
    an inline destination array. Named destinations live entirely outside
    the page tree, so they are a sharper regression check than array-style
    destinations: raw page slicing drops /Root/Names entirely, which left
    a dangling, unresolvable link (pointing at a name that no longer
    exists anywhere in the output file) rather than a cleanly pruned one.
    """
    pdf = pikepdf.Pdf.new()
    for _ in range(2):
        pdf.add_blank_page(page_size=(200, 200))

    dests_tree = pikepdf.NameTree.new(pdf)
    dests_tree["GoToPage1"] = pikepdf.Array([pdf.pages[1].obj, pikepdf.Name.Fit])
    pdf.Root.Names = pdf.make_indirect(pikepdf.Dictionary(Dests=dests_tree.obj))

    named_link = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Link,
        Rect=pikepdf.Array([0, 0, 50, 50]),
        A=pikepdf.Dictionary(S=pikepdf.Name.GoTo, D="GoToPage1"),
    )
    pdf.pages[0].Annots = pdf.make_indirect(pikepdf.Array([pdf.make_indirect(named_link)]))

    return pdf


def _outline_titles(pdf):
    with pdf.open_outline() as outline:
        return [item.title for item in outline.root]


def test_burst_preserves_outlines_within_each_chunk(pdf_with_internal_navigation):
    """Each chunk should keep only the bookmark(s) for the pages it contains,
    correctly retargeted to a page that actually exists in that chunk."""
    results = list(
        _generate_burst_chunks(
            opened_pdfs=[pdf_with_internal_navigation],
            specs=["1", "3"],
            output_pattern="chunk_%d.pdf",
        )
    )
    assert len(results) == 2
    _, chunk1 = results[0]
    _, chunk2 = results[1]

    assert len(chunk1.pages) == 2
    assert len(chunk2.pages) == 2

    assert _outline_titles(chunk1) == ["Chapter 1"]
    assert _outline_titles(chunk2) == ["Chapter 2"]

    with chunk1.open_outline() as outline:
        dest_page = outline.root[0].destination[0]
        assert any(p.objgen == dest_page.objgen for p in chunk1.pages)


def test_burst_remaps_intra_chunk_links_and_drops_cross_chunk_links(
    pdf_with_internal_navigation,
):
    """On-page links to another page in the SAME chunk must be retargeted
    to the correct local page object. Links to a page that lands in a
    DIFFERENT chunk must be pruned safely rather than left dangling, and
    the resulting chunk must still be a valid, saveable PDF."""
    results = list(
        _generate_burst_chunks(
            opened_pdfs=[pdf_with_internal_navigation],
            specs=["1", "3"],
            output_pattern="chunk_%d.pdf",
        )
    )
    _, chunk1 = results[0]

    # Original page 0 -> local page 0: intra-chunk link to original page 1
    # (now local page 1) should resolve to a page within this same chunk.
    page0_annots = chunk1.pages[0].Annots
    assert len(page0_annots) == 1
    intra_dest_page = page0_annots[0].A.D[0]
    assert intra_dest_page.objgen == chunk1.pages[1].obj.objgen

    # Original page 1 -> local page 1: cross-chunk link to original page 3
    # (which is not part of this chunk) must not reference a foreign/missing
    # page object.
    page1_annots = chunk1.pages[1].Annots
    assert len(page1_annots) == 1
    cross_dest_page = page1_annots[0].A.D[0]
    assert cross_dest_page is None

    # Sanity: the chunk must still save out as a structurally valid PDF.
    buf = io.BytesIO()
    chunk1.save(buf)


def test_burst_preserves_named_destination_links(pdf_with_named_destination_link):
    """Regression test: a link to a NAMED destination (rather than an
    inline destination array) must survive burst. The /Root/Names/Dests
    name tree has to be rebuilt in the chunk, and the name must resolve
    to the correct local page -- not be left as a dangling string
    reference to a name tree that no longer exists."""
    results = list(
        _generate_burst_chunks(
            opened_pdfs=[pdf_with_named_destination_link],
            specs=["1"],
            output_pattern="chunk_%d.pdf",
        )
    )
    assert len(results) == 1
    _, chunk = results[0]

    # The link annotation itself should still reference the name by string.
    annot = chunk.pages[0].Annots[0]
    assert annot.A.D == "GoToPage1"

    # And that name must actually be resolvable in the output PDF's own
    # name tree, pointing at the correct (local) destination page.
    assert "/Names" in chunk.Root, "/Root/Names was dropped -- named destination is now dangling"
    assert "/Dests" in chunk.Root.Names

    name_tree = pikepdf.NameTree(chunk.Root.Names.Dests)
    assert "GoToPage1" in name_tree

    resolved = name_tree["GoToPage1"]
    # pdftl stores resolved named destinations as a {/D: [...]} dict.
    target_page = resolved.D[0]
    assert target_page.objgen == chunk.pages[1].obj.objgen

    # Sanity: the chunk must still save out as a structurally valid PDF.
    buf = io.BytesIO()
    chunk.save(buf)


# --- Regression tests: burst must preserve original page-label identity ---
#
# Each burst chunk is a NEW pdf built from a contiguous slice of the source
# document, so there's no in-place-mutation ordering hazard here (unlike
# delete/insert/move) -- remap_page_labels just needs to run once per chunk
# against the (untouched) source. This is an intentional pdftk deviation:
# classic pdftk burst has no concept of preserving original numbering per
# chunk. See CHANGELOG.


@pytest.fixture
def book_with_chapters():
    """An 8-page 'book' with roman-numeral front matter (pages 1-4, i..iv)
    and decimal body pages (pages 5-8, 1..4), plus chapter bookmarks used
    as burst split points."""
    pdf = pikepdf.Pdf.new()
    for _ in range(8):
        pdf.add_blank_page(page_size=(200, 200))

    nums = [
        0,
        pikepdf.Dictionary(St=1, S=pikepdf.Name("/r")),  # pages 0-3: i..iv
        4,
        pikepdf.Dictionary(St=1, S=pikepdf.Name("/D")),  # pages 4-7: 1..4
    ]
    pdf.Root.PageLabels = pdf.make_indirect(pikepdf.Dictionary(Nums=pikepdf.Array(nums)))

    with pdf.open_outline() as outline:
        outline.root.append(
            pikepdf.OutlineItem("Front Matter", destination=[pdf.pages[0].obj, pikepdf.Name.Fit])
        )
        outline.root.append(
            pikepdf.OutlineItem("Chapter 1", destination=[pdf.pages[4].obj, pikepdf.Name.Fit])
        )

    return pdf


@pytest.fixture
def book_with_implicit_decimal_body():
    """Like book_with_chapters, but the body section's rule has NO /S key
    at all (relying on the PDF-spec default of plain decimal), rather than
    an explicit S=/D. Lets tests distinguish 'no style set' from
    'explicitly decimal style' without ambiguity."""
    pdf = pikepdf.Pdf.new()
    for _ in range(8):
        pdf.add_blank_page(page_size=(200, 200))

    nums = [
        0,
        pikepdf.Dictionary(St=1, S=pikepdf.Name("/r")),  # pages 0-3: i..iv
        4,
        pikepdf.Dictionary(St=1),  # pages 4-7: 1..4, no /S key at all
    ]
    pdf.Root.PageLabels = pdf.make_indirect(pikepdf.Dictionary(Nums=pikepdf.Array(nums)))
    return pdf


def test_burst_preserves_absence_of_explicit_style(book_with_implicit_decimal_body):
    """When the source rule has no /S key, chunks taken from that section
    must not gain one either -- distinct from the S=/D case covered
    elsewhere, where the source explicitly sets decimal style."""
    chunk = _make_chunk_pdf(book_with_implicit_decimal_body, start_idx=5, end_idx=7)
    labels = get_all_page_label_dicts(chunk)
    assert [d["St"] for d in labels] == [2, 3, 4]
    assert all("S" not in d for d in labels)


def test_burst_by_bookmark_preserves_original_page_numbers(book_with_chapters):
    """Splitting at chapter boundaries (level1 bookmarks) should give each
    chunk labels that match its ORIGINAL position in the book, not a
    restarted 1-based sequence local to the chunk."""
    results = list(
        _generate_burst_chunks(
            opened_pdfs=[book_with_chapters],
            specs=["level1"],
            output_pattern="chunk_%d.pdf",
        )
    )
    assert len(results) == 2
    _, front_matter = results[0]
    _, chapter1 = results[1]

    front_labels = get_all_page_label_dicts(front_matter)
    assert [d["St"] for d in front_labels] == [1, 2, 3, 4]
    assert all(d["S"] == pikepdf.Name("/r") for d in front_labels)

    chapter1_labels = get_all_page_label_dicts(chapter1)
    assert [d["St"] for d in chapter1_labels] == [1, 2, 3, 4]
    assert all(d["S"] == pikepdf.Name("/D") for d in chapter1_labels)


def test_burst_single_pages_each_keep_their_own_original_number(book_with_chapters):
    """Classic pdftk-style single-page burst: even here, each single-page
    output file should carry the page's real original number/style rather
    than defaulting to '1' for every file."""
    results = list(
        _generate_burst_chunks(
            opened_pdfs=[book_with_chapters],
            specs=["1-end"],
            output_pattern="pg_%04d.pdf",
        )
    )
    assert len(results) == 8

    # page 2 (0-based idx 1) -> should be labeled "ii", not "i"/"1"
    _, page2_chunk = results[1]
    labels = get_all_page_label_dicts(page2_chunk)
    assert labels == [{"St": 2, "S": pikepdf.Name("/r")}]

    # page 6 (0-based idx 5) -> should be labeled "2" (decimal body), not "1"
    _, page6_chunk = results[5]
    labels = get_all_page_label_dicts(page6_chunk)
    assert labels == [{"St": 2, "S": pikepdf.Name("/D")}]


def test_burst_step_split_preserves_numbers_across_arbitrary_boundaries(book_with_chapters):
    """Fixed-size step splitting (not aligned to the label-style boundary)
    should still correctly split styles at the point where they actually
    change, e.g. step3 on an 8-page book with the roman/decimal boundary
    at page 5 produces a chunk straddling both styles."""
    results = list(
        _generate_burst_chunks(
            opened_pdfs=[book_with_chapters],
            specs=["step3"],
            output_pattern="chunk_%d.pdf",
        )
    )
    # 8 pages, step 3 -> chunks of [1-3], [4-6], [7-8]
    assert len(results) == 3
    _, chunk1 = results[0]
    _, chunk2 = results[1]
    _, chunk3 = results[2]

    assert [d["St"] for d in get_all_page_label_dicts(chunk1)] == [1, 2, 3]
    assert all(d["S"] == pikepdf.Name("/r") for d in get_all_page_label_dicts(chunk1))

    # chunk2 straddles the roman/decimal boundary: original pages 4(iv),5(1),6(2)
    chunk2_labels = get_all_page_label_dicts(chunk2)
    assert chunk2_labels[0] == {"St": 4, "S": pikepdf.Name("/r")}
    assert chunk2_labels[1] == {"St": 1, "S": pikepdf.Name("/D")}
    assert chunk2_labels[2] == {"St": 2, "S": pikepdf.Name("/D")}

    assert [d["St"] for d in get_all_page_label_dicts(chunk3)] == [3, 4]


def test_burst_source_without_page_labels_produces_chunks_without_labels(two_page_pdf):
    """If the source PDF has no /PageLabels at all, chunks shouldn't have
    any synthesized labels either -- consistent with remap_page_labels'
    no-source-labels short circuit."""
    with pikepdf.open(two_page_pdf) as pdf:
        results = list(
            _generate_burst_chunks(
                opened_pdfs=[pdf],
                specs=["1-end"],
                output_pattern="pg_%d.pdf",
            )
        )
        for _, chunk in results:
            assert "/PageLabels" not in chunk.Root


def test_make_chunk_pdf_directly_preserves_labels(book_with_chapters):
    """Direct unit-level check on _make_chunk_pdf itself, independent of
    the burst-splitting logic in _generate_burst_chunks."""
    chunk = _make_chunk_pdf(book_with_chapters, start_idx=5, end_idx=7)
    labels = get_all_page_label_dicts(chunk)
    assert [d["St"] for d in labels] == [2, 3, 4]
    assert all(d["S"] == pikepdf.Name("/D") for d in labels)


def test_invalid_argument_error_raised_on_misparse(two_page_pdf):
    with pytest.raises(InvalidArgumentError, match="Invalid size format"):
        burst_pdf([two_page_pdf], ["sizenot_a_number"])
