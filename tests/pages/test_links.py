import logging
from unittest.mock import MagicMock, call, patch

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, NameTree, Pdf, String

try:
    from pikepdf.exceptions import ForeignObjectError
except ImportError:
    from pikepdf import ForeignObjectError

# --- Import the module and functions to test ---
# --- Import the class/functions we need to mock OR test ---
from pdftl.pages.link_remapper import LinkRemapper, _build_link_caches
from pdftl.pages.links import (
    RebuildLinksPartialContext,
    _process_annotation,
    _rebuild_annotations_for_page,
    rebuild_links,
    write_named_dests,
)

# --- Fixtures ---


@pytest.fixture
def mock_source_pdf(mocker):
    """
    Creates a *REAL* source PDF with pages and a NameTree.
    This fixes the ValueError in test_build_link_caches.
    """
    pdf = Pdf.new()
    pdf.add_blank_page()
    pdf.add_blank_page()

    # Create a real, owned NameTree
    nt = NameTree.new(pdf)
    nt["Dest1"] = Dictionary(D=Array())
    pdf.Root.Names = Dictionary(Dests=nt.obj)

    yield pdf

    # Teardown
    pdf.close()


@pytest.fixture
def mock_remapper(mocker):
    """
    Creates a mock LinkRemapper instance.
    """
    # Create a mock that has the correct class spec
    remapper = MagicMock(spec=LinkRemapper)

    # Add the .pdf and .source_pdf attributes that the module code accesses
    remapper.pdf = MagicMock(spec=Pdf)
    remapper.source_pdf = MagicMock(spec=Pdf)

    # Set up a mock return for the copy_foreign method
    remapper.pdf.copy_foreign.return_value = "copied_annot"
    return remapper


@pytest.fixture
def mock_real_page(request):
    """
    Creates a real PDF with a real page to avoid mocking pikepdf.Page.
    This page is used as the *target* page in the new PDF.
    """
    pdf = Pdf.new()
    page = pdf.add_blank_page()

    # Allow parameterizing the page to have an /Annots array or not
    if request.param.get("has_annots_array", False):
        page.Annots = Array([])

    yield pdf, page

    pdf.close()


# --- Test Cases ---


def test_rebuild_links_partial_context_defaults():
    """Tests that the dataclass initializes with empty defaults."""
    ctx = RebuildLinksPartialContext()
    assert ctx.page_map == {}
    assert ctx.page_transforms == {}
    assert ctx.processed_page_info == []
    assert ctx.unique_source_pdfs == set()


def test_build_link_caches(mock_source_pdf):
    """Tests the cache building logic."""
    # 1. Arrange
    # Test include_instance=True
    source_pages = [(mock_source_pdf, 0, 0), (mock_source_pdf, 1, 1)]
    # Test include_pdf_id=False
    source_pdfs = {mock_source_pdf}

    # Get real objgens from the real pages
    p1_gen = mock_source_pdf.pages[0].obj.objgen
    p2_gen = mock_source_pdf.pages[1].obj.objgen

    # 2. Act
    # This now calls the correctly imported function
    caches = _build_link_caches(source_pages, source_pdfs)
    rev_maps, dest_caches, include_inst, include_pdf = caches

    # 3. Assert
    assert include_inst is True
    assert include_pdf is False

    expected_rev_map = {p1_gen: 0, p2_gen: 1}
    assert rev_maps[id(mock_source_pdf)] == expected_rev_map

    # Check that the NameTree was read
    assert "Dest1" in dest_caches[id(mock_source_pdf)]


def test_build_link_caches_no_dests(mock_source_pdf):
    """Tests cache building when /Names or /Dests is missing."""
    # Arrange
    del mock_source_pdf.Root.Names  # No /Names
    source_pages = [(mock_source_pdf, 0, 0)]
    source_pdfs = {mock_source_pdf}

    # Act
    # This now calls the correctly imported function
    _, dest_caches, _, _ = _build_link_caches(source_pages, source_pdfs)

    # Assert
    assert dest_caches[id(mock_source_pdf)] == {}


@patch("pdftl.pages.links.DEFAULT_ACTION_HANDLER")
@patch("pdftl.pages.links.ACTION_HANDLERS", new_callable=dict)
def test_process_annotation_dispatch_goto(
    mock_action_handlers, mock_default_handler, mock_remapper
):
    """Tests that _process_annotation correctly dispatches to the GoTo handler."""
    # 1. Arrange
    mock_goto_handler = MagicMock(return_value=("new_action_goto", "new_dest_goto"))
    mock_action_handlers[Name.GoTo] = mock_goto_handler

    action = Dictionary(S=Name.GoTo)
    mock_annot = Dictionary(Subtype=Name.Link, A=action)
    mock_remapper.pdf.copy_foreign.return_value = mock_annot

    # 2. Act
    new_annot, new_dest = _process_annotation(mock_annot, 0, mock_remapper)

    # 3. Assert
    mock_goto_handler.assert_called_once_with(mock_remapper, action)
    mock_default_handler.assert_not_called()
    assert new_annot.A == "new_action_goto"
    assert new_dest == "new_dest_goto"


