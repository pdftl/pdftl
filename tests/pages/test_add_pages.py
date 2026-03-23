from unittest.mock import MagicMock, call, patch

import pikepdf
import pytest
from pikepdf import Dictionary, Name, Pdf

import pdftl.pages.add_pages as add_pages_module

# --- Import module and functions to test ---
from pdftl.pages.add_pages import (
    _apply_rotation,
    add_pages,
    process_source_pages,
)
from pdftl.pages.link_remapper import LinkRemapper

# --- Import dependencies needed for testing ---
# This is returned by process_source_pages, so we need it.
from pdftl.pages.links import RebuildLinksPartialContext
from pdftl.utils.page_specs import PageTransform

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


def test_process_source_pages_full():
    """
    Tests process_source_pages with multiple PDFs and duplicate pages.

    This test verifies:
    1. Correct page assembly order.
    2. Proper handling of repeated pages (independent rotation vs shared resources).
    3. Transformation functions are called with the correct arguments.
    4. Context data structures are populated correctly.

    It uses real pikepdf objects for the PDFs to ensure that operations like
    `add_blank_page` and `copy_foreign` function as intended within the library's
    internal logic.
    """
    # 1. Arrange
    # Create real in-memory PDFs to act as Source A and Source B
    # Source A: 2 blank pages
    source_pdf = pikepdf.Pdf.new()
    source_pdf.add_blank_page()  # Page 0
    source_pdf.add_blank_page()  # Page 1

    # Source B: 1 blank page
    source_pdf_b = pikepdf.Pdf.new()
    source_pdf_b.add_blank_page()  # Page 0

    # Create the Destination PDF
    new_pdf = pikepdf.Pdf.new()

    # Define transformation specs
    # Spec 1: Source A, Page 1 (First encounter) -> Rotate 90
    tf1_rot, tf1_scale = (90, False), 1.0

    # Spec 2: Source B, Page 0 (First encounter) -> Rotate 0, Scale 0.5
    tf2_rot, tf2_scale = (0, True), 0.5

    # Spec 3: Source A, Page 1 (Repeat encounter) -> Rotate 180
    # This checks if the repeat encounter logic (add_blank_page + copy_foreign)
    # works correctly, allowing a different rotation on the same source content.
    tf3_rot, tf3_scale = (180, False), 1.5

    # Create PageTransform objects
    page_transforms = [
        PageTransform(source_pdf, 1, tf1_rot, tf1_scale),
        PageTransform(source_pdf_b, 0, tf2_rot, tf2_scale),
        PageTransform(source_pdf, 1, tf3_rot, tf3_scale),
    ]

    # Mock the transformation functions. We want to verify they are called,
    # but we don't need them to actually modify the PDF content for this test.
    with (
        patch("pdftl.pages.add_pages.apply_scaling") as mock_scale,
        patch("pdftl.pages.add_pages._apply_rotation") as mock_rot,
    ):
        # 2. Act
        ctx, _ = process_source_pages(new_pdf, page_transforms)

        # 3. Assert

        # --- A. Check final PDF state ---
        # The final PDF should have exactly 3 pages.
        assert len(new_pdf.pages) == 3
        page_0 = new_pdf.pages[0]
        page_1 = new_pdf.pages[1]
        page_2 = new_pdf.pages[2]

        # --- B. Check transformations ---
        # Ensure _apply_rotation was called 3 times with the correct arguments.
        # Note: The second argument to _apply_rotation is the SOURCE page.
        mock_rot.assert_has_calls(
            [
                call(page_0, source_pdf.pages[1], tf1_rot),
                call(page_1, source_pdf_b.pages[0], tf2_rot),
                call(page_2, source_pdf.pages[1], tf3_rot),
            ]
        )

        # Ensure apply_scaling was called 3 times
        mock_scale.assert_has_calls(
            [
                # optimization: don't call if scale is 1.0, so no tf1_scale here
                call(page_1, tf2_scale, scale_annotations=False),
                call(page_2, tf3_scale, scale_annotations=False),
            ]
        )

        # --- C. Check returned context ---
        # Verify unique sources are tracked
        assert ctx.unique_source_pdfs == {source_pdf, source_pdf_b}

        # Verify processed_page_info matches the input sequence
        # Format: (source_pdf_obj, source_page_index, instance_counter)
        expected_page_info = [
            (source_pdf, 1, 0),
            (source_pdf_b, 0, 0),
            (source_pdf, 1, 1),  # Second instance of source_pdf page 1
        ]
        assert ctx.processed_page_info == expected_page_info

        # Verify page_map
        # Maps (id(source_pdf), source_index, instance_counter) -> destination_page_obj
        expected_page_map = {
            (id(source_pdf), 1, 0): page_0,
            (id(source_pdf_b), 0, 0): page_1,
            (id(source_pdf), 1, 1): page_2,
        }
        assert ctx.page_map == expected_page_map

        # Verify page_transforms
        # Maps destination_page.obj.objgen -> (rotation, scale)
        # This is used in Pass 2 to adjust link coordinates.
        expected_page_transforms = {
            page_0.obj.objgen: (tf1_rot, tf1_scale),
            page_1.obj.objgen: (tf2_rot, tf2_scale),
            page_2.obj.objgen: (tf3_rot, tf3_scale),
        }
        assert ctx.page_transforms == expected_page_transforms


