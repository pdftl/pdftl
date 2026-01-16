import json

from pdftl.info.info_types import (
    BookmarkEntry,
    DocInfoEntry,
    PageLabelEntry,
    PageMediaEntry,
    PdfInfo,
)


def test_info_types_serialization():
    """Cover .to_dict() and .to_json() methods."""

    # 1. PageLabelEntry to_dict
    pl = PageLabelEntry(new_index=1, num_style="RomanUpper")
    assert pl.to_dict() == {"NewIndex": 1, "NumStyle": "RomanUpper", "Start": 1}

    # 2. PageMediaEntry to_dict
    pm = PageMediaEntry(page_number=1, rotation=90, media_rect=[0, 0, 100, 100])
    d_pm = pm.to_dict()
    assert d_pm["PageNumber"] == 1
    assert d_pm["Rotation"] == 90
    assert d_pm["MediaRect"] == [0, 0, 100, 100]

    # 3. BookmarkEntry to_dict
    bm = BookmarkEntry(title="Chapter 1", level=1, page_number=5)
    d_bm = bm.to_dict()
    assert d_bm["Title"] == "Chapter 1"
    assert d_bm["PageNumber"] == 5

    # 4. PdfInfo to_json (full serialization)
    info = PdfInfo(
        pages=10,
        page_media=[pm],
        bookmarks=[bm],
        page_labels=[pl],
        doc_info=[DocInfoEntry("Title", "Test Doc")],
    )

    json_str = info.to_json()
    data = json.loads(json_str)

    assert data["NumberOfPages"] == 10
    assert data["PageMedia"][0]["Rotation"] == 90
    # Verify DocInfo flattening in to_dict logic
    assert data["Info"]["Title"] == "Test Doc"


def test_bookmark_entry_lowercase_children():
    """Test BookmarkEntry.from_dict with lowercase 'children' key."""
    data = {
        "Title": "Root",
        "Level": 1,
        "PageNumber": 1,
        "children": [{"Title": "Child", "Level": 2, "PageNumber": 2}],
    }

    bm = BookmarkEntry.from_dict(data)
    assert len(bm.children) == 1
    assert bm.children[0].title == "Child"


import pytest

from pdftl.info.info_types import _fuzzy_create


def test_pdf_info_from_dict_complex():
    """Test PdfInfo deserialization with IDs and nested bookmarks."""
    data = {
        "PdfID0": "abc",
        "PdfID1": "def",
        "BookmarkList": [
            {
                "Title": "Chapter 1",
                "Level": 1,
                "PageNumber": 1,
                # Use capitalized key to hit the 'elif "Children" in d:' branch (line 62)
                "Children": [{"Title": "Section 1.1", "Level": 2, "PageNumber": 2}],
            }
        ],
    }

    info = PdfInfo.from_dict(data)

    # Test ID parsing (Line 106)
    assert info.ids == ["abc", "def"]

    # Test Bookmark Recursion (Lines 54-70)
    assert len(info.bookmarks) == 1
    root = info.bookmarks[0]
    assert isinstance(root, BookmarkEntry)
    assert root.title == "Chapter 1"

    assert len(root.children) == 1
    child = root.children[0]
    assert isinstance(child, BookmarkEntry)
    assert child.title == "Section 1.1"


def test_fuzzy_create_guard():
    """Test the guard clause in _fuzzy_create (Line 12)."""
    # If data is not a dict, it should return it as-is
    assert _fuzzy_create(PdfInfo, "not-a-dict") == "not-a-dict"


from unittest.mock import MagicMock, patch

from pdftl.info import output_info

# --- Part 1: Test info_types.py (Serialization/Deserialization) ---