@patch("pdftl.pages.links.DEFAULT_ACTION_HANDLER")
@patch("pdftl.pages.links.ACTION_HANDLERS", {})
def test_process_annotation_dispatch_default(mock_default_handler, mock_remapper):
    """Tests that _process_annotation dispatches to the default handler."""
    # 1. Arrange
    mock_default_handler.return_value = ("new_action_default", None)

    action = Dictionary(S=Name.URI)  # Not in our explicit handler map
    mock_annot = Dictionary(Subtype=Name.Link, A=action)
    mock_remapper.pdf.copy_foreign.return_value = mock_annot

    # 2. Act
    new_annot, new_dest = _process_annotation(mock_annot, 0, mock_remapper)

    # 3. Assert
    mock_default_handler.assert_called_once_with(mock_remapper, action)
    assert new_annot.A == "new_action_default"
    assert new_dest is None


def test_process_annotation_not_a_link(mock_remapper):
    """Tests that non-Link annotations are just copied and returned."""
    mock_annot = Dictionary(Subtype=Name.Square)  # Not a Link
    mock_remapper.pdf.copy_foreign.return_value = mock_annot

    new_annot, new_dest = _process_annotation(mock_annot, 0, mock_remapper)

    assert new_annot is mock_annot
    assert new_dest is None


def test_process_annotation_copy_fails(mock_remapper, caplog):
    """Tests that a corrupt annotation is skipped and a warning is logged."""
    caplog.set_level(logging.WARNING)

    mock_annot = Dictionary(Subtype=Name.Link, A=Dictionary(S=Name.GoTo))
    mock_remapper.pdf.copy_foreign.side_effect = ForeignObjectError("Test error")

    new_annot, new_dest = _process_annotation(mock_annot, 0, mock_remapper)

    assert new_annot is None
    assert new_dest is None
    assert "Skipping potentially corrupt annotation" in caplog.text
    assert "Test error" in caplog.text


def test_rebuild_annotations_for_page(mocker, mock_remapper):
    """Tests that _rebuild_annotations_for_page loops, processes, and re-adds annots."""
    # 1. Arrange
    mock_process = mocker.patch("pdftl.pages.links._process_annotation")

    mock_annot1 = Dictionary(A=None)
    mock_annot2 = Dictionary(A=None)

    mock_source_page = MagicMock(spec=pikepdf.Page)
    mock_source_page.Annots = [mock_annot1, mock_annot2]  # For the loop
    mock_source_page.__contains__ = MagicMock(side_effect=lambda key: key == Name.Annots)

    # --- Use a REAL PDF and REAL PAGE ---
    real_pdf = Pdf.new()
    real_pdf.add_blank_page()
    new_page = real_pdf.pages[0]
    # Add a dummy /Annots array so the 'if' block is entered
    new_page.Annots = Array([])
    assert Name.Annots in new_page  # This is now True
    # ---

    mock_remapper.source_pdf.pages = [mock_source_page]
    # Point the remapper to the real PDF so .make_indirect works
    mock_remapper.pdf = real_pdf

    mock_process.side_effect = [
        (Dictionary(A="new_action_1", P=None), ["new_dest_1"]),
        (Dictionary(A="new_action_2", P=None), None),
    ]

    # 2. Act
    new_dests = _rebuild_annotations_for_page(
        new_page, mock_source_page, 0, mock_remapper, pikepdf
    )

    # 3. Assert
    # Check that the old /Annots was deleted (by checking if it's empty)
    # The module correctly re-assigns it.
    assert Name.Annots in new_page
    assert len(new_page.Annots) == 2  # This will now pass

    # Check _process_annotation was called twice
    assert mock_process.call_count == 2

    # Check that new annots were added back
    assert new_page.Annots[0].A == "new_action_1"
    assert new_page.Annots[1].A == "new_action_2"

    # Check that the .P (parent) key was set
    assert new_page.Annots[0].P == new_page.obj
    assert new_page.Annots[1].P == new_page.obj

    assert new_dests == ["new_dest_1"]

    real_pdf.close()


