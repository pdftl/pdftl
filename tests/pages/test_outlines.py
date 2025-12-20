from unittest.mock import MagicMock, call, patch

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from pikepdf import Array, Dictionary, Name, OutlineItem, Pdf

from pdftl.pages.link_remapper import LinkRemapper

# --- Import classes we need to mock/use ---
from pdftl.pages.links import RebuildLinksPartialContext

# --- Import the module and functions to test ---
from pdftl.pages.outlines import _build_outline_chunks  # We test the new one
from pdftl.pages.outlines import OutlineCopier, rebuild_outlines

# Mark all tests in this file as using hypothesis
pytestmark = pytest.mark.hypothesis

# --- Hypothesis Strategies & Helpers ---

# Create a "pool" of mock PDF objects for hypothesis to sample from
MOCK_PDF_POOL = [
    MagicMock(spec=Pdf, name="PDF_A"),
    MagicMock(spec=Pdf, name="PDF_B"),
    MagicMock(spec=Pdf, name="PDF_C"),
]

# A strategy for a single processed page tuple: (pdf, src_idx, inst_num)
st_page_spec = st.tuples(
    st.sampled_from(MOCK_PDF_POOL),  # (pdf)
    st.integers(min_value=0, max_value=50),  # (src_idx)
    st.integers(min_value=0, max_value=3),  # (inst_num)
)

# A strategy for a full 'processed_page_info' list
st_page_info_list = st.lists(st_page_spec, min_size=1, max_size=100)


def build_page_to_chunk_map(chunks, total_pages):
    """
    Helper function to create a simple lookup map of
    {output_page_num: chunk_object}.
    """
    page_to_chunk_map = {}
    for i, chunk in enumerate(chunks):
        # Determine the end page for this chunk
        next_start = chunks[i + 1].output_start_page if (i + 1) < len(chunks) else total_pages + 1

        # Assign all pages in this range to this chunk
        for page_num in range(chunk.output_start_page, next_start):
            page_to_chunk_map[page_num] = chunk

    return page_to_chunk_map


# --- Fixtures ---


@pytest.fixture
def mock_source_pdf():
    """Returns a mock Pdf object for use as a source."""
    return MagicMock(spec=Pdf, id=12345)


@pytest.fixture
def mock_remapper():
    """Returns a mock LinkRemapper."""
    return MagicMock(spec=LinkRemapper)


@pytest.fixture
def mock_context(mock_source_pdf):
    """
    Returns a mock RebuildLinksPartialContext with processed_page_info
    set up for the "A, A" test case.
    """
    # Mimics "cat A.pdf(1-2) A.pdf(1-2)"
    processed_page_info = [
        (mock_source_pdf, 0, 0),  # A, page 1, instance 0
        (mock_source_pdf, 1, 0),  # A, page 2, instance 0
        (mock_source_pdf, 0, 1),  # A, page 1, instance 1
        (mock_source_pdf, 1, 1),  # A, page 2, instance 1
    ]

    ctx = RebuildLinksPartialContext(
        processed_page_info=processed_page_info, unique_source_pdfs={mock_source_pdf}
    )
    return ctx


# --- Test Cases ---


def test_build_outline_chunks_simple(mock_source_pdf):
    """
    Tests the new chunking logic for a simple "A, B" case.
    """
    # 1. Arrange
    mock_pdf_b = MagicMock(spec=Pdf, id=67890)
    # Mimics "cat A(1-2) B(1)"
    processed_page_info = [
        (mock_source_pdf, 0, 0),  # A, p1, i0
        (mock_source_pdf, 1, 0),  # A, p2, i0
        (mock_pdf_b, 0, 1),  # B, p1, i1
    ]

    # 2. Act
    chunks = _build_outline_chunks(processed_page_info)

    # 3. Assert
    assert len(chunks) == 2

    assert chunks[0].pdf == mock_source_pdf
    assert chunks[0].instance_num == 0
    assert chunks[0].output_start_page == 1
    assert chunks[0].source_page_map == {0: 0, 1: 1}  # {src_idx: chunk_idx}

    assert chunks[1].pdf == mock_pdf_b
    assert chunks[1].instance_num == 1
    assert chunks[1].output_start_page == 3
    assert chunks[1].source_page_map == {0: 0}


def test_build_outline_chunks_cat_A_A_case(mock_source_pdf):
    """
    Tests the "A, A" (multi-instance) case that was failing.
    """
    # 1. Arrange
    # Mimics "cat A(1-2) A(1-2)"
    processed_page_info = [
        (mock_source_pdf, 0, 0),  # A, p1, i0
        (mock_source_pdf, 1, 0),  # A, p2, i0
        (mock_source_pdf, 0, 1),  # A, p1, i1
        (mock_source_pdf, 1, 1),  # A, p2, i1
    ]

    # 2. Act
    chunks = _build_outline_chunks(processed_page_info)

    # 3. Assert
    assert len(chunks) == 2

    assert chunks[0].pdf == mock_source_pdf
    assert chunks[0].instance_num == 0
    assert chunks[0].output_start_page == 1
    assert chunks[0].source_page_map == {0: 0, 1: 1}

    assert chunks[1].pdf == mock_source_pdf
    assert chunks[1].instance_num == 1
    assert chunks[1].output_start_page == 3
    assert chunks[1].source_page_map == {0: 0, 1: 1}