def test_pdf_info_round_trip():
    """
    Excercises almost all lines in info_types.py by doing a full
    Python -> Dict -> Python round trip with all optional fields.
    """
    original = PdfInfo(
        pages=5,
        ids=["id_a", "id_b"],
        file_path="test.pdf",
        doc_info=[DocInfoEntry("Title", "Test Doc")],
        bookmarks=[BookmarkEntry("Chapter 1", 1, 1, children=[BookmarkEntry("Sec 1.1", 2, 2)])],
        page_media=[PageMediaEntry(page_number=1, rotation=90, media_rect=[0, 0, 100, 100])],
        page_labels=[PageLabelEntry(new_index=1, start=1, prefix="A-", num_style="D")],
    )

    # 1. Test to_dict (Serialization)
    # This hits info_types.py lines 21-35 (factory), 79, 118, 133, 183-196
    serialized = original.to_dict()

    # Verify strict pdftk-compatibility of the output keys
    assert serialized["NumberOfPages"] == 5
    assert serialized["PdfID"] == {"0": "id_a", "1": "id_b"}
    assert serialized["Info"] == {"Title": "Test Doc"}
    assert serialized["PageMedia"][0]["MediaRect"] == [
        0,
        0,
        100,
        100,
    ]  # Auto-renamed from media_rect
    assert serialized["Bookmarks"][0]["Title"] == "Chapter 1"
    assert serialized["Bookmarks"][0]["Children"][0]["Title"] == "Sec 1.1"

    # 2. Test from_dict (Deserialization)
    # This hits info_types.py lines 99-115, 150-179
    reconstructed = PdfInfo.from_dict(serialized)

    # Verify equality
    assert reconstructed.pages == original.pages
    assert reconstructed.ids == original.ids
    # Note: doc_info equality might depend on order, but here it's simple
    assert reconstructed.doc_info[0].key == "Title"
    assert len(reconstructed.bookmarks) == 1
    assert reconstructed.bookmarks[0].children[0].title == "Sec 1.1"


def test_fuzzy_matching_and_fallbacks():
    """
    Tests the fuzzy logic and specific fallbacks (like 'rect' -> 'media_rect').
    Hits info_types.py lines 60-61.
    """
    # Legacy data using 'rect' instead of 'MediaRect' or 'media_rect'
    legacy_data = {
        "PageMediaList": [{"PageNumber": 1, "Rect": [0, 0, 50, 50]}]  # Should map to media_rect
    }

    info = PdfInfo.from_dict(legacy_data)
    assert info.page_media[0].media_rect == [0, 0, 50, 50]


# --- Part 2: Test output_info.py (Writer Coverage) ---


def test_write_info_full_coverage():
    """
    Calls write_info with a fully populated object to hit all 'if' branches.
    Hits output_info.py lines 119, 140, 152, 157, 163, 182, 186.
    """
    mock_writer = MagicMock()

    info = PdfInfo(
        pages=10,
        ids=["abc", "def"],
        file_path="/tmp/test.pdf",
        version="1.7",
        encrypted=False,
        doc_info=[DocInfoEntry("Title", "MyPDF")],
        bookmarks=[BookmarkEntry("Intro", 1, 1)],
        page_media=[
            PageMediaEntry(
                page_number=1,
                rotation=0,
                media_rect=[0, 0, 100, 200],
                dimensions=("100", "200"),
                crop_rect=[10, 10, 90, 190],  # Triggers crop_rect logic
            )
        ],
        page_labels=[
            PageLabelEntry(
                new_index=1, start=1, prefix="ix", num_style="r"
            )  # Triggers prefix logic
        ],
    )

    # Call with extra_info=True to hit _write_extra_info (lines 163-164)
    output_info.write_info(mock_writer, info, extra_info=True)

    # Gather all calls to the writer
    output_text = "\n".join(c.args[0] for c in mock_writer.call_args_list)

    # Assertions for specific branches
    assert "File: /tmp/test.pdf" in output_text  # extra_info
    assert "PdfID0: abc" in output_text  # ids
    assert "BookmarkTitle: Intro" in output_text  # bookmarks
    # pdf_rect_to_string output is "x y w h", not list brackets
    assert "PageMediaCropRect: 10 10 90 190" in output_text
    assert "PageLabelPrefix: ix" in output_text  # labels prefix