def test_write_named_dests(mocker):
    """Tests that a new NameTree is built and attached to the PDF."""
    # 1. Arrange
    mock_nt_new = mocker.patch("pikepdf.NameTree.new")
    mock_nt = mock_nt_new.return_value
    mock_nt.obj = "The Name Tree Object"

    mock_pdf = MagicMock(spec=Pdf)
    mock_pdf.Root = Dictionary()

    dest_dict_1 = Dictionary(D=Array())
    dest_dict_2 = Dictionary(D=Array())
    new_named_dests = [String("Dest1"), dest_dict_1, String("Dest2"), dest_dict_2]

    # 2. Act
    write_named_dests(mock_pdf, new_named_dests)

    # 3. Assert
    mock_nt_new.assert_called_once_with(mock_pdf)

    assert mock_nt.__setitem__.call_count == 2
    mock_nt.__setitem__.assert_has_calls(
        [
            call("Dest1", dest_dict_1),
            call("Dest2", dest_dict_2),
        ]
    )

    assert Name.Names in mock_pdf.Root
    assert mock_pdf.Root.Names.Dests == "The Name Tree Object"


def test_write_named_dests_no_dests(mocker):
    """Tests that the function does nothing if no dests are provided."""
    mock_nametree_new = mocker.patch("pikepdf.NameTree.new")
    mock_pdf = MagicMock(spec=Pdf)

    write_named_dests(mock_pdf, [])

    mock_nametree_new.assert_not_called()


@patch("pdftl.pages.links._rebuild_annotations_for_page")
def test_rebuild_links_orchestration(
    mock_rebuild_annots,
    mock_source_pdf,  # From fixture
):
    """
    Tests the main rebuild_links function as an orchestrator,
    mocking its helper functions.
    """
    # 1. Arrange
    # Mock the helper function to return different dests for each page
    mock_source_pdf = MagicMock(name="mock_source_pdf_simple")
    mock_rebuild_annots.side_effect = [
        ["page_0_dest"],
        ["page_1_dest_A", "page_1_dest_B"],
    ]

    # Create a mock LinkRemapper instance
    mock_remapper_instance = MagicMock(spec=LinkRemapper)

    # Create a mock target PDF with two pages
    mock_pdf = MagicMock(spec=Pdf, pages=[MagicMock(), MagicMock()])

    src_p0 = MagicMock()
    src_p0.__contains__.return_value = True
    src_p1 = MagicMock()
    src_p1.__contains__.return_value = True
    mock_source_pdf.pages = [src_p0, src_p1]

    # Define the processed_page_info list that rebuild_links will iterate over
    processed_page_info = [
        (mock_source_pdf, 0, 0),  # (src_pdf, page_idx, instance_num) for page 0
        (mock_source_pdf, 1, 1),  # (src_pdf, page_idx, instance_num) for page 1
    ]

    # 2. Act
    # Call rebuild_links with the new signature
    all_dests_result = rebuild_links(
        mock_pdf,
        processed_page_info,
        mock_remapper_instance,  # Pass the mock remapper
    )

    # 3. Assert

    # 1. Assert set_call_context is called TWICE (once per page)
    assert mock_remapper_instance.set_call_context.call_count == 2
    mock_remapper_instance.set_call_context.assert_has_calls(
        [
            call(mock_pdf, mock_source_pdf, 0),
            call(mock_pdf, mock_source_pdf, 1),
        ]
    )

    # 2. Assert annotations are rebuilt for each page
    from unittest.mock import ANY

    assert mock_rebuild_annots.call_count == 2
    mock_rebuild_annots.assert_has_calls(
        [
            call(mock_pdf.pages[0], src_p0, 0, mock_remapper_instance, ANY),
            call(mock_pdf.pages[1], src_p1, 1, mock_remapper_instance, ANY),
        ]
    )

    # 3. Assert final result is correct
    assert all_dests_result == [
        "page_0_dest",
        "page_1_dest_A",
        "page_1_dest_B",
    ]


def test_process_annotation_uses_original_action(mock_remapper, mocker):
    action = Dictionary(S=Name.GoTo)
    original_annot = Dictionary(Subtype=Name.Link, A=action)
    mock_remapper.pdf.copy_foreign.return_value = Dictionary(
        Subtype=Name.Link, A=Dictionary(S=Name.GoTo)
    )
    handler = mocker.patch.dict(
        "pdftl.pages.links.ACTION_HANDLERS",
        {Name.GoTo: MagicMock(return_value=(None, None))},
    )
    _process_annotation(original_annot, 0, mock_remapper)
    handler[Name.GoTo].assert_called_once_with(mock_remapper, action)


# --- Hypothesis Tests ---