def test_build_outline_chunks_non_contiguous(mock_source_pdf):
    """
    Tests that non-contiguous pages create a new chunk, even
    from the same instance.
    """
    # 1. Arrange
    # Mimics "cat A(1, 3)"
    processed_page_info = [
        (mock_source_pdf, 0, 0),  # A, p1, i0
        (mock_source_pdf, 2, 0),  # A, p3, i0 (non-contiguous)
    ]

    # 2. Act
    chunks = _build_outline_chunks(processed_page_info)

    # 3. Assert
    assert len(chunks) == 2

    assert chunks[0].pdf == mock_source_pdf
    assert chunks[0].instance_num == 0
    assert chunks[0].output_start_page == 1
    assert chunks[0].source_page_map == {0: 0}

    assert chunks[1].pdf == mock_source_pdf
    assert chunks[1].instance_num == 0
    assert chunks[1].output_start_page == 2
    assert chunks[1].source_page_map == {2: 0}


def test_build_outline_chunks_empty():
    """Tests that an empty input gives an empty output."""
    chunks = _build_outline_chunks([])
    assert chunks == []


def test_copy_item_remaps_and_collects_dests(mock_remapper):
    """
    Tests that OutlineCopier.copy_item:
    1. Calls the remapper with a GoTo action.
    2. Appends the new item to the parent list.
    3. Extends the new_dests_list with the result from the remapper.
    """
    # 1. Arrange
    # Mock an OutlineItem with a .destination
    mock_item = MagicMock(spec=OutlineItem)
    mock_item.title = "Test Item"
    mock_item.destination = Name.Dest1
    mock_item.action = None
    mock_item.children = []

    # Set up the remapper's return value
    new_action = Dictionary(S=Name.GoTo, D=Name.NewDest)
    new_dest_tuple = ("NewDest_str", Array([1, 2, 3]))  # (name, dest_array)

    # remap_goto_action returns *both* the action and the (name, dest) tuple
    mock_remapper.remap_goto_action.return_value = (new_action, new_dest_tuple)

    new_parent_list = []

    # 2. Act
    with patch("pikepdf.OutlineItem", MagicMock()) as mock_OI_constructor:
        copier = OutlineCopier(mock_remapper)
        copier.copy_item(
            mock_item,
            new_parent_list,
        )

    # 3. Assert
    # Check that the remapper was called with a constructed GoTo action
    expected_action = Dictionary(S=Name.GoTo, D=Name.Dest1)
    mock_remapper.remap_goto_action.assert_called_once_with(expected_action)

    # Check that the new item was created and added
    mock_OI_constructor.assert_called_with(title="Test Item", destination=Name.NewDest)
    assert len(new_parent_list) == 1

    # Check that the destinations list was extended
    # Note: .extend() means we add the tuple's *contents*
    assert copier.new_dests_list == ["NewDest_str", Array([1, 2, 3])]


def test_copy_item_uses_action(mock_remapper):
    """
    Tests that OutlineCopier.copy_item uses the .action if .destination is None.
    """
    # 1. Arrange
    mock_action = Dictionary(S=Name.GoTo, D=Name.Dest1)
    mock_item = MagicMock(spec=OutlineItem)
    mock_item.title = "Test Action Item"
    mock_item.destination = None
    mock_item.action = mock_action  # Use this
    mock_item.children = []

    mock_remapper.remap_goto_action.return_value = (None, None)  # Prune
    new_parent_list = []
    new_dests_list = []

    # 2. Act
    copier = OutlineCopier(mock_remapper)
    copier.copy_item(mock_item, new_parent_list)

    # 3. Assert
    # Check that the remapper was called with the *existing* action
    mock_remapper.remap_goto_action.assert_called_once_with(mock_action)
    assert len(new_parent_list) == 0  # Pruned
    assert len(copier.new_dests_list) == 0


def test_copy_item_recursive_pruning(mock_remapper):
    """
    Tests that an item is pruned if the remapper returns (None, None)
    AND it has no valid children.
    """
    # 1. Arrange
    mock_item = MagicMock(spec=OutlineItem)
    mock_item.destination = Name.Dest1
    mock_item.title = "Test Pruning Item"
    mock_item.action = None
    mock_item.children = []  # No children

    # Remapper returns (None, None), meaning "prune this link"
    mock_remapper.remap_goto_action.return_value = (None, None)

    new_parent_list = []

    # 2. Act
    # --- Configure the mock OutlineItem to have "falsy" children ---
    # A default MagicMock is "truthy", which breaks the pruning logic.
    mock_constructor = MagicMock(spec=OutlineItem)
    mock_new_item = MagicMock(spec=OutlineItem)
    mock_new_item.children = []  # This makes it "falsy"
    mock_constructor.return_value = mock_new_item

    copier = OutlineCopier(mock_remapper)
    with patch("pikepdf.OutlineItem", mock_constructor) as mock_OI_constructor:
        copier.copy_item(mock_item, new_parent_list)

    # 3. Assert
    # The new item is *created* (for its children to be processed)
    mock_constructor.assert_called_once_with(title=mock_item.title, destination=None)
    # But it is *not appended* to the parent list
    assert len(new_parent_list) == 0
    assert len(copier.new_dests_list) == 0


