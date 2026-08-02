# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/pages/test_outlines.py
from unittest.mock import MagicMock, call, patch

import pikepdf
import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from pikepdf import Array, Dictionary, Name, OutlineItem, Pdf

from pdftl.pages.link_remapper import LinkRemapper

# --- Import classes we need to mock/use ---
from pdftl.pages.links import RebuildLinksPartialContext

# --- Import the module and functions to test ---
from pdftl.pages.outlines import (
    CachedOutlineItem,
    OutlineCopier,
    _build_outline_chunks,
    _cache_outline_tree,
    _get_source_action,
    rebuild_outlines,
)

# Mark all tests in this file as using hypothesis
pytestmark = pytest.mark.hypothesis

# --- Hypothesis Strategies & Helpers ---


class DummyPdf:
    """Lightweight dummy class to avoid Hypothesis entropy explosion caused by MagicMock."""

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return self.name


# Create a "pool" of lightweight PDF objects for hypothesis to sample from
MOCK_PDF_POOL = [
    DummyPdf("PDF_A"),
    DummyPdf("PDF_B"),
    DummyPdf("PDF_C"),
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
    ret = MagicMock(spec=LinkRemapper)
    ret.pdf = MagicMock()
    ret.source_pdf = MagicMock()
    return ret


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
    mock_pdf_b = MagicMock(spec=Pdf, id=67890)
    processed_page_info = [
        (mock_source_pdf, 0, 0),  # A, p1, i0
        (mock_source_pdf, 1, 0),  # A, p2, i0
        (mock_pdf_b, 0, 1),  # B, p1, i1
    ]

    chunks = _build_outline_chunks(processed_page_info)

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
    processed_page_info = [
        (mock_source_pdf, 0, 0),  # A, p1, i0
        (mock_source_pdf, 1, 0),  # A, p2, i0
        (mock_source_pdf, 0, 1),  # A, p1, i1
        (mock_source_pdf, 1, 1),  # A, p2, i1
    ]

    chunks = _build_outline_chunks(processed_page_info)

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
    processed_page_info = [
        (mock_source_pdf, 0, 0),  # A, p1, i0
        (mock_source_pdf, 2, 0),  # A, p3, i0 (non-contiguous)
    ]

    chunks = _build_outline_chunks(processed_page_info)

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
    2. Returns a new valid item.
    3. Extends the new_dests_list with the result from the remapper.
    """
    # 1. Arrange
    mock_action = Dictionary(S=Name.GoTo, D=Name.Dest1)

    # We now mock a CachedOutlineItem directly
    cached_item = CachedOutlineItem(
        title="Test Item", action=mock_action, is_closed=False, obj=Dictionary(), children=[]
    )

    # Set up the remapper's return value
    new_action = Dictionary(S=Name.GoTo, D=Name.NewDest)
    new_dest_tuple = [("NewDest_str", Array([1, 2, 3]))]  # Flat list returned

    # remap_goto_action returns *both* the action and the (name, dest) tuple
    mock_remapper.remap_goto_action.return_value = (new_action, new_dest_tuple)

    # 2. Act
    with patch("pikepdf.OutlineItem") as mock_OI_constructor:
        # Give the mock a children list so extend() doesn't fail
        mock_OI_constructor.return_value.children = []

        copier = OutlineCopier(mock_remapper)
        result = copier.copy_item(cached_item)

    # 3. Assert
    mock_remapper.remap_goto_action.assert_called_once_with(mock_action)

    # Check that the new item was created and returned
    mock_OI_constructor.assert_called_with(title="Test Item")
    # Verify that the destination attribute was attached properly
    assert result.destination == Name.NewDest

    # Check that the destinations list was extended
    assert copier.new_dests_list == [("NewDest_str", Array([1, 2, 3]))]


def test_copy_item_uses_action(mock_remapper):
    """
    Tests that OutlineCopier.copy_item uses the .action property from the cache.
    """
    # 1. Arrange
    mock_action = Dictionary(S=Name.GoTo, D=Name.Dest1)
    cached_item = CachedOutlineItem(
        title="Test Action Item",
        action=mock_action,
        is_closed=False,
        obj=Dictionary(),
        children=[],
    )

    mock_remapper.remap_goto_action.return_value = (None, None)  # Prune

    # 2. Act
    copier = OutlineCopier(mock_remapper)
    result = copier.copy_item(cached_item)

    # 3. Assert
    # Check that the remapper was called with the *existing* action
    mock_remapper.remap_goto_action.assert_called_once_with(mock_action)
    assert result is None  # Pruned
    assert len(copier.new_dests_list) == 0


def test_copy_item_recursive_pruning(mock_remapper):
    """
    Tests that an item is pruned if the remapper returns (None, None)
    AND it has no valid children.
    """
    from pikepdf import Dictionary, Name

    # Mock remap to return a valid action for everything
    mock_remapper.remap_goto_action.return_value = (MagicMock(D="remapped"), None)

    copier = OutlineCopier(mock_remapper)

    # Create a cache structure: Parent -> Child
    # Must use actual GoTo action to trigger the mock_remapper
    goto_action = Dictionary(S=Name.GoTo, D=Name.Dest1)

    child = CachedOutlineItem(
        title="Child", action=goto_action, is_closed=False, obj=Dictionary(), children=[]
    )
    parent = CachedOutlineItem(
        title="Parent", action=goto_action, is_closed=False, obj=Dictionary(), children=[child]
    )

    # This triggers the recursion
    with patch(
        "pikepdf.OutlineItem",
        side_effect=lambda title, **kwargs: MagicMock(
            title=title, children=[], obj=Dictionary(), **kwargs
        ),
    ):
        result = copier.copy_item(parent)

    assert result is not None
    assert len(result.children) == 1
    assert result.children[0].title == "Child"
    # Verify child was processed (remapper called twice: parent + child)
    assert mock_remapper.remap_goto_action.call_count == 2


@patch("pdftl.pages.outlines._cache_outline_tree")
@patch("pdftl.pages.outlines._build_outline_chunks")
@patch("pdftl.pages.outlines.OutlineCopier.copy_item")
@patch("pikepdf.models.outlines.Outline")
def test_rebuild_outlines_processes_chunks(
    mock_outline_class,
    mock_copy_item,
    mock_build_chunks,
    mock_cache_tree,
    mock_context,
    mock_source_pdf,
    mock_remapper,
):
    mock_pdf = MagicMock(spec=Pdf)

    mock_source_pdf.Root.Outlines = True

    # Mock the Outline instance returned when Outline(source_pdf) is called
    mock_outline_instance = MagicMock()
    mock_outline_instance.root = ["item_A", "item_B"]
    mock_outline_class.return_value = mock_outline_instance

    # Mock the caching step to return two dummy CachedOutlineItems
    mock_cache_tree.return_value = ["cached_A", "cached_B"]

    chunks = [
        MagicMock(pdf=mock_source_pdf, instance_num=0, output_start_page=1),
        MagicMock(pdf=mock_source_pdf, instance_num=1, output_start_page=3),
    ]
    mock_build_chunks.return_value = chunks

    mock_new_outline = MagicMock()
    mock_pdf.open_outline.return_value.__enter__.return_value = mock_new_outline

    result_dests = rebuild_outlines(mock_pdf, [], mock_context, mock_remapper)

    mock_build_chunks.assert_called_once_with(mock_context.processed_page_info)
    assert mock_remapper.set_call_context.call_count == 2
    mock_remapper.set_call_context.assert_has_calls(
        [
            call(mock_pdf, mock_source_pdf, 0),
            call(mock_pdf, mock_source_pdf, 1),
        ]
    )

    # copy_item called for each cached item in each chunk (2 chunks * 2 items)
    assert mock_copy_item.call_count == 4
    assert result_dests == []


# --- Hypothesis Tests ---


@given(processed_page_info=st_page_info_list)
def test_build_chunks_smoke_test(processed_page_info):
    """
    Tests that the chunker always produces a valid, non-empty
    list of chunks for any non-empty input and doesn't crash.
    """
    chunks = _build_outline_chunks(processed_page_info)
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
    for i in range(len(processed_page_info) - 1):
        page_num_current = i + 1  # 1-based page number
        page_num_next = i + 2

        chunk_current = page_map[page_num_current]
        chunk_next = page_map[page_num_next]

        if chunk_current == chunk_next:
            p_curr = processed_page_info[i]
            p_next = processed_page_info[i + 1]

            assert p_curr[0] is p_next[0], "Must be same PDF"
            assert p_curr[2] == p_next[2], "Must be same instance"
            assert p_curr[1] + 1 == p_next[1], "Must be contiguous source pages"


def test_build_outline_chunks_malformed_data(caplog):
    malformed_info = [("pdf_obj", 0)]  # Missing inst_num

    result = _build_outline_chunks(malformed_info)

    assert result == []
    assert "Could not build outline chunks" in caplog.text


def test_get_source_action_non_goto_action():
    mock_item = MagicMock()
    mock_item.destination = None
    # Simulate a generic action instead of GoTo
    mock_item.action.S = Name.URI
    mock_item.obj = MagicMock()
    mock_item.is_closed = MagicMock()

    action = _get_source_action(mock_item)
    # The new behavior preserves non-GoTo actions!
    assert action is mock_item.action


def test_rebuild_outlines_no_chunks():
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_remapper = MagicMock()
    mock_context = MagicMock()
    mock_context.processed_page_info = []

    result = rebuild_outlines(mock_pdf, [], mock_context, mock_remapper)

    assert result == []


def test_cache_outline_tree_closed_when_count_missing():
    """
    Tests that an item is marked closed during the caching phase if /Count is
    absent from the source obj (matches PDF spec default behaviour).
    """
    mock_item = MagicMock(spec=OutlineItem)
    mock_item.title = "Test Item"
    mock_item.action = None
    mock_item.destination = None
    mock_item.children = []

    # Simulate a dictionary missing /Count
    mock_dict = MagicMock()
    mock_dict.__contains__.side_effect = lambda key: False
    mock_item.obj = mock_dict

    mock_item.is_closed = False  # Would normally be open

    cached_list = _cache_outline_tree([mock_item])

    assert len(cached_list) == 1
    assert cached_list[0].is_closed is True


def test_copy_item_preserves_formatting_and_state_integration():
    """
    Integration test: verifies that bold/italic flags, colour, and open/closed
    state survive a round-trip through rebuild_outlines.
    """
    import pikepdf
    from pikepdf import Array, Name, Pdf

    from pdftl.pages.add_pages import add_pages
    from pdftl.utils.page_specs.spec_types import PageTransform
    from pdftl.utils.pikepdf_compatibility_utils import set_outline_item_style_compat

    # --- Build a source PDF with formatted bookmarks ---
    src = Pdf.new()
    page1 = pikepdf.Page(
        pikepdf.Dictionary(
            Type=Name.Page,
            MediaBox=Array([0, 0, 612, 792]),
        )
    )
    page2 = pikepdf.Page(
        pikepdf.Dictionary(
            Type=Name.Page,
            MediaBox=Array([0, 0, 612, 792]),
        )
    )
    src.pages.append(page1)
    src.pages.append(page2)

    # Build outline manually so we can set /F and /C
    with src.open_outline() as outline:
        # Pass the actual page object for a valid destination
        parent = pikepdf.OutlineItem("Parent", destination=0)
        outline.root.append(parent)

    with src.open_outline() as outline:
        parent = outline.root[0]
        # UPDATE the existing item via the version-compatible helper: raw
        # obj[Name.F]/obj[Name.C] mutation is silently dropped on pikepdf
        # >= 10.11.0 once this item is reassigned into outline.root below.
        set_outline_item_style_compat(parent, color=(1.0, 0.0, 0.0), bold=True, italic=True)
        parent.is_closed = True

        # Pass the actual page object for the child
        child = pikepdf.OutlineItem("Child", destination=1)
        parent.children.append(child)
        outline.root[0] = parent

    with src.open_outline() as outline:
        # UPDATE the existing obj
        parent = outline.root[0]
        child = parent.children[0]
        set_outline_item_style_compat(child, color=(0.0, 0.0, 1.0), italic=True)
        child.is_closed = False

    # --- Run through add_pages ---
    out = Pdf.new()
    page_transforms = [
        PageTransform(pdf=src, index=0, rotation=(0, False), scale=1.0),
        PageTransform(pdf=src, index=1, rotation=(0, False), scale=1.0),
    ]
    add_pages(out, [src], page_transforms)

    # --- Inspect the output outline ---
    with out.open_outline() as out_outline:
        assert len(out_outline.root) == 1
        out_parent = out_outline.root[0]
        assert out_parent.title == "Parent"
        assert out_parent.is_closed is True
        # breakpoint()
        assert int(out_parent.obj[Name.F]) == 3
        assert list(out_parent.obj.C) == pytest.approx([1.0, 0.0, 0.0])

        assert len(out_parent.children) == 1
        out_child = out_parent.children[0]
        assert out_child.title == "Child"
        assert out_child.is_closed is False
        assert int(out_child.obj.F) == 1
        assert list(out_child.obj.C) == pytest.approx([0.0, 0.0, 1.0])


def test_copy_item_preserves_open_closed_state(mock_remapper):
    """
    Tests that the is_closed state is faithfully copied from the cached item.
    """
    for source_is_closed in (True, False):
        cached_item = CachedOutlineItem(
            title="Test Item",
            action=Dictionary(S=Name.GoTo, D=Name.Dest1),
            is_closed=source_is_closed,
            obj=Dictionary(),
            children=[],
        )

        mock_remapper.remap_goto_action.return_value = (
            Dictionary(S=Name.GoTo, D=Name.NewDest),
            None,
        )

        copier = OutlineCopier(mock_remapper)

        with patch("pikepdf.OutlineItem") as mock_OI:
            mock_OI.return_value.children = []
            result = copier.copy_item(cached_item)

        assert result is not None
        assert result.is_closed == source_is_closed


def test_copy_item_copies_obj_for_formatting(mock_remapper):
    """
    Tests that copy_item manually extracts /F (bold/italic) and /C (colour)
    formatting fields and stashes them to bypass pikepdf limitations.
    """
    mock_obj = Dictionary()
    mock_obj[Name.C] = Array([1.0, 0.5, 0.0])
    mock_obj[Name.F] = 3  # Both italic (1) and bold (2)

    cached_item = CachedOutlineItem(
        title="Test Item",
        action=Dictionary(S=Name.GoTo, D=Name.Dest1),
        is_closed=False,
        obj=mock_obj,
        children=[],
    )

    mock_remapper.remap_goto_action.return_value = (Dictionary(S=Name.GoTo, D=Name.NewDest), None)

    copier = OutlineCopier(mock_remapper)

    with patch("pikepdf.OutlineItem") as mock_OI:
        new_item_mock = MagicMock()
        new_item_mock.children = []
        mock_OI.return_value = new_item_mock

        result = copier.copy_item(cached_item)

    assert result is not None
    # Verify the minimal clean constructor call required by pikepdf
    mock_OI.assert_called_once_with(title="Test Item")
    assert result.destination == Name.NewDest
    # Check that properties are stashed accurately for post-processing outside the context
    assert result._cached_color == [1.0, 0.5, 0.0]
    assert result._cached_flags == 3


from unittest.mock import Mock

from pdftl.pages.outlines import (
    _process_chunk,
    ChunkData,
)


def test_remap_item_action_with_no_action():
    """Hits Line 89: action is None/falsy."""
    remapper = Mock()
    copier = OutlineCopier(remapper)

    # Directly call the internal method to guarantee target execution
    dest, is_valid = copier._remap_item_action(None)

    assert dest is None
    assert is_valid is False


def test_apply_cached_meta_none_obj():
    """Hits Line 104: cached_item has no obj or obj is None."""
    remapper = Mock()
    copier = OutlineCopier(remapper)
    new_item = Mock()

    item_with_none = CachedOutlineItem(
        title="Test", action=Mock(), is_closed=False, obj=None, children=[]
    )
    copier._apply_cached_meta(item_with_none, new_item)  # Should early return cleanly


def test_get_source_action_fallback_to_goto_action():
    """Hits Line 152: item has no destination but holds a valid /GoTo action."""
    source_item = Mock()
    source_item.destination = None

    # Mock an action where .S evaluates to Name.GoTo
    mock_action = Mock()
    mock_action.S = Name.GoTo
    source_item.action = mock_action

    action = _get_source_action(source_item)
    assert action == mock_action


def test_process_chunk_empty_cached_root_items():
    """Hits Line 309: chunk setup yields an empty list of cached root elements."""
    mock_chunk = ChunkData(pdf=Mock(), source_page_map={}, output_start_page=1, instance_num=0)
    mock_remapper = Mock()

    # Pass empty array as cached_root_items
    dests, items = _process_chunk(mock_chunk, mock_remapper, [])

    assert dests == []
    assert items == []


def test_remap_item_action_non_goto():
    """Ensures non-GoTo actions are safely passed through the remapper."""
    remapper = Mock()
    copier = OutlineCopier(remapper)

    non_goto_action = Dictionary(S=Name.Launch, F=pikepdf.String("file.txt"))
    new_action, is_valid = copier._remap_item_action(non_goto_action)

    assert new_action is non_goto_action
    assert is_valid is True
    remapper.remap_goto_action.assert_not_called()