@patch("pdftl.pages.links._process_annotation")
@pytest.mark.parametrize("mock_real_page", [{"has_annots_array": False}], indirect=True)
def test_rebuild_annots_page_with_no_annots_key(mock_process, mock_remapper, mock_real_page):
    """
    Hypothesis: If the source page has no /Annots key at all,
    the function should do nothing and return an empty list.
    """
    # 1. Arrange
    # Mock a source page that returns False for `Name.Annots in page`
    _, real_page = mock_real_page
    mock_source_page = MagicMock(spec=pikepdf.Page)
    mock_source_page.__contains__ = MagicMock(return_value=False)

    mock_remapper.source_pdf.pages = [mock_source_page]

    # 2. Act
    new_dests = _rebuild_annotations_for_page(
        real_page, mock_source_page, 0, mock_remapper, pikepdf
    )

    # 3. Assert
    assert new_dests == []
    mock_process.assert_not_called()
    # The target page's /Annots should not have been created or modified
    assert Name.Annots not in real_page


@patch("pdftl.pages.links._process_annotation")
@pytest.mark.parametrize(
    "mock_real_page",
    [{"has_annots_array": False}],  # Target page starts with no /Annots
    indirect=True,
)
def test_rebuild_annots_page_with_empty_annots_list(mock_process, mock_remapper, mock_real_page):
    """
    Hypothesis: If the source page has an /Annots key with an
    empty list, the function should do nothing and return an empty list.
    """
    # 1. Arrange
    # Mock a source page that *has* /Annots, but it's empty
    _, real_page = mock_real_page

    mock_source_page = MagicMock(spec=pikepdf.Page)
    mock_source_page.__contains__ = MagicMock(return_value=True)
    mock_source_page.Annots = []  # Empty list

    mock_remapper.source_pdf.pages = [mock_source_page]

    # 2. Act
    new_dests = _rebuild_annotations_for_page(
        real_page, mock_source_page, 0, mock_remapper, pikepdf
    )

    # 3. Assert
    assert new_dests == []
    mock_process.assert_not_called()
    assert Name.Annots not in real_page


@patch("pdftl.pages.links._process_annotation")
@pytest.mark.parametrize(
    "mock_real_page",
    [{"has_annots_array": True}],  # Target page starts with /Annots = []
    indirect=True,
)
def test_rebuild_annots_pruning_logic(mock_process, mock_remapper, mock_real_page):
    """
    Hypothesis: If _process_annotation returns (None, None),
    (e.g., for a pruned link), it is NOT added to the new page's
    /Annots list and no destination is returned.
    """
    # 1. Arrange
    mock_source_page = MagicMock(spec=pikepdf.Page)
    mock_source_page.__contains__ = MagicMock(return_value=True)
    mock_source_page.Annots = [
        Dictionary(Subtype=Name.Link),  # Valid annot 1
        Dictionary(Subtype=Name.Link),  # Pruned annot
        Dictionary(Subtype=Name.Link),  # Valid annot 2
    ]

    real_pdf, real_page = mock_real_page
    mock_remapper.source_pdf.pages = [mock_source_page]

    mock_remapper.pdf = real_pdf

    # # we tried this earlier
    # annot_1 = real_pdf.make_indirect(Dictionary(A="new_1"))
    # annot_2 = real_pdf.make_indirect(Dictionary(A="new_2"))

    # now we try this.
    annot_1 = Dictionary(A="new_1")
    annot_2 = Dictionary(A="new_2")

    # Mock the return values from _process_annotation
    mock_process.side_effect = [
        (annot_1, ["new_dest_1"]),  # Valid
        (None, None),  # Pruned
        (annot_2, None),  # Valid (no dest)
    ]

    # 2. Act
    new_dests = _rebuild_annotations_for_page(
        real_page, mock_source_page, 0, mock_remapper, pikepdf
    )

    # 3. Assert
    assert new_dests == ["new_dest_1"]
    assert mock_process.call_count == 3

    # Assert that only the non-None annotations were added
    assert len(real_page.Annots) == 2
    assert real_page.Annots[0].A == "new_1"
    assert real_page.Annots[1].A == "new_2"

    # Assert the parent key was set correctly
    assert real_page.Annots[0].P == real_page.obj
    assert real_page.Annots[1].P == real_page.obj


@patch("pdftl.pages.links._rebuild_annotations_for_page")
def test_rebuild_links_empty_context(mock_rebuild_annots):
    """
    Hypothesis: If rebuild_links is called with an empty context
    (no pages), it should do nothing and return an empty list.
    """
    # 1. Arrange
    mock_pdf = MagicMock(spec=Pdf, pages=[])
    processed_page_info = []  # Empty list of pages
    mock_remapper_instance = MagicMock(spec=LinkRemapper)

    # 2. Act
    result_dests = rebuild_links(mock_pdf, processed_page_info, mock_remapper_instance)

    # 3. Assert
    assert result_dests == []

    # Assert its methods are NOT called
    mock_remapper_instance.set_call_context.assert_not_called()
    mock_rebuild_annots.assert_not_called()