@patch("pdftl.pages.outlines._build_outline_chunks")
@patch("pdftl.pages.outlines.OutlineCopier.copy_item")
def test_rebuild_outlines_processes_chunks(  # 1b. Renamed test
    mock_copy_recursive,
    mock_build_chunks,
    # mock_LinkRemapper (REMOVED)
    # mock_build_caches (REMOVED)
    mock_context,  # Fixture
    mock_source_pdf,  # Fixture
):
    """
    Tests that rebuild_outlines correctly iterates chunks and calls
    the remapper for each one.
    """
    # 1. Arrange
    mock_pdf = MagicMock(spec=Pdf)

    # 2. CREATE the mock remapper to be injected
    mock_remapper_instance = MagicMock(spec=LinkRemapper)

    # Mock source outlines
    mock_source_pdf.Root.Outlines = True
    mock_source_pdf.open_outline.return_value.__enter__.return_value.root = [
        "item_A",
        "item_B",
    ]

    # Mock the chunking helper
    chunks = [
        MagicMock(pdf=mock_source_pdf, instance_num=0, output_start_page=1),  # Chunk 1
        MagicMock(pdf=mock_source_pdf, instance_num=1, output_start_page=3),  # Chunk 2
    ]
    mock_build_chunks.return_value = chunks

    # (mock_build_caches logic is GONE)

    # Mock the PDF's outline manager
    mock_new_outline = MagicMock()
    mock_pdf.open_outline.return_value.__enter__.return_value = mock_new_outline

    # (mock_remapper_instance is now created above)

    # 2. Act
    result_dests = rebuild_outlines(
        mock_pdf,
        [],  # source_pages_to_process
        mock_context,
        mock_remapper_instance,
    )

    # 3. Assert
    # Check that chunks were built (this is still correct)
    mock_build_chunks.assert_called_once_with(mock_context.processed_page_info)

    # 4. DELETE all the old assertions for cache/remapper creation

    # 5. KEEP the assertions for remapper *usage* (these are still correct)
    # Check that set_call_context was called TWICE, once for each chunk
    assert mock_remapper_instance.set_call_context.call_count == 2
    mock_remapper_instance.set_call_context.assert_has_calls(
        [
            call(mock_pdf, mock_source_pdf, 0),  # Chunk 1
            call(mock_pdf, mock_source_pdf, 1),  # Chunk 2
        ]
    )

    # Check that copy_item was called for all items in both chunks
    assert mock_copy_recursive.call_count == 4

    # Check that the (empty) list of destinations was returned
    assert result_dests == []


# --- Hypothesis Tests ---


@given(processed_page_info=st_page_info_list)
def test_build_chunks_smoke_test(processed_page_info):
    """
    Tests that the chunker always produces a valid, non-empty
    list of chunks for any non-empty input and doesn't crash.
    """
    # 1. Arrange
    assume(len(processed_page_info) > 0)

    # 2. Act
    chunks = _build_outline_chunks(processed_page_info)

    # 3. Assert
    assert chunks  # Not empty
    assert len(chunks) >= 1
    assert chunks[0].output_start_page == 1


@given(processed_page_info=st_page_info_list)
def test_build_chunks_invariant(processed_page_info):
    """
    Tests the core invariant of the chunking logic:

    If any two adjacent pages (A, B) are in the SAME chunk,
    it MUST be because they are from the same PDF, same instance,
    AND have contiguous source page numbers.
    """
    # 1. Arrange
    assume(len(processed_page_info) > 1)

    # 2. Act
    chunks = _build_outline_chunks(processed_page_info)
    page_map = build_page_to_chunk_map(chunks, len(processed_page_info))

    # 3. Assert
    # Check the invariant for every adjacent page pair
    for i in range(len(processed_page_info) - 1):
        page_num_current = i + 1  # 1-based page number
        page_num_next = i + 2

        chunk_current = page_map[page_num_current]
        chunk_next = page_map[page_num_next]

        # If they are in the same chunk...
        if chunk_current == chunk_next:

            # ...then the non-break condition MUST be true.
            p_curr = processed_page_info[i]
            p_next = processed_page_info[i + 1]

            assert p_curr[0] is p_next[0], "Must be same PDF"
            assert p_curr[2] == p_next[2], "Must be same instance"
            assert p_curr[1] + 1 == p_next[1], "Must be contiguous source pages"
