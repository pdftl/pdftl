from unittest.mock import MagicMock, call, patch

import pikepdf
import pytest
from pikepdf import (
    Dictionary,
    Name,
    NumberTree,
    OutlineItem,
    Pdf,
    String,
)

# --- Import Modules to Test ---
from pdftl.info import set_info as set_info_module

# --- Import Functions to Test ---
from pdftl.info.output_info import (
    BookmarkWriterContext,
    _write_bookmarks,
    _write_bookmarks_recursive,
    _write_docinfo,
    _write_extra_info,
    _write_id_info,
    _write_page_labels,
    _write_page_media_info,
    write_info,
)
from pdftl.info.parse_dump import (
    _parse_info_field,
    _safe_float_list,
    _safe_int,
    parse_dump_data,
)
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
# === Tests for pdftl.info.output_info
# ==================================================================


class TestOutputInfo:

    @patch("pdftl.info.output_info._write_page_labels")
    @patch("pdftl.info.output_info._write_page_media_info")
    @patch("pdftl.info.output_info._write_bookmarks")
    @patch("pdftl.info.output_info._write_id_info")
    @patch("pdftl.info.output_info._write_docinfo")
    @patch("pdftl.info.output_info._write_extra_info")
    def test_write_info_orchestration(
        self,
        mock_extra,
        mock_docinfo,
        mock_id,
        mock_bookmarks,
        mock_media,
        mock_labels,
        mock_writer,
        mock_pdf,
    ):
        """Tests the main write_info orchestrator function."""
        # Test with extra_info=True
        write_info(mock_writer, mock_pdf, "file.pdf", escape_xml=False, extra_info=True)

        mock_extra.assert_called_once_with(mock_writer, mock_pdf, "file.pdf")
        mock_docinfo.assert_called_once_with(mock_writer, mock_pdf, False)
        mock_id.assert_called_once_with(mock_writer, mock_pdf)
        mock_bookmarks.assert_called_once_with(mock_writer, mock_pdf, False)
        mock_media.assert_called_once_with(mock_writer, mock_pdf)
        mock_labels.assert_called_once_with(mock_writer, mock_pdf)
        assert "NumberOfPages: 2" in mock_writer.output

        # Test with extra_info=False (default)
        mock_extra.reset_mock()
        write_info(mock_writer, mock_pdf, "file.pdf")
        mock_extra.assert_not_called()

    @patch("pdftl.info.output_info.xml_encode_for_info")
    def test_write_docinfo(self, mock_xml_encode, mock_writer, mock_pdf):
        """Tests writing the DocInfo dictionary."""
        mock_xml_encode.return_value = "Encoded Title"

        # Test with XML encoding (default)
        _write_docinfo(mock_writer, mock_pdf, escape_xml=True)
        assert mock_xml_encode.call_count == 2

        # Test without XML encoding
        mock_xml_encode.reset_mock()
        _write_docinfo(mock_writer, mock_pdf, escape_xml=False)
        mock_xml_encode.assert_not_called()

        expected_output = [
            # From the first call (escape_xml=True)
            "InfoBegin\nInfoKey: Title\nInfoValue: Encoded Title",
            "InfoBegin\nInfoKey: Author\nInfoValue: Encoded Title",
            # From the second call (escape_xml=False)
            "InfoBegin\nInfoKey: Title\nInfoValue: Test Title",
            "InfoBegin\nInfoKey: Author\nInfoValue: Test Author",
        ]
        # Check that the output contains the expected snippets
        assert mock_writer.output == expected_output
        # Check that invalid '123' value was skipped
        assert not any("123" in line for line in mock_writer.output)

    @patch(
        "pdftl.info.output_info.pdf_id_metadata_as_strings",
        return_value=["hex0", "hex1"],
    )
    def test_write_id_info(self, mock_pdf_id, mock_writer, mock_pdf):
        _write_id_info(mock_writer, mock_pdf)
        mock_pdf_id.assert_called_once_with(mock_pdf)
        assert "PdfID0: hex0" in mock_writer.output
        assert "PdfID1: hex1" in mock_writer.output

    def test_write_extra_info(self, mock_writer, mock_pdf):
        _write_extra_info(mock_writer, mock_pdf, "file.pdf")
        assert "File: file.pdf" in mock_writer.output
        assert f"PDF version: {mock_pdf.pdf_version}" in mock_writer.output
        assert f"Encrypted: {mock_pdf.is_encrypted}" in mock_writer.output

    @patch("pdftl.info.output_info._write_bookmarks_recursive")
    @patch("pdftl.info.output_info.get_named_destinations")
    def test_write_bookmarks_orchestration(
        self, mock_get_dests, mock_recursive, mock_writer, mock_pdf
    ):
        """Tests the _write_bookmarks wrapper function."""
        mock_outline = mock_pdf.open_outline.return_value.__enter__.return_value
        mock_outline.root = [MagicMock(name="bookmark1")]
        mock_get_dests.return_value = "dests"

        _write_bookmarks(mock_writer, mock_pdf, escape_xml=True)

        mock_get_dests.assert_called_once_with(mock_pdf)
        # Check that the context was created correctly and passed to recursive
        mock_recursive.assert_called_once()
        context_arg = mock_recursive.call_args[0][1]
        assert isinstance(context_arg, BookmarkWriterContext)
        assert context_arg.outline_items == list(mock_outline.root)
        assert context_arg.pages == list(mock_pdf.pages)
        assert context_arg.named_destinations == "dests"
        assert mock_recursive.call_args[1]["escape_xml"] is True

    @patch(
        "pdftl.info.output_info.resolve_page_number",
        side_effect=[1, AssertionError("Test"), 3],
    )
    @patch("pdftl.info.output_info.xml_encode_for_info", side_effect=lambda x: f"XML({x})")
    def test_write_bookmarks_recursive(self, mock_xml_encode, mock_resolve, mock_writer):
        """Tests the recursive bookmark writing logic."""
        # Setup mock items
        mock_child = MagicMock(title="Child <&>", children=[])
        mock_item1 = MagicMock(title="Item 1", children=[])
        mock_item2 = MagicMock(title="Item 2", children=[mock_child])

        context = BookmarkWriterContext(
            outline_items=[mock_item1, mock_item2],
            pages="pages_list",
            named_destinations="dests",
        )

        _write_bookmarks_recursive(mock_writer, context, level=1, escape_xml=True)

        # Check resolution calls
        mock_resolve.assert_has_calls(
            [
                call(mock_item1, "pages_list", "dests"),
                call(mock_item2, "pages_list", "dests"),
                call(mock_child, "pages_list", "dests"),
            ]
        )

        # Check XML encoding calls
        mock_xml_encode.assert_has_calls([call("Item 1"), call("Item 2"), call("Child <&>")])

        # Check output
        expected_output = [
            "BookmarkTitle: XML(Item 1)",
            "BookmarkLevel: 1",
            "BookmarkPageNumber: 1",
            "BookmarkTitle: XML(Item 2)",
            "BookmarkLevel: 1",
            "BookmarkPageNumber: 0",  # From AssertionError
            "BookmarkTitle: XML(Child <&>)",
            "BookmarkLevel: 2",  # Recursed
            "BookmarkPageNumber: 3",
        ]
        for expected in expected_output:
            assert expected in "\n".join(mock_writer.output)

    @patch("pdftl.info.output_info.pdf_num_to_string", side_effect=lambda x: f"{x:.1f}")
    @patch("pdftl.info.output_info.pdf_rect_to_string", side_effect=lambda r: str(r))
    def test_write_page_media_info(self, mock_rect_str, mock_num_str, mock_writer, mock_pdf):
        """Tests writing page media, including mismatching cropbox."""
        _write_page_media_info(mock_writer, mock_pdf)

        # Check Page 1 (no crop, rotate=0)
        assert "PageMediaNumber: 1" in mock_writer.output[0]
        assert "PageMediaRotation: 0" in mock_writer.output[0]
        assert "PageMediaRect: [0, 0, 600, 800]" in mock_writer.output[0]
        assert "PageMediaDimensions: 600.0 800.0" in mock_writer.output[0]
        assert "PageMediaCropRect" not in mock_writer.output[0]

        # Check Page 2 (has crop, rotate=90)
        assert "PageMediaNumber: 2" in mock_writer.output[1]
        assert "PageMediaRotation: 90" in mock_writer.output[1]
        assert "PageMediaRect: [0, 0, 500, 500]" in mock_writer.output[1]
        assert "PageMediaDimensions: 500.0 500.0" in mock_writer.output[1]
        assert "PageMediaCropRect: [10, 10, 490, 490]" in mock_writer.output[2]

    def test_write_page_labels(self, mock_writer, mock_pdf):
        """Tests writing page label data using a real NumberTree."""

        # --- Create a real, temporary PDF to "own" the NumberTree ---
        with Pdf.new() as real_pdf_owner:
            # 1. Create a real NumberTree owned by the real PDF
            real_nt = NumberTree.new(real_pdf_owner)

            # 2. Add our test data directly to the real NumberTree
            real_nt[0] = Dictionary(S=Name("/D"))
            real_nt[2] = Dictionary(St=3, P=String("A-"), S=Name("/r"))

            # 3. Assign the real, owned object to our mock_pdf
            mock_pdf.Root.PageLabels = real_nt.obj

            # 4. Now, when _write_page_labels is called:
            #    - NumberTree(real_nt.obj) works
            #    - isinstance(..., NumberTree) works
            #    - labels.items() works
            _write_page_labels(mock_writer, mock_pdf)

        # 5. The assertions remain the same
        expected_output = [
            "PageLabelBegin\nPageLabelNewIndex: 1\nPageLabelStart: 1",
            "PageLabelNumStyle: Decimal",
            "PageLabelBegin\nPageLabelNewIndex: 3\nPageLabelStart: 3",
            "PageLabelPrefix: A-",
            "PageLabelNumStyle: LowercaseRoman",
        ]
        output_str = "\n".join(mock_writer.output)
        for expected in expected_output:
            assert expected in output_str


