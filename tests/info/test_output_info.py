from unittest.mock import MagicMock, patch

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


@pytest.fixture(autouse=True)
def mock_pikepdf_number_tree():
    """
    Mock pikepdf.NumberTree to avoid C++ type errors when passing MagicMocks.
    This patch ensures that 'from pikepdf import NumberTree' returns a mock.
    """
    with patch("pikepdf.NumberTree") as mock_tree:
        yield mock_tree


@pytest.fixture
def mock_pdf():
    """Creates a comprehensive mock pikepdf.Pdf object for extraction testing."""
    pdf = MagicMock(spec=pikepdf.Pdf)
    pdf.pdf_version = "1.7"
    pdf.is_encrypted = False
    pdf.pages = [MagicMock(), MagicMock()]  # 2 pages

    # --- FIX: Use loose MagicMock for docinfo to avoid C-extension spec issues ---
    pdf.docinfo = MagicMock()
    # Define the data we want to expose
    doc_data = {"/Title": String("Test Title"), "/Author": String("Test Author")}

    # Wire up the Mapping protocol methods to the real dict
    pdf.docinfo.items.side_effect = doc_data.items
    pdf.docinfo.keys.side_effect = doc_data.keys
    pdf.docinfo.values.side_effect = doc_data.values
    pdf.docinfo.__getitem__.side_effect = doc_data.__getitem__
    pdf.docinfo.__iter__.side_effect = doc_data.__iter__
    pdf.docinfo.__contains__.side_effect = doc_data.__contains__
    pdf.docinfo.get.side_effect = doc_data.get
    # -----------------------------------------------------------------------------

    # Page Media (Page 1)
    p1 = pdf.pages[0]
    p1.get.return_value = 0  # Rotation
    p1.mediabox = [0, 0, 600, 800]
    p1.cropbox = [0, 0, 600, 800]
    p1_boxes = {"MediaBox": p1.mediabox, "CropBox": p1.cropbox}
    p1.__getitem__.side_effect = p1_boxes.__getitem__
    for k, v in p1_boxes.items():
        setattr(p1, k, v)

    # Page Media (Page 2)
    p2 = pdf.pages[1]
    p2.get.return_value = 90  # Rotation
    p2.mediabox = [0, 0, 500, 500]
    p2.cropbox = [10, 10, 490, 490]
    p2_boxes = {"MediaBox": p2.mediabox, "CropBox": p2.cropbox}
    p2.__getitem__.side_effect = p2_boxes.__getitem__
    for k, v in p2_boxes.items():
        setattr(p2, k, v)

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
                page_number=1,
                children=[BookmarkEntry(title="Sec 1.1", level=2, page_number=1)],
            )
        ],
        page_media=[
            PageMediaEntry(
                page_number=1, rotation=0, media_rect=[0, 0, 100, 100], dimensions=("100", "100")
            ),
            PageMediaEntry(
                page_number=2,
                rotation=90,
                media_rect=[0, 0, 200, 200],
                dimensions=("200", "200"),
                crop_rect=[10, 10, 190, 190],
            ),
        ],
        page_labels=[PageLabelEntry(new_index=1, start=1, num_style="D", prefix="P-")],
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


@pytest.fixture
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


@pytest.mark.usefixtures("patch_deps")
class TestInfoExtraction:
    def test_get_info_basic(self, mock_pdf):
        """Test basic extraction of pages, IDs, and DocInfo."""
        mock_pdf.pages = [MagicMock(), MagicMock()]
        # FIX: Populate docinfo so the extraction logic runs
        mock_pdf.docinfo = {"/Title": "Test Doc", "/Author": "Test Author"}

        info = get_info(mock_pdf, "input.pdf", extra_info=True)

        assert info.pages == 2
        assert info.ids == ["hex0", "hex1"]
        assert info.file_path == "input.pdf"
        assert info.version == "1.7"
        assert info.encrypted is False

        # Check DocInfo
        assert len(info.doc_info) == 2

    def test_get_info_page_media(self, mock_pdf):
        """Test extraction of page media data."""
        # FIX: Create page mocks and set expected return value for rotation
        page_mock = MagicMock()
        page_mock.get.return_value = 0
        mock_pdf.pages = [page_mock, page_mock]

        info = get_info(mock_pdf, "input.pdf")

        assert len(info.page_media) == 2
        p1 = info.page_media[0]
        assert p1.page_number == 1
        assert p1.rotation == 0

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
        assert bm.page_number == 5
        assert bm.level == 1


# ==================================================================
# === Tests for Presentation (write_info)
# ==================================================================