def test_get_info_mocked():
    """
    Mock pikepdf to hit the reading logic in get_info.
    Hits output_info.py lines 76, 99, 206, 210.
    """
    mock_pdf = MagicMock()
    mock_pdf.pages = [MagicMock(), MagicMock()]  # 2 pages
    mock_pdf.pdf_version = "1.7"
    mock_pdf.is_encrypted = False
    mock_pdf.docinfo = {"/Title": "MockTitle"}

    # Mock Page Media
    mock_pdf.pages[0].get.return_value = 0
    mock_pdf.pages[0].mediabox = [0, 0, 100, 100]
    mock_pdf.pages[1].get.return_value = 90
    mock_pdf.pages[1].mediabox = [0, 0, 100, 100]

    # Mock PageLabels
    mock_pdf.Root.PageLabels = "MockLabels"

    # Use unittest.mock.patch instead of pytest.patch
    with (
        patch("pikepdf.NumberTree") as MockTree,
        patch("pdftl.info.output_info.get_named_destinations", return_value={}),
        patch("pdftl.info.output_info.resolve_page_number", return_value=1),
    ):
        # Setup Mock Tree items
        mock_entry = MagicMock()
        mock_entry.S = "/D"  # Decimal style
        mock_entry.St = 1
        mock_entry.P = "Page-"

        # MockTree instance behavior
        instance = MockTree.return_value
        instance.items.return_value = [(0, mock_entry)]

        # Run
        info = output_info.get_info(mock_pdf, "dummy.pdf", extra_info=True)

        # Verify
        # "D" maps to "DecimalArabicNumerals" in pdftl constants
        assert info.page_labels[0].num_style == "DecimalArabicNumerals"
        assert info.page_labels[0].prefix == "Page-"
        assert info.pages == 2


from pdftl.exceptions import PdftlConfigError  # Assuming this is where it lives

# --- Tests for PageMediaEntry (Missing page_number) ---


def test_fuzzy_create_page_media_missing_all():
    """Test that creating PageMediaEntry with no data raises PdftlConfigError."""
    with pytest.raises(PdftlConfigError) as excinfo:
        _fuzzy_create(PageMediaEntry, {})

    assert "PageMediaEntry" in str(excinfo.value)
    assert "page_number" in str(excinfo.value)


def test_fuzzy_create_page_media_wrong_keys():
    """Test that having data but not the 'required' key still fails."""
    # 'rotation' is optional, 'page_number' is required.
    bad_data = {"Rotation": 90, "Author": "Unknown"}

    with pytest.raises(PdftlConfigError) as excinfo:
        _fuzzy_create(PageMediaEntry, bad_data)

    assert "page_number" in str(excinfo.value)


# --- Tests for Alias Resolution + Failure ---


def test_fuzzy_create_alias_success():
    """Verify that 'Number' alias works for PageMediaEntry (Happy Path)."""
    # This proves the fuzzy logic is working before we test its failure
    data = {"Number": 5}
    entry = _fuzzy_create(PageMediaEntry, data)
    assert entry.page_number == 5


def test_fuzzy_create_page_label_missing_index():
    """PageLabelEntry requires 'index'. Test failure when only 'start' is provided."""
    data = {"start": 10}  # 'index' is missing

    with pytest.raises(PdftlConfigError) as excinfo:
        _fuzzy_create(PageLabelEntry, data)

    assert "index" in str(excinfo.value)


# --- Testing the 'safe_create' behavior directly ---


def test_safe_create_ignores_extra_args():
    """
    If safe_create filters extra args (as discussed),
    ensure it doesn't crash on 'unexpected keyword argument'.
    """
    data = {"page_number": 1, "garbage_key": "some_value"}
    # This should succeed because safe_create strips garbage_key
    entry = _fuzzy_create(PageMediaEntry, data)
    assert entry.page_number == 1
    assert not hasattr(entry, "garbage_key")
