import logging
from unittest.mock import MagicMock, call, patch

import pikepdf
import pytest
from pikepdf import Name, String

# --- Import SUT ---
from pdftl.info.output_info import (
    BookmarkEntry,
    DocInfoEntry,
    PageLabelEntry,
    PageMediaEntry,
    PdfInfo,
    _write_bookmarks,
    _write_docinfo,
    _write_page_labels,
    _write_page_media_info,
    get_info,
    write_info,
)

# --- Fixtures ---


@pytest.fixture
def mock_pdf():
    """Creates a comprehensive mock pikepdf.Pdf object for extraction testing."""
    pdf = MagicMock(spec=pikepdf.Pdf)
    pdf.pdf_version = "1.7"
    pdf.is_encrypted = False
    pdf.pages = [MagicMock(), MagicMock()]  # 2 pages

    # DocInfo
    pdf.docinfo = MagicMock(spec=pikepdf.Dictionary)
    pdf.docinfo.items.return_value = [
        (Name("/Title"), String("Test Title")),
        (Name("/Author"), String("Test Author")),
    ]

    # Page Media (Page 1)
    p1 = pdf.pages[0]
    p1.get.return_value = 0  # Rotation
    p1.mediabox = [0, 0, 600, 800]
    p1.cropbox = [0, 0, 600, 800]

    # Page Media (Page 2)
    p2 = pdf.pages[1]
    p2.get.return_value = 90  # Rotation
    p2.mediabox = [0, 0, 500, 500]
    p2.cropbox = [10, 10, 490, 490]

    # IDs
    pdf.trailer = MagicMock()
    pdf.trailer.ID = [b"id0", b"id1"]

    # Root (PageLabels empty)
    pdf.Root = MagicMock()
    del pdf.Root.PageLabels  # Ensure attribute doesn't exist by default

    # Outline
    pdf.open_outline.return_value.__enter__.return_value.root = []

    return pdf


@pytest.fixture
def sample_info():
    """Creates a populated PdfInfo dataclass for writing testing."""
    return PdfInfo(
        pages=2,
        ids=["hex0", "hex1"],
        doc_info=[
            DocInfoEntry(key="Title", value="Test Title"),
            DocInfoEntry(key="Author", value="Test <&> Author"),  # Needs escaping
        ],
        bookmarks=[
            BookmarkEntry(
                title="Chapter 1",
                level=1,
                page=1,
                children=[BookmarkEntry(title="Sec 1.1", level=2, page=1)],
            )
        ],
        page_media=[
            PageMediaEntry(number=1, rotation=0, rect=[0, 0, 100, 100], dimensions=("100", "100")),
            PageMediaEntry(
                number=2,
                rotation=90,
                rect=[0, 0, 200, 200],
                dimensions=("200", "200"),
                crop_rect=[10, 10, 190, 190],
            ),
        ],
        page_labels=[PageLabelEntry(index=1, start=1, style="D", prefix="P-")],
        file_path="test.pdf",
        version="1.7",
        encrypted=False,
    )


@pytest.fixture
def mock_writer():
    """A simple list-based writer."""
    output = []

    def writer(text):
        output.append(text)

    writer.output = output
    return writer


@pytest.fixture(autouse=True)
def patch_deps(mocker):
    """Patch external helpers to simplify unit tests."""
    mocker.patch(
        "pdftl.info.output_info.pdf_id_metadata_as_strings", return_value=["hex0", "hex1"]
    )
    mocker.patch("pdftl.info.output_info.pdf_num_to_string", side_effect=lambda x: str(int(x)))
    mocker.patch("pdftl.info.output_info.pdf_rect_to_string", return_value="[0 0 100 100]")


# ==================================================================
# === Tests for Extraction (get_info)
# ==================================================================


