from unittest.mock import MagicMock, call, patch

import pytest
from pikepdf import Dictionary, Name, Pdf

# --- Import module and functions to test ---
from pdftl.pages.add_pages import _apply_rotation, add_pages, process_source_pages
from pdftl.pages.link_remapper import LinkRemapper

# --- Import dependencies needed for testing ---
# This is returned by process_source_pages, so we need it.
from pdftl.pages.links import RebuildLinksPartialContext

# --- Fixtures ---


@pytest.fixture
def mock_new_pdf():
    """Create a new, empty pikepdf.Pdf object for testing."""
    pdf = Pdf.new()
    yield pdf
    pdf.close()


@pytest.fixture
def mock_source_pdf():
    """Create a mock source PDF with two pages."""
    pdf = Pdf.new()
    pdf.add_blank_page()
    pdf.add_blank_page()
    # Add a rotation to the second page to test relative rotation
    pdf.pages[1].Rotate = 180
    yield pdf
    pdf.close()


@pytest.fixture
def mock_source_pdf_b():
    """Create a second mock source PDF with one page."""
    pdf = Pdf.new()
    pdf.add_blank_page()
    yield pdf
    pdf.close()


# --- Test Cases ---

## _apply_rotation ##


@pytest.mark.parametrize(
    "source_rot, rotation_spec, expected_rot",
    [
        (180, (90, True), 90),  # Absolute rotation ignores source
        (180, (90, False), 270),  # Relative rotation adds to source
        (None, (90, False), 90),  # Relative rotation with no source default
        (180, (0, True), 0),  # Absolute zero
    ],
)
def test_apply_rotation(mock_new_pdf, source_rot, rotation_spec, expected_rot):
    """Tests that _apply_rotation correctly sets the /Rotate key."""
    # Source page is only read from, can be a simple Dictionary
    source_page = Dictionary()
    if source_rot is not None:
        source_page.Rotate = source_rot

    # The destination page must be a real page object
    mock_new_pdf.add_blank_page()
    page = mock_new_pdf.pages[0]

    _apply_rotation(page, source_page, rotation_spec)

    assert page.Rotate == expected_rot


def test_apply_rotation_no_op(mock_new_pdf):
    """Tests that no rotation is applied if angle is 0 and not absolute."""
    source_page = Dictionary(Rotate=180)
    mock_new_pdf.add_blank_page()
    page = mock_new_pdf.pages[0]

    _apply_rotation(page, source_page, (0, False))

    # Assert the /Rotate key was not added to the page
    assert Name.Rotate not in page


## process_source_pages ##


def _make_mock_transform(pdf, index, rotation, scale):
    """Helper to create a mock PageTransform data object."""
    mock = MagicMock()
    mock.pdf = pdf
    mock.index = index
    mock.rotation = rotation
    mock.scale = scale
    return mock


@patch("pdftl.pages.add_pages.apply_scaling")
@patch("pdftl.pages.add_pages._apply_rotation")
def test_process_source_pages_full(
    mock_apply_rotation,
    mock_apply_scaling,
    mock_new_pdf,
    mock_source_pdf,
    mock_source_pdf_b,
):
    """
    Tests process_source_pages with multiple PDFs, duplicate pages,
    and checks transformations and context building.
    """
    # 1. Arrange
    # We will add 3 pages in this order:
    # 1. source_pdf, page 1 (instance 0)
    # 2. source_pdf_b, page 0 (instance 0)
    # 3. source_pdf, page 1 (instance 1)

    tf1_rot, tf1_scale = (90, False), 1.0
    tf2_rot, tf2_scale = (0, True), 0.5
    tf3_rot, tf3_scale = (0, False), 1.5

    # Create mock PageTransform objects
    page_transforms = [
        _make_mock_transform(mock_source_pdf, 1, tf1_rot, tf1_scale),
        _make_mock_transform(mock_source_pdf_b, 0, tf2_rot, tf2_scale),
        _make_mock_transform(mock_source_pdf, 1, tf3_rot, tf3_scale),
    ]

    # Get references to the source pages
    source_p1 = mock_source_pdf.pages[1]
    source_b_p0 = mock_source_pdf_b.pages[0]

    # 2. Act
    ctx = process_source_pages(mock_new_pdf, page_transforms)

    # 3. Assert

    # --- A. Check final PDF state ---
    # Two pristine copies (source_pdf p1, source_pdf_b p0) were added
    # and removed. The final PDF should have exactly 3 pages.
    assert len(mock_new_pdf.pages) == 3
    page_0, page_1, page_2 = mock_new_pdf.pages

    # --- B. Check transformations ---
    mock_apply_rotation.assert_has_calls(
        [
            call(page_0, source_p1, tf1_rot),
            call(page_1, source_b_p0, tf2_rot),
            call(page_2, source_p1, tf3_rot),
        ]
    )
    mock_apply_scaling.assert_has_calls(
        [
            call(page_0, tf1_scale),
            call(page_1, tf2_scale),
            call(page_2, tf3_scale),
        ]
    )

    # --- C. Check returned context ---
    assert isinstance(ctx, RebuildLinksPartialContext)
    assert ctx.unique_source_pdfs == {mock_source_pdf, mock_source_pdf_b}

    # Check processed_page_info (tracks (pdf, index, instance_num))
    expected_page_info = [
        (mock_source_pdf, 1, 0),
        (mock_source_pdf_b, 0, 0),
        (mock_source_pdf, 1, 1),
    ]
    assert ctx.processed_page_info == expected_page_info

    # Check page_map (maps (id(pdf), index, instance_num) -> new page)
    expected_page_map = {
        (id(mock_source_pdf), 1, 0): page_0,
        (id(mock_source_pdf_b), 0, 0): page_1,
        (id(mock_source_pdf), 1, 1): page_2,
    }
    assert ctx.page_map == expected_page_map

    # Check page_transforms (maps new page objgen -> transforms)
    expected_page_transforms = {
        page_0.obj.objgen: (tf1_rot, tf1_scale),
        page_1.obj.objgen: (tf2_rot, tf2_scale),
        page_2.obj.objgen: (tf3_rot, tf3_scale),
    }
    assert ctx.page_transforms == expected_page_transforms