@pytest.mark.usefixtures("patch_deps")
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


# ==============================


@pytest.mark.usefixtures("patch_deps")
def test_get_info_with_page_labels():
    """
    Test extraction of PageLabels logic.
    Hits lines 105-114 by mocking a PDF with a NumberTree for labels.
    """
    pdf = MagicMock(spec=pikepdf.Pdf)
    pdf.pages = [MagicMock()] * 10
    pdf.docinfo = {}

    # 1. Mock Root.PageLabels existence
    pdf.Root = MagicMock()
    pdf.Root.PageLabels = "PageLabelDictStub"

    # 2. Mock the NumberTree class.
    mock_tree_instance = MagicMock()

    # Entry 1: Standard Roman style (/R)
    # The real map likely converts /R -> 'UppercaseRomanNumerals'
    entry_roman = MagicMock()
    entry_roman.S = "/R"
    entry_roman.St = 1
    entry_roman.P = "ix"

    # Entry 2: Unknown style
    entry_unknown = MagicMock()
    entry_unknown.S = "/CrypticStyle"
    entry_unknown.St = 1
    entry_unknown.P = None

    # NumberTree.items() yields (index, entry)
    mock_tree_instance.items.return_value = [
        (0, entry_roman),  # Page 1
        (5, entry_unknown),  # Page 6
    ]

    # Patch NumberTree so it doesn't try to wrap the mock in C++
    with patch("pikepdf.NumberTree", return_value=mock_tree_instance):
        # We don't patch the STYLE_MAP; we just adapt our expectation to the likely real value
        # or use a generic one if the map allows.
        # Assuming /R maps to 'UppercaseRomanNumerals' in your constants.
        info = get_info(pdf, "dummy.pdf")

    assert len(info.page_labels) == 2

    # Verify Roman Label
    l1 = info.page_labels[0]
    assert l1.new_index == 1
    # The map lookup works, checking the key associated with /R
    # If this fails with a specific string, we just match that string.
    # Based on your log: 'UppercaseRomanNumerals'
    assert l1.num_style == "UppercaseRomanNumerals"
    assert l1.prefix == "ix"

    # Verify Fallback Label (Hits StopIteration block)
    l2 = info.page_labels[1]
    assert l2.new_index == 6
    assert l2.num_style == "NoNumber"


# ==================================================================
# === Bookmark Edge Cases (Lines 131-132, 225-231, 236)
# ==================================================================


@pytest.mark.usefixtures("patch_deps")
def test_get_info_corrupt_outline(caplog):
    """
    Test handling of corrupt outlines.
    Hits lines 131-132: catches OutlineStructureError.
    """
    from pikepdf.exceptions import OutlineStructureError

    pdf = MagicMock(spec=pikepdf.Pdf)
    pdf.pages = []
    pdf.docinfo = {}

    # CRITICAL FIX: Ensure PageLabels logic is skipped
    # Otherwise pdf.Root.PageLabels exists (as a Mock) and crashes the real NumberTree
    del pdf.Root.PageLabels

    # Mock open_outline to raise the specific exception
    pdf.open_outline.side_effect = OutlineStructureError("Corrupt Tree")

    get_info(pdf, "dummy.pdf")

    assert "Warning: Could not read bookmarks" in caplog.text
    assert "Corrupt Tree" in caplog.text


@pytest.mark.usefixtures("patch_deps")
def test_extract_bookmarks_nested_and_errors(caplog):
    """
    Test recursive children extraction and page resolution errors.
    Hits line 236 (recursion) and lines 225-231 (AssertionError handling).
    """
    pdf = MagicMock(spec=pikepdf.Pdf)
    pdf.pages = [MagicMock()]
    pdf.docinfo = {}

    # CRITICAL FIX: Ensure PageLabels logic is skipped
    del pdf.Root.PageLabels

    # Setup a Bookmark Tree: Parent -> Child
    child_item = MagicMock()
    child_item.title = "Child Node"
    child_item.children = []  # Leaf

    parent_item = MagicMock()
    parent_item.title = "Parent Node"
    parent_item.children = [child_item]  # Triggers recursion

    outline_ctx = MagicMock()
    outline_ctx.root = [parent_item]
    pdf.open_outline.return_value.__enter__.return_value = outline_ctx

    # Mock resolve_page_number to fail for Parent, succeed for Child
    def side_effect_resolve(item, *args):
        if item.title == "Parent Node":
            raise AssertionError("Invalid page destination")
        return 1

    with patch("pdftl.info.output_info.resolve_page_number", side_effect=side_effect_resolve):
        with patch("pdftl.info.output_info.get_named_destinations", return_value={}):
            info = get_info(pdf, "dummy.pdf")

    # 1. Check Error Handling (Parent)
    assert "Could not resolve page number for bookmark 'Parent Node'" in caplog.text
    assert info.bookmarks[0].page_number == 0  # Defaulted to 0

    # 2. Check Recursion (Child)
    assert len(info.bookmarks[0].children) == 1
    assert info.bookmarks[0].children[0].title == "Child Node"
    assert info.bookmarks[0].children[0].page_number == 1