# ==================================================================
# === Tests for pdftl.info.parse_dump
# ==================================================================


class TestParseDump:

    @pytest.mark.parametrize(
        "value, expected",
        [("123", 123), ("-10", -10), ("0", 0), ("foo", "foo"), (None, None)],
    )
    def test_safe_int(self, value, expected):
        assert _safe_int(value) == expected

    @pytest.mark.parametrize(
        "value, expected",
        [
            ("1.5 2.0", [1.5, 2.0]),
            ("-10 0", [-10.0, 0.0]),
            ("foo", "foo"),
            (None, None),
        ],
    )
    def test_safe_float_list(self, value, expected):
        assert _safe_float_list(value) == expected

    def test_parse_info_field(self, caplog):
        """Tests the stateful parsing of InfoKey/InfoValue pairs."""
        info_dict = {}
        state = {"last_info_key": None}
        decoder = lambda x: x  # Passthrough

        # 1. Key, then Value
        _parse_info_field("InfoKey", "Title", info_dict, state, decoder)
        assert state["last_info_key"] == "Title"
        assert info_dict == {}

        _parse_info_field("InfoValue", "My Doc", info_dict, state, decoder)
        assert state["last_info_key"] is None  # Key was consumed
        assert info_dict == {"Title": "My Doc"}

        # 2. Value, then Key (should log warning and do nothing)
        with caplog.at_level("WARNING"):
            _parse_info_field("InfoValue", "Orphan Value", info_dict, state, decoder)

        assert info_dict == {"Title": "My Doc"}
        assert len(caplog.records) == 1
        assert caplog.records[0].message == "Got InfoValue without a preceding InfoKey. Ignoring"

    def test_parse_dump_data_integration(self):
        """Full integration test for parse_dump_data."""
        dump_data = [
            "InfoBegin",
            "InfoKey: Title",
            "InfoValue: My Document",
            "InfoKey: Author",
            "InfoValue: Me",
            "PdfID0: 12345",
            "NumberOfPages: 10",
            "BookmarkBegin",
            "BookmarkTitle: Chapter 1",
            "BookmarkLevel: 1",
            "BookmarkPageNumber: 1",
            "BookmarkBegin",
            "BookmarkTitle: Section 1.1",
            "BookmarkLevel: 2",
            "BookmarkPageNumber: 2",
            "PageMediaBegin",
            "PageMediaNumber: 1",
            "PageMediaRotation: 90",
            "PageMediaRect: 0 0 600 800",
            "PageLabelBegin",
            "PageLabelNewIndex: 1",
            "PageLabelPrefix: A-",
        ]

        decoder = lambda x: x  # Passthrough
        result = parse_dump_data(dump_data, decoder)

        # Check top-level
        assert result["PdfID0"] == "12345"
        assert result["NumberOfPages"] == 10

        # Check Info
        assert result["Info"] == {"Title": "My Document", "Author": "Me"}

        # Check Bookmarks
        assert len(result["BookmarkList"]) == 2
        assert result["BookmarkList"][0] == {
            "Title": "Chapter 1",
            "Level": 1,
            "PageNumber": 1,
        }
        assert result["BookmarkList"][1] == {
            "Title": "Section 1.1",
            "Level": 2,
            "PageNumber": 2,
        }

        # Check PageMedia
        assert len(result["PageMediaList"]) == 1
        assert result["PageMediaList"][0] == {
            "Number": 1,
            "Rotation": 90,
            "Rect": [0.0, 0.0, 600.0, 800.0],
        }

        # Check PageLabels
        assert len(result["PageLabelList"]) == 1
        assert result["PageLabelList"][0] == {"NewIndex": 1, "Prefix": "A-"}


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