def test_process_source_pages_empty(mock_new_pdf):
    """Tests that processing an empty list does nothing."""
    ctx, queue = process_source_pages(mock_new_pdf, [])

    assert len(mock_new_pdf.pages) == 0
    assert isinstance(ctx, RebuildLinksPartialContext)
    assert ctx.page_map == {}
    assert ctx.processed_page_info == []
    assert ctx.unique_source_pdfs == set()
    assert queue == []


## add_pages ##


@patch("pikepdf.Pdf.get_object")
@patch("pdftl.utils.scale.scale_annotations_in_page")
@patch("pdftl.pages.add_pages.handle_page_widgets")
@patch("pdftl.pages.add_pages.rebuild_acroform_index")
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
    mock_rebuild_acroform,
    mock_handle_widgets,
    mock_scale_annotations_in_page,
    mock_pdf_get_object,
    mock_new_pdf,  # Fixture
):
    """Tests that add_pages correctly orchestrates its helper functions."""
    # 1. Arrange
    mock_context = MagicMock(spec=RebuildLinksPartialContext)
    mock_context.page_map = {"page_map_key": "page_map_val"}
    mock_context.page_transforms = {"transforms_key": ("transforms_val0", "transforms_val1")}
    mock_context.processed_page_info = ["page_info_1"]
    mock_context.unique_source_pdfs = {"pdf_a", "pdf_b"}
    # UPDATED: Mock return value must be a tuple (ctx, queue)
    mock_queue = [("item1"), ("item2")]
    mock_process_source_pages.return_value = (mock_context, mock_queue)

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
    mock_process_source_pages.assert_called_once_with(mock_new_pdf, source_pages_to_process)

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

    # PASS 3
    assert mock_handle_widgets.call_count == len(mock_queue)
    mock_rebuild_acroform.assert_called_once_with(mock_new_pdf)


def test_add_page_rotated_dimensions(tmp_path):
    """
    Covers line 232: Swapping width/height for rotated pages.
    """
    # 1. Create a source PDF with a rotated page
    pdf = pikepdf.new()
    # 100x200 Portrait
    page = pdf.add_blank_page(page_size=(100, 200))
    page.Rotate = 90  # Visually Landscape (200x100)


# Create a mock PageTransform object since we only need attribute access
class MockPageTransform:
    def __init__(self, pdf, index, rotation=(0, False), scale=1.0):
        self.pdf = pdf
        self.index = index
        self.rotation = rotation
        self.scale = scale


def test_stash_page_source_data_rotation_swap():
    """
    Covers Lines 231-232:
    Verify that if the source page has a rotation of 90 (or 270),
    the width and height are swapped in the metadata.
    """
    # 1. Setup Mocks
    mock_new_page = MagicMock()
    mock_source_page = MagicMock()
    mock_pdf = MagicMock()
    mock_pdf.filename = "test.pdf"

    # Setup MediaBox: [0, 0, 100, 200] -> Width 100, Height 200
    # We must ensure both .MediaBox (property) and .obj (raw dict) behave
    mbox_val = [0, 0, 100, 200]
    mock_source_page.MediaBox = mbox_val

    # FIX: Ensure .obj.get() returns None so the code falls back to the property
    mock_source_page.obj.get.return_value = None

    # Setup Rotation: 90
    # Handle .get() calls for Rotation
    def get_side_effect(key, default=None):
        # Handle both string "/Rotate" and pikepdf.Name objects
        if str(key) == "/Rotate":
            return 90
        return default

    mock_source_page.get.side_effect = get_side_effect
    # Also handle .obj.get() calls if the code checks there for rotation
    mock_source_page.obj.get.side_effect = lambda k, d=None: 90 if str(k) == "/Rotate" else None

    page_data = MockPageTransform(pdf=mock_pdf, index=0)

    # 2. Execute
    add_pages_module._stash_page_source_data(mock_new_page, mock_source_page, page_data, 0)

    # 3. Verification
    assert mock_new_page.__setitem__.called
    call_args = mock_new_page.__setitem__.call_args
    info_dict = call_args[0][1]

    # Original Width: 100, Height: 200. Rotation 90 -> Swap.
    # Width should now be 200.
    assert info_dict["/source_width"] == 200.0
    assert info_dict["/source_height"] == 100.0

    # Orientation should be 'landscape' because 200 >= 100
    assert info_dict["/source_orientation"] == "landscape"