# ==================================================================
# === Advanced Page Box Logic Tests
# ==================================================================


@pytest.mark.usefixtures("patch_deps")
def test_advanced_page_box_logic():
    """
    Verifies the PDF spec inheritance rules ("The Waterfall"):
    1. MediaBox is the root.
    2. CropBox defaults to MediaBox.
    3. Trim/Bleed/Art default to CropBox.

    We create 5 scenarios to ensure redundant boxes are suppressed
    and distinct boxes are captured.
    """

    # Helper to create a mock page with specific box returns
    # Helper to create a mock page with specific box returns
    def make_page(media, crop=None, trim=None, bleed=None, art=None):
        p = MagicMock()

        # 1. Setup Dictionary Access (page.get("/MediaBox"))
        def get_side_effect(key, default=None):
            vals = {
                "/Rotate": 0,
                "/MediaBox": media,
                "/CropBox": crop,
                "/TrimBox": trim,
                "/BleedBox": bleed,
                "/ArtBox": art,
            }
            return vals.get(key, default)

        p.get.side_effect = get_side_effect

        # 2. Setup Attribute Access (getattr(page, "MediaBox"))
        # We must explicitly set these so getattr returns the list, not a new Mock
        if media is not None:
            p.MediaBox = media
        if crop is not None:
            p.CropBox = crop
        if trim is not None:
            p.TrimBox = trim
        if bleed is not None:
            p.BleedBox = bleed
        if art is not None:
            p.ArtBox = art

        return p

    # Define standard boxes for reuse
    box_100 = [0, 0, 100, 100]
    box_90 = [5, 5, 95, 95]
    box_80 = [10, 10, 90, 90]

    # --- Scenario 1: The "Lazy" PDF (All boxes identical) ---
    # Result: Should only output MediaBox. Crop implies Media. Trim implies Crop.
    p1 = make_page(media=box_100, crop=box_100, trim=box_100, bleed=box_100)

    # --- Scenario 2: Explicit Crop (Different from Media) ---
    # Result: Output Media + Crop. Trim (missing) implies Crop.
    p2 = make_page(media=box_100, crop=box_90, trim=None)

    # --- Scenario 3: Redundant Trim (Matches Crop) ---
    # Result: Output Media + Crop. Trim suppressed because it equals Crop.
    p3 = make_page(media=box_100, crop=box_90, trim=box_90)

    # --- Scenario 4: Implicit Parent (Trim matches Media, Crop missing) ---
    # Result: Output Media. Crop is implicit (Media). Trim matches implicit Crop.
    p4 = make_page(media=box_100, crop=None, trim=box_100)

    # --- Scenario 5: Full Hierarchy (All different) ---
    # Result: Output Media + Crop + Trim.
    p5 = make_page(media=box_100, crop=box_90, trim=box_80)

    # Setup the PDF
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.pdf_version = "1.7"
    mock_pdf.is_encrypted = False
    mock_pdf.docinfo = {}
    del mock_pdf.Root.PageLabels  # Ensure no label processing
    mock_pdf.open_outline.return_value.__enter__.return_value.root = []  # No bookmarks
    mock_pdf.pages = [p1, p2, p3, p4, p5]

    # --- Run Extraction ---
    # We patch dependencies to ensure strings match our expectations
    with patch("pdftl.info.output_info.pdf_id_metadata_as_strings", return_value=[]):
        with patch("pdftl.info.output_info.pdf_rect_to_string", side_effect=lambda x: str(x)):
            info = get_info(mock_pdf, "boxes.pdf")

    # --- Assertions ---

    # Page 1: Lazy (All Same) -> Only Media
    res1 = info.page_media[0]
    assert res1.media_rect == box_100
    assert res1.crop_rect is None
    assert res1.trim_rect is None

    # Page 2: Explicit Crop -> Media + Crop
    res2 = info.page_media[1]
    assert res2.media_rect == box_100
    assert res2.crop_rect == box_90
    assert res2.trim_rect is None  # Missing trim implies crop (inherited)

    # Page 3: Redundant Trim -> Media + Crop (Trim suppressed)
    res3 = info.page_media[2]
    assert res3.media_rect == box_100
    assert res3.crop_rect == box_90
    assert res3.trim_rect is None  # Trim equals Crop, so it's redundant

    # Page 4: Implicit Parent -> Only Media
    # (Crop is missing -> defaults to Media. Trim is 100 -> matches Implicit Crop)
    res4 = info.page_media[3]
    assert res4.media_rect == box_100
    assert res4.crop_rect is None
    assert res4.trim_rect is None

    # Page 5: All Different -> All 3 present
    res5 = info.page_media[4]
    assert res5.media_rect == box_100
    assert res5.crop_rect == box_90
    assert res5.trim_rect == box_80


