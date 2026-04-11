from unittest.mock import MagicMock, patch

import pytest

from pdftl.utils.outline_select import get_outlines_to_level, get_outlines_to_level_pages


# --- 2. Mock Classes to Simulate pikepdf ---
class MockOutlineItem:
    """Simulates a pikepdf.OutlineItem with a title and children."""

    def __init__(self, title, children=None):
        self.title = title
        self.children = children or []

    # Used to make test output readable if a test fails
    def __repr__(self):
        return f"<MockOutlineItem: {self.title}>"


class MockOutlineContext:
    """Simulates the context manager returned by pdf.open_outline()."""

    def __init__(self, root_items):
        self.root = root_items

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class MockPdf:
    """Simulates the pikepdf.Pdf object."""

    def __init__(self, root_items):
        self.root_items = root_items

    def open_outline(self):
        return MockOutlineContext(self.root_items)


# --- 3. Test Fixtures ---
@pytest.fixture
def sample_pdf():
    """
    Creates a mock PDF with the following bookmark tree:
    Level 1: Chapter 1
      Level 2: Section 1.1
        Level 3: Sub-section 1.1.1
      Level 2: Section 1.2
    Level 1: Chapter 2
    """
    tree = [
        MockOutlineItem(
            "Chapter 1",
            [
                MockOutlineItem("Section 1.1", [MockOutlineItem("Sub-section 1.1.1")]),
                MockOutlineItem("Section 1.2"),
            ],
        ),
        MockOutlineItem("Chapter 2"),
    ]
    return MockPdf(tree)


# --- 4. Test Cases ---
def test_to_level_1(sample_pdf):
    # Should return only Chapter 1 and Chapter 2
    result = get_outlines_to_level(sample_pdf, n=1, last_level_only=False)
    titles = [item.title for item in result]

    assert len(titles) == 2
    assert titles == ["Chapter 1", "Chapter 2"]


def test_to_level_2(sample_pdf):
    # Should return Chapters and Sections, but NOT the Sub-section
    result = get_outlines_to_level(sample_pdf, n=2, last_level_only=False)
    titles = [item.title for item in result]

    assert len(titles) == 4
    assert titles == ["Chapter 1", "Section 1.1", "Section 1.2", "Chapter 2"]


def test_last_level_only_level_2(sample_pdf):
    # Should skip Chapters and only return Sections
    result = get_outlines_to_level(sample_pdf, n=2, last_level_only=True)
    titles = [item.title for item in result]

    assert len(titles) == 2
    assert titles == ["Section 1.1", "Section 1.2"]


def test_last_level_only_level_3(sample_pdf):
    # Should only return the deepest Sub-section
    result = get_outlines_to_level(sample_pdf, n=3, last_level_only=True)
    titles = [item.title for item in result]

    assert len(titles) == 1
    assert titles == ["Sub-section 1.1.1"]


def test_level_exceeds_tree_depth(sample_pdf):
    # Asking for a level deeper than the tree goes should just return everything
    result = get_outlines_to_level(sample_pdf, n=99, last_level_only=False)
    titles = [item.title for item in result]

    assert len(titles) == 5
    assert "Sub-section 1.1.1" in titles


def test_zero_level(sample_pdf):
    # Level 0 is technically invalid since our recursion starts at 1, should return empty
    result = get_outlines_to_level(sample_pdf, n=0)
    assert len(result) == 0


# --- Helper Classes for Mocking ---
class MockPage:
    """Simulates a pikepdf.Page object with an objgen tuple."""

    def __init__(self, objgen):
        self.objgen = objgen


class MockResolvedDest:
    """Simulates the object returned by resolve_dest_to_page_num."""

    def __init__(self, page_num):
        self.page_num = page_num


# --- The Test ---
@patch("pdftl.utils.outline_select.get_outlines_to_level")
@patch("pdftl.utils.outline_select.get_named_destinations")
@patch("pdftl.utils.outline_select.resolve_dest_to_page_num")
def test_get_outlines_to_level_pages(mock_resolve, mock_get_named, mock_get_outlines):
    # 1. Setup the mock PDF with 3 fake pages
    mock_pdf = MagicMock()
    # objgen in pikepdf is typically a tuple like (object_number, generation_number)
    mock_pdf.pages = [MockPage((10, 0)), MockPage((20, 0)), MockPage((30, 0))]

    # 2. Setup mock outlines (simulate finding 3 bookmarks)
    mock_outline_1 = MagicMock(name="Outline 1")
    mock_outline_2 = MagicMock(name="Outline 2")
    mock_outline_unresolved = MagicMock(name="Outline Unresolved")
    mock_get_outlines.return_value = [mock_outline_1, mock_outline_2, mock_outline_unresolved]

    # 3. Setup mock named destinations dictionary
    mock_named_dests = {"Dest1": "DummyValue"}
    mock_get_named.return_value = mock_named_dests

    # 4. Setup the resolution logic
    # We want outline 1 to go to page 1, outline 2 to go to page 3,
    # and the last outline to fail resolution (return None)
    def resolve_side_effect(outline, page_map, named_dests):
        if outline == mock_outline_1:
            return MockResolvedDest(page_num=1)
        elif outline == mock_outline_2:
            return MockResolvedDest(page_num=3)
        else:
            return None  # Simulates a bookmark with a missing/broken destination

    mock_resolve.side_effect = resolve_side_effect

    # --- Execute the function ---
    result = get_outlines_to_level_pages(mock_pdf, n=2, last_level_only=False)

    # --- Assertions ---
    # 1. Verify the final result filters out the `None` and extracts page_num correctly
    assert result == [1, 3]

    # 2. Verify `get_outlines_to_level` was called with the right arguments
    mock_get_outlines.assert_called_once_with(mock_pdf, 2, False)

    # 3. Verify the `page_map` was generated correctly (1-indexed based on the mock pages)
    expected_page_map = {(10, 0): 1, (20, 0): 2, (30, 0): 3}

    # Check that resolve_dest_to_page_num was passed the correct page_map and named_dests
    mock_resolve.assert_any_call(mock_outline_1, expected_page_map, mock_named_dests)
