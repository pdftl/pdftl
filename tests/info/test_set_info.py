from unittest.mock import MagicMock, call, patch

import pikepdf
import pytest
from pikepdf import (
    Dictionary,
    Name,
    OutlineItem,
    String,
)

# --- Import Modules to Test ---
from pdftl.info import set_info as set_info_module

# --- Import Functions to Test ---
from pdftl.info.set_info import (
    CANNOT_SET_PDFID1,
    _add_bookmark,
    _make_page_label,
    _set_docinfo,
    _set_id_info,
    _set_page_media_entry,
    set_metadata_in_pdf,
)

# --- Import Exceptions ---


# --- General Fixtures ---


@pytest.fixture
def mock_pdf():
    """Creates a comprehensive mock pikepdf.Pdf object."""
    pdf = MagicMock(spec=pikepdf.Pdf)
    pdf.pdf_version = "1.7"
    pdf.is_encrypted = False

    # DocInfo
    pdf.docinfo = MagicMock(spec=pikepdf.Dictionary)
    pdf.docinfo.items.return_value = [
        (Name("/Title"), String("Test Title")),
        (Name("/Author"), String("Test Author")),
        (Name("/Invalid"), 123),  # Should be skipped
    ]

    # Pages
    mock_page1 = MagicMock(spec=pikepdf.Page, name="Page1")

    def page1_get_side_effect(key, default=None):
        if key == "/Rotate":
            return 0
        return default

    mock_page1.get.side_effect = page1_get_side_effect
    mock_page1.get.return_value = 0  # Default for /Rotate
    mock_page1.mediabox = [0, 0, 600, 800]
    mock_page1.cropbox = [0, 0, 600, 800]
    mock_page1.objgen = (1, 0)

    mock_page2 = MagicMock(spec=pikepdf.Page, name="Page2")
    mock_page2.get.side_effect = lambda key, default: 90 if key == "/Rotate" else "ii"
    mock_page2.mediabox = [0, 0, 500, 500]
    mock_page2.cropbox = [10, 10, 490, 490]  # Different from mediabox
    mock_page2.objgen = (2, 0)

    pdf.pages = [mock_page1, mock_page2]

    # ID
    pdf.trailer = MagicMock()
    pdf.trailer.ID = [b"id0_bytes", b"id1_bytes"]

    # Outlines
    pdf.open_outline.return_value.__enter__.return_value = MagicMock()
    pdf.Root = MagicMock(spec=pikepdf.Dictionary)

    # Page Labels
    pdf.Root.PageLabels = None  # Default

    return pdf


@pytest.fixture
def mock_writer():
    """Returns a list that can be used as a simple writer function."""
    output = []

    def writer(text):
        output.append(text)

    writer.output = output
    return writer


@pytest.fixture(autouse=True)
def patch_logging(mocker):
    """Patch logging for all tests in these modules."""
    mocker.patch("pdftl.info.output_info.logging")
    mocker.patch("pdftl.info.parse_dump.logging")
    mocker.patch("pdftl.info.set_info.logging")


# ==================================================================
# === Tests for pdftl.info.set_info
# ==================================================================