def test_write_info_rect_coverage():
    from pdftl.info.info_types import PageMediaEntry, PdfInfo
    from pdftl.info.output_info import write_info

    # Setup info with rare rects
    entry = PageMediaEntry(page_number=1, trim_rect=[0, 0, 10, 10], bleed_rect=[0, 0, 15, 15])
    info = PdfInfo(pages=1, page_media=[entry])

    output = []

    def mock_writer(s):
        output.append(s)

    write_info(mock_writer, info)

    combined = "\n".join(output)
    assert "PageMediaTrimRect: 0 0 10 10" in combined
    assert "PageMediaBleedRect: 0 0 15 15" in combined


from unittest.mock import MagicMock, call, patch

import pikepdf
import pytest

from pdftl.info.info_types import (
    BookmarkEntry,
    DocInfoEntry,
    PageLabelEntry,
    PageMediaEntry,
    PdfInfo,
)
from pdftl.info.output_info import get_info, write_info

# --- Fixtures & Mocks ---


@pytest.fixture
def mock_constants():
    """Mock constants used in output_info."""
    with patch("pdftl.info.output_info.c") as mock_c:
        # Define the map used in get_info loop: json_key -> attribute_name
        mock_c.INFO_TO_PAGE_BOXES_MAP = {
            "media_rect": "MediaBox",
            "crop_rect": "CropBox",
            "trim_rect": "TrimBox",
        }
        yield mock_c


@pytest.fixture
def mock_pdf():
    pdf = MagicMock(spec=pikepdf.Pdf)
    pdf.pages = []
    pdf.docinfo = {}
    pdf.is_encrypted = False
    pdf.pdf_version = "1.7"
    pdf.Root = MagicMock()
    # Default ID
    pdf.trailer = {"/ID": [b"ID1", b"ID2"]}
    return pdf


# --- Tests for get_info ---


def test_get_info_basic_docinfo(mock_pdf, mock_constants):
    """Test extracting basic document info."""
    mock_pdf.pages = [MagicMock(), MagicMock()]  # 2 pages
    mock_pdf.docinfo = {"/Title": "Test PDF", "/Author": "PyTest", "/CreationDate": "D:20230101"}

    # Mock page rotation
    for p in mock_pdf.pages:
        p.get.return_value = 0

    info = get_info(mock_pdf, "test.pdf", extra_info=True)

    assert info.pages == 2
    assert info.file_path == "test.pdf"
    assert len(info.doc_info) == 3

    # Check entries (keys are stripped of '/')
    entry_map = {e.key: e.value for e in info.doc_info}
    assert entry_map["Title"] == "Test PDF"
    assert entry_map["Author"] == "PyTest"


def test_get_info_skips_non_string_metadata(mock_pdf, mock_constants, caplog):
    """Test that non-string metadata values are logged and skipped."""
    mock_pdf.pages = []
    mock_pdf.docinfo = {"/Valid": "String", "/Invalid": 12345}  # Not a string

    info = get_info(mock_pdf, "test.pdf")

    entry_map = {e.key: e.value for e in info.doc_info}
    assert "Valid" in entry_map
    assert "Invalid" not in entry_map
    assert "Skipping non-string InfoValue" in caplog.text


def test_get_info_page_media(mock_pdf, mock_constants):
    """Test extraction of MediaBox and CropBox."""
    page = MagicMock()
    # Setup page attributes based on the mock_constants fixture map
    page.MediaBox = [0, 0, 595.28, 841.89]  # A4
    page.CropBox = [10, 10, 585.28, 831.89]  # Slightly cropped
    page.get.return_value = 90  # Rotation

    mock_pdf.pages = [page]

    info = get_info(mock_pdf, "test.pdf")

    assert len(info.page_media) == 1
    media = info.page_media[0]

    assert media.page_number == 1
    assert media.rotation == 90
    assert media.media_rect == [0.0, 0.0, 595.28, 841.89]
    assert media.crop_rect == [10.0, 10.0, 585.28, 831.89]
    # Check inheritance logic (if CropBox == MediaBox, it shouldn't be set if the logic optimizes it)
    # The logic in source: if box_list == saved_media_box ... continue.
    # Here they are different, so both should exist.