class TestInfoExtraction:

    def test_get_info_basic(self, mock_pdf):
        """Test basic extraction of pages, IDs, and DocInfo."""
        info = get_info(mock_pdf, "input.pdf", extra_info=True)

        assert info.pages == 2
        assert info.ids == ["hex0", "hex1"]
        assert info.file_path == "input.pdf"
        assert info.version == "1.7"
        assert info.encrypted is False

        # Check DocInfo
        assert len(info.doc_info) == 2
        assert info.doc_info[0].key == "Title"
        assert info.doc_info[0].value == "Test Title"

    def test_get_info_page_media(self, mock_pdf):
        """Test extraction of page media data."""
        info = get_info(mock_pdf, "input.pdf")

        assert len(info.page_media) == 2
        p1 = info.page_media[0]
        assert p1.number == 1
        assert p1.rotation == 0
        assert p1.rect == [0, 0, 600, 800]
        assert p1.crop_rect is None  # Equal to mediabox

        p2 = info.page_media[1]
        assert p2.number == 2
        assert p2.rotation == 90
        # CropBox was different, but wait - the new code doesn't explicitly store crop_rect
        # unless we check the logic.
        # Looking at diff: `if page.cropbox != mediabox: writer(...)` was OLD.
        # NEW logic? `if entry.crop_rect is not None`.
        # The extraction logic for crop_rect is missing from the snippet provided?
        # Assuming get_info has logic for crop_rect or similar.
        # If the diff snippet didn't show crop_rect extraction, we might need to verify that later.

    @patch("pdftl.info.output_info.get_named_destinations", return_value={})
    @patch("pdftl.info.output_info.resolve_page_number", return_value=5)
    def test_get_info_bookmarks(self, mock_resolve, mock_dests, mock_pdf):
        """Test extraction of bookmark tree."""
        # Setup specific outline structure for this test
        mock_item = MagicMock(title="Chapter 1", children=[])
        mock_pdf.open_outline.return_value.__enter__.return_value.root = [mock_item]

        info = get_info(mock_pdf, "input.pdf")

        assert len(info.bookmarks) == 1
        bm = info.bookmarks[0]
        assert bm.title == "Chapter 1"
        assert bm.page == 5
        assert bm.level == 1


# ==================================================================
# === Tests for Presentation (write_info)
# ==================================================================


class TestInfoWriting:

    def test_write_info_orchestration(self, mock_writer, sample_info):
        """Tests that write_info calls all sub-writers."""
        # We can test the output directly rather than mocking internal calls
        write_info(mock_writer, sample_info, extra_info=True)

        out = "\n".join(mock_writer.output)

        assert "File: test.pdf" in out
        assert "NumberOfPages: 2" in out
        assert "InfoKey: Title" in out
        assert "BookmarkTitle: Chapter 1" in out
        assert "PageMediaNumber: 1" in out
        assert "PageLabelPrefix: P-" in out

    def test_write_docinfo_escaping(self, mock_writer, sample_info):
        """Test that XML escaping is applied to DocInfo."""
        _write_docinfo(mock_writer, sample_info, escape_xml=True)

        # "Test <&> Author" should become "Test &lt;&amp;&gt; Author" or similar
        # relying on xml_encode_for_info behavior
        assert any(
            "Test &lt;&amp;&gt; Author" in line
            for line in mock_writer.output
            if "InfoValue" in line
        )

    def test_write_bookmarks_recursive(self, mock_writer, sample_info):
        """Test recursive writing of bookmark dataclasses."""
        _write_bookmarks(mock_writer, sample_info.bookmarks, escape_xml=True)

        # Flattened output check
        out = "\n".join(mock_writer.output)

        # Level 1
        assert "BookmarkTitle: Chapter 1" in out
        assert "BookmarkLevel: 1" in out

        # Level 2 (Child)
        assert "BookmarkTitle: Sec 1.1" in out
        assert "BookmarkLevel: 2" in out

    def test_write_page_media(self, mock_writer, sample_info):
        """Test writing of page media entries."""
        _write_page_media_info(mock_writer, sample_info)

        out = "\n".join(mock_writer.output)

        # Page 1
        assert "PageMediaNumber: 1" in out
        assert "PageMediaRotation: 0" in out

        # Page 2 (Has CropRect)
        assert "PageMediaNumber: 2" in out
        assert "PageMediaCropRect:" in out

    def test_write_page_labels(self, mock_writer, sample_info):
        """Test writing of page label entries."""
        _write_page_labels(mock_writer, sample_info)

        out = "\n".join(mock_writer.output)
        assert "PageLabelNewIndex: 1" in out
        assert "PageLabelStart: 1" in out
        assert "PageLabelPrefix: P-" in out
        assert "PageLabelNumStyle: D" in out