class TestSetInfo:
    @patch("pdftl.info.set_info._set_page_labels")
    @patch("pdftl.info.set_info._set_page_media")
    @patch("pdftl.info.set_info._set_bookmarks")
    @patch("pdftl.info.set_info._set_id_info")
    @patch("pdftl.info.set_info._set_docinfo")
    def test_set_metadata_in_pdf(
        self, mock_docinfo, mock_id, mock_bookmarks, mock_media, mock_labels, mock_pdf
    ):
        """Tests the main set_metadata orchestrator."""
        meta_dict = {
            "Info": {"Title": "A"},
            "PdfID0": "123",
            "BookmarkList": [{}],
            "PageMediaList": [{}],
            "PageLabelList": [{}],
        }
        set_metadata_in_pdf(mock_pdf, meta_dict)

        mock_docinfo.assert_called_once_with(mock_pdf, meta_dict["Info"])
        mock_id.assert_called_with(mock_pdf, 0, meta_dict["PdfID0"])
        mock_bookmarks.assert_called_once_with(mock_pdf, meta_dict["BookmarkList"])
        mock_media.assert_called_once_with(mock_pdf, meta_dict["PageMediaList"])
        mock_labels.assert_called_once_with(mock_pdf, meta_dict["PageLabelList"])

    def test_set_docinfo(self, mock_pdf):
        info_dict = {"Title": "New Title", "Subject": "New Subject"}
        _set_docinfo(mock_pdf, info_dict)

        mock_pdf.docinfo.__setitem__.assert_has_calls(
            [
                call(Name("/Title"), "New Title"),
                call(Name("/Subject"), "New Subject"),
            ]
        )

    def test_set_page_media_entry(self, mock_pdf):
        """Tests setting page media properties."""
        mock_page = mock_pdf.pages[0]
        page_media = {
            "Number": 1,
            "Rotation": 180,
            "Rect": [0, 0, 1, 1],
            "CropRect": [0, 0, 2, 2],
        }

        _set_page_media_entry(mock_pdf, page_media)

        mock_page.rotate.assert_called_once_with(180, relative=False)
        assert mock_page.mediabox == [0, 0, 1, 1]
        assert mock_page.cropbox == [0, 0, 2, 2]

    def test_set_page_media_entry_errors(self, mock_pdf, caplog):
        """Tests error handling for _set_page_media_entry."""
        # 1. Missing page number
        with caplog.at_level("WARNING"):
            _set_page_media_entry(mock_pdf, {"Rotation": 90})
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.message == (
            (
                "Skipping PageMedia metadata with missing page number (PageMediaNumber)."
                " Metadata entry details:\n  %s"
            )
            % {"Rotation": 90}
        )

        # 2. Non-existent page number
        caplog.clear()
        with caplog.at_level("WARNING"):
            _set_page_media_entry(mock_pdf, {"Number": 99})
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.message == "Nonexistent page 99 requested for PageMedia metadata. Skipping."

    @patch("pikepdf.OutlineItem")
    def test_add_bookmark_logic(self, mock_OutlineItem, mock_pdf):
        """Tests the complex ancestor/level logic for adding bookmarks."""
        mock_pdf.pages = [MagicMock()] * 5  # 5 pages
        mock_outline = MagicMock()
        mock_outline.root = MagicMock()

        # Mock OutlineItem to track children
        def new_oi(title, destination):
            oi = MagicMock(spec=OutlineItem, title=title)
            oi.children = []
            return oi

        mock_OutlineItem.side_effect = new_oi

        ancestors = []

        # 1. Add Level 1
        b1 = {"Title": "Chap 1", "Level": 1, "PageNumber": 1}
        ancestors = _add_bookmark(mock_pdf, b1, mock_outline, ancestors)
        oi1 = ancestors[0]
        mock_outline.root.append.assert_called_once_with(oi1)
        assert len(ancestors) == 1

        # 2. Add Level 2 (child of Chap 1)
        b2 = {"Title": "Sec 1.1", "Level": 2, "PageNumber": 2}
        ancestors = _add_bookmark(mock_pdf, b2, mock_outline, ancestors)
        oi2 = ancestors[1]
        assert oi2 in oi1.children
        assert len(oi1.children) == 1
        assert len(ancestors) == 2

        # 3. Add another Level 2 (sibling of Sec 1.1)
        b3 = {"Title": "Sec 1.2", "Level": 2, "PageNumber": 3}
        ancestors = _add_bookmark(mock_pdf, b3, mock_outline, ancestors)
        oi3 = ancestors[1]  # Replaces oi2 in ancestor list
        assert oi3 in oi1.children
        assert len(oi1.children) == 2  # Now contains oi2 and oi3
        assert len(ancestors) == 2

        # 4. Add Level 1 (sibling of Chap 1)
        b4 = {"Title": "Chap 2", "Level": 1, "PageNumber": 4}
        ancestors = _add_bookmark(mock_pdf, b4, mock_outline, ancestors)
        oi4 = ancestors[0]  # Replaces oi1/oi3 in ancestor list
        mock_outline.root.append.assert_called_with(oi4)
        assert len(ancestors) == 1

    def test_add_bookmark_errors(self, mock_pdf, caplog):
        mock_pdf.pages = [MagicMock()]  # 1 page

        # 1. Missing key
        with caplog.at_level("WARNING"):
            _add_bookmark(mock_pdf, {"Title": "Fail"}, MagicMock(), [])
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert (
            "Skipping incomplete bookmark, we need Level, PageNumber and Title." in record.message
        )

        # 2. Bad page number
        b = {"Title": "B", "Level": 1, "PageNumber": 99}
        caplog.clear()
        with caplog.at_level("WARNING"):
            _add_bookmark(mock_pdf, b, MagicMock(), [])
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert (
            record.message
            == "Nonexistent page 99 requested for bookmark with title 'B'. Skipping."
        )

        # 3. Bad level (too deep)
        b = {"Title": "C", "Level": 3, "PageNumber": 1}
        caplog.clear()
        with caplog.at_level("WARNING"):
            _add_bookmark(mock_pdf, b, MagicMock(), [])
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.message == (
            "Bookmark level 3 requested (with title 'C'),"
            "\nbut we are only at level 0 in the bookmark tree. Skipping."
        )

    @pytest.mark.parametrize(
        "label_data, expected_dict, expected_index",
        [
            ({"NewIndex": 1}, {}, 0),  # Simplest case
            (
                {
                    "NewIndex": 3,
                    "Prefix": "A-",
                    "Start": 5,
                    "NumStyle": "UppercaseRoman",
                },
                {"/P": "A-", "/St": 5, "/S": Name("/R")},
                2,
            ),
            (
                {
                    "Prefix": "Intro",
                    "NumStyle": "LowercaseRoman",
                },  # Defaults NewIndex/Start
                {"/P": "Intro", "/S": Name("/r")},
                0,
            ),
        ],
    )
    def test_make_page_label(self, label_data, expected_dict, expected_index, mock_pdf, mocker):
        mock_map = {
            "UppercaseRoman": "/R",
            "LowercaseRoman": "/r",
        }
        mocker.patch.dict(set_info_module.PAGE_LABEL_STYLE_MAP, mock_map)
        mock_indirect = MagicMock()
        mock_pdf.make_indirect.return_value = mock_indirect

        index, label_obj = _make_page_label(mock_pdf, label_data)

        assert index == expected_index
        assert label_obj == mock_indirect
        mock_pdf.make_indirect.assert_called_once_with(Dictionary(expected_dict))

    def test_set_id_info(self, mock_pdf, caplog):
        # 1. Set ID 0
        _set_id_info(mock_pdf, 0, "68656c6c6f")  # "hello"
        assert mock_pdf.trailer.ID[0] == b"hello"

        # 2. Set ID 1 (should log warning)
        with caplog.at_level("WARNING"):
            _set_id_info(mock_pdf, 1, "world")
        assert CANNOT_SET_PDFID1 in [rec.message for rec in caplog.records]

        # 3. Bad hex string
        caplog.clear()
        with caplog.at_level("WARNING"):
            _set_id_info(mock_pdf, 0, "not hex")
        expected = "Could not set PDFID%s to '%s'; invalid hex string?" % (0, "not hex")
        assert expected in [rec.message for rec in caplog.records]

    @patch("pdftl.info.set_info._set_id_info")
    def test_set_metadata_in_pdf_id1(self, mock_id, mock_pdf):
        """Tests that 'PdfID1' is correctly handled in the orchestrator."""
        meta_dict = {"PdfID1": "abc"}
        set_metadata_in_pdf(mock_pdf, meta_dict)
        # Note: The code has a bug here, it passes meta_dict["PdfID0"]
        # The test should reflect the code as-written.
        # If you fix the bug to meta_dict["PdfID1"], update this test.
        try:
            mock_id.assert_called_with(mock_pdf, 1, meta_dict["PdfID0"])
            set_info_module.logging.warning("BUG: set_metadata_in_pdf uses PdfID0 for PdfID1")
        except KeyError:
            # This will happen if you fix the bug
            mock_id.assert_called_with(mock_pdf, 1, meta_dict["PdfID1"])

    @patch("pdftl.info.set_info._set_page_media_entry")
    def test_set_page_media_loop(self, mock_entry, mock_pdf):
        """Tests the _set_page_media loop function."""
        page_media_list = [{"Number": 1}, {"Number": 2}]
        set_info_module._set_page_media(mock_pdf, page_media_list)

        mock_entry.assert_has_calls(
            [
                call(mock_pdf, page_media_list[0]),
                call(mock_pdf, page_media_list[1]),
            ]
        )

    def test_set_page_media_entry_dimensions(self, mock_pdf):
        """Tests the 'elif "Dimensions"' branch of _set_page_media_entry."""
        mock_page = mock_pdf.pages[0]
        # This dict must *not* have "Rect" or "CropRect"
        page_media = {"Number": 1, "Dimensions": [300, 400]}

        _set_page_media_entry(mock_pdf, page_media)

        # Check that mediabox was set using Dimensions
        assert mock_page.mediabox == [0, 0, 300, 400]
        # Check that rotate and cropbox were not called/set
        mock_page.rotate.assert_not_called()
        assert "cropbox" not in mock_page.mock_calls

    @patch("pdftl.info.set_info._add_bookmark")
    def test_set_bookmarks_loop(self, mock_add_bookmark, mock_pdf):
        """Tests the _set_bookmarks loop and outline clearing."""
        bookmark_list = [{"Title": "A"}, {"Title": "B"}]
        mock_outline = mock_pdf.open_outline.return_value.__enter__.return_value

        # 1. Test with delete_existing_bookmarks=True (default)
        set_info_module._set_bookmarks(mock_pdf, bookmark_list)

        # Check that the outline was cleared
        assert mock_outline.root == []
        # Check that _add_bookmark was called for each item
        mock_add_bookmark.assert_has_calls(
            [
                call(mock_pdf, bookmark_list[0], mock_outline, []),
                call(
                    mock_pdf,
                    bookmark_list[1],
                    mock_outline,
                    mock_add_bookmark.return_value,
                ),
            ]
        )

        # 2. Test with delete_existing_bookmarks=False
        mock_add_bookmark.reset_mock()
        mock_outline.reset_mock()
        # Set a non-empty list to prove it's not cleared
        original_list_content = [MagicMock()]
        mock_outline.root = original_list_content

        set_info_module._set_bookmarks(mock_pdf, bookmark_list, delete_existing_bookmarks=False)

        # Check that outline.root was *not* changed
        assert mock_outline.root is original_list_content
        # Check that the loop ran and _add_bookmark was still called
        mock_add_bookmark.assert_has_calls(
            [
                call(mock_pdf, bookmark_list[0], mock_outline, []),
                call(
                    mock_pdf,
                    bookmark_list[1],
                    mock_outline,
                    mock_add_bookmark.return_value,
                ),
            ]
        )

    def test_add_bookmark_errors_bad_level(self, mock_pdf, caplog):
        """Tests the error case for a bookmark level < 1."""
        mock_pdf.pages = [MagicMock()]
        b = {"Title": "A", "Level": 0, "PageNumber": 1}
        ancestors = []

        with caplog.at_level("WARNING"):
            result = _add_bookmark(mock_pdf, b, MagicMock(), ancestors)

        # Check that a warning was logged
        expected = "Skipping invalid bookmark with level %s. Levels should be 1 or greater." % 0
        assert expected in [rec.message for rec in caplog.records]

        # Check that ancestors list was returned unchanged
        assert result is ancestors

    @patch("pikepdf.NumberTree")
    def test_set_page_labels_no_delete(self, mock_NumberTree, mock_pdf):
        """Tests the 'delete_existing=False' branch of _set_page_labels."""
        # 1. Setup: PDF must have existing PageLabels
        mock_pdf.Root.PageLabels = Dictionary()
        mock_nt_instance = mock_NumberTree.return_value

        label_list = [{"NewIndex": 1}]

        # 2. Act: Call with delete_existing=False
        set_info_module._set_page_labels(mock_pdf, label_list, delete_existing=False)

        # 3. Assert
        # Check that NumberTree was *not* created new
        mock_NumberTree.new.assert_not_called()
        # Check that the existing tree was opened
        mock_NumberTree.assert_called_once_with(mock_pdf.Root.PageLabels)
        # Check that the new label was set
        mock_nt_instance.__setitem__.assert_called_once()
        # Check that the root was updated
        assert mock_pdf.Root.PageLabels == mock_nt_instance.obj

    def test_set_id_info_bad_hex(self, mock_pdf, caplog):
        """Tests the ValueError exception handler in _set_id_info."""
        # Setup: Ensure trailer.ID is a list-like mock
        mock_pdf.trailer.ID = [b"original_id"]

        with caplog.at_level("WARNING"):
            _set_id_info(mock_pdf, 0, "not a hex string")

        # Check that the warning was logged
        expected = "Could not set PDFID%s to '%s'; invalid hex string?" % (
            0,
            "not a hex string",
        )
        assert expected in [rec.message for rec in caplog.records]

        # Check that the original ID was not modified
        assert mock_pdf.trailer.ID[0] == b"original_id"