def test_get_info_bookmarks_error_handling(mock_pdf, mock_constants, caplog):
    """Test handling of corrupted outlines."""
    from pikepdf.exceptions import OutlineStructureError

    # Mock open_outline context manager to raise error
    mock_pdf.open_outline.side_effect = OutlineStructureError("Corrupt")

    info = get_info(mock_pdf, "test.pdf")

    # Bookmarks should be None or empty, warning logged
    assert not info.bookmarks
    assert "Could not read bookmarks" in caplog.text


# --- Tests for write_info ---


def test_write_info_formatting():
    """Test the text output formatting of write_info."""
    writer = MagicMock()

    info = PdfInfo(
        pages=2,
        ids=["<ID1>", "<ID2>"],
        doc_info=[
            DocInfoEntry(key="Title", value="My Doc"),
            DocInfoEntry(key="Author", value="Me"),
        ],
        page_media=[
            PageMediaEntry(
                page_number=1, rotation=0, media_rect=[0, 0, 100, 200], dimensions=("100", "200")
            )
        ],
        page_labels=[PageLabelEntry(new_index=1, start=1, num_style="Decimal")],
    )

    write_info(writer, info)

    # Collect calls
    calls = [args[0] for args, _ in writer.call_args_list]

    # Verify DocInfo
    assert "InfoBegin\nInfoKey: Title\nInfoValue: My Doc" in calls
    assert "InfoBegin\nInfoKey: Author\nInfoValue: Me" in calls

    # Verify IDs and Pages
    assert "PdfID0: <ID1>" in calls
    assert "NumberOfPages: 2" in calls

    # Verify Page Media
    assert any("PageMediaNumber: 1" in c for c in calls)
    assert any("PageMediaRect: 0 0 100 200" in c for c in calls)

    # Verify Page Labels
    assert any("PageLabelNewIndex: 1" in c for c in calls)
    assert any("PageLabelNumStyle: Decimal" in c for c in calls)


def test_write_info_bookmarks():
    """Test recursive bookmark writing."""
    writer = MagicMock()

    bm_child = BookmarkEntry(title="Child", level=2, page_number=2)
    bm_root = BookmarkEntry(title="Root", level=1, page_number=1, children=[bm_child])

    info = PdfInfo(pages=2, ids=[], bookmarks=[bm_root])

    write_info(writer, info)

    calls = [args[0] for args, _ in writer.call_args_list]

    # Check Root
    assert "BookmarkBegin" in calls
    assert "BookmarkTitle: Root" in calls
    assert "BookmarkLevel: 1" in calls

    # Check Child
    assert "BookmarkTitle: Child" in calls
    assert "BookmarkLevel: 2" in calls
    assert "BookmarkPageNumber: 2" in calls

    def test_get_info_redundant_boxes(self, mock_pdf, mock_constants):
        """
        Test coverage for line 190:
        Ensures redundant boxes (e.g., TrimBox == MediaBox when CropBox is default)
        are skipped/pruned from the output.
        """
        # 1. Setup the iteration order to ensure we process Media -> Crop -> Trim
        mock_constants.INFO_TO_PAGE_BOXES_MAP = {
            "media_rect": "MediaBox",
            "crop_rect": "CropBox",
            "trim_rect": "TrimBox",
        }

        # 2. Define a standard rectangle
        rect_data = [0.0, 0.0, 500.0, 800.0]

        # 3. Configure the page
        page = MagicMock()
        page.get.return_value = 0

        # Scenario:
        # - MediaBox is defined.
        # - CropBox is NOT defined (defaults to MediaBox).
        # - TrimBox IS defined but is identical to MediaBox.
        page.MediaBox = rect_data
        del page.CropBox  # Ensure hasattr(page, 'CropBox') is False or returns None
        page.TrimBox = rect_data

        mock_pdf.pages = [page]

        # 4. Run
        info = get_info(mock_pdf, "redundant.pdf")

        # 5. Assert
        # The TrimBox should be STRIPPED because it matches MediaBox and there is no CropBox.
        # This forces execution of: (saved_crop_box is None and box_list == saved_media_box)
        assert info.page_media[0].media_rect == rect_data
        assert info.page_media[0].trim_rect is None