def test_process_source_pages_empty(mock_new_pdf):
    """Tests that processing an empty list does nothing."""
    ctx = process_source_pages(mock_new_pdf, [])

    assert len(mock_new_pdf.pages) == 0
    assert isinstance(ctx, RebuildLinksPartialContext)
    assert ctx.page_map == {}
    assert ctx.processed_page_info == []
    assert ctx.unique_source_pdfs == set()


## add_pages ##


@patch("pdftl.pages.add_pages.write_named_dests")
@patch("pdftl.pages.add_pages.rebuild_outlines")
@patch("pdftl.pages.add_pages.rebuild_links")
@patch("pdftl.pages.add_pages.create_link_remapper")
@patch("pdftl.pages.add_pages.process_source_pages")
def test_add_pages_orchestration(
    mock_process_source_pages,
    mock_create_link_remapper,
    mock_rebuild_links,
    mock_rebuild_outlines,
    mock_write_named_dests,
    mock_new_pdf,  # Fixture
):
    """Tests that add_pages correctly orchestrates its helper functions."""
    # 1. Arrange
    mock_context = MagicMock(spec=RebuildLinksPartialContext)
    mock_context.page_map = {"page_map_key": "page_map_val"}
    mock_context.page_transforms = {"transforms_key": "transforms_val"}
    mock_context.processed_page_info = ["page_info_1"]
    mock_context.unique_source_pdfs = {"pdf_a", "pdf_b"}
    mock_process_source_pages.return_value = mock_context

    # Mock the remapper that the factory will return
    mock_remapper = MagicMock(spec=LinkRemapper)
    mock_create_link_remapper.return_value = mock_remapper

    # Mock the destinations returned by the helpers
    mock_rebuild_links.return_value = ["link_dest_1"]
    mock_rebuild_outlines.return_value = ["outline_dest_1"]

    # Mock opened_pdfs list and source_pages_to_process
    pdf_a = MagicMock(spec=Pdf)
    pdf_b = MagicMock(spec=Pdf)
    opened_pdfs = [pdf_a, pdf_b]
    source_pages_to_process = [MagicMock(), MagicMock()]

    # 2. Act
    add_pages(mock_new_pdf, opened_pdfs, source_pages_to_process)

    # 3. Assert

    # Check PASS 1
    mock_process_source_pages.assert_called_once_with(
        mock_new_pdf, source_pages_to_process
    )

    # Check that the remapper factory was called correctly
    expected_pdf_map = {id(pdf_a): 0, id(pdf_b): 1}
    mock_create_link_remapper.assert_called_once_with(
        page_map=mock_context.page_map,
        page_transforms=mock_context.page_transforms,
        processed_page_info=mock_context.processed_page_info,
        unique_source_pdfs=mock_context.unique_source_pdfs,
        pdf_to_input_index=expected_pdf_map,
    )

    # Check PASS 2a (rebuild_links)
    # This function takes 3 arguments
    mock_rebuild_links.assert_called_once_with(
        mock_new_pdf,
        mock_context.processed_page_info,
        mock_remapper,
    )

    # Check PASS 2b (rebuild_outlines)
    # This function takes 4 arguments
    mock_rebuild_outlines.assert_called_once_with(
        mock_new_pdf, source_pages_to_process, mock_context, mock_remapper
    )

    # Check PASS 2c (write_named_dests)
    mock_write_named_dests.assert_called_once_with(
        mock_new_pdf, ["link_dest_1", "outline_dest_1"]  # Check dests are combined
    )
