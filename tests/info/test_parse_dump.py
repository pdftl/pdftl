from unittest.mock import patch

import pytest

# Import the functions directly for testing
# Note: The functions are imported with their actual names from the source module
from pdftl.info.parse_dump import (
    _handle_begin_tag,
    _handle_key_value,
    _handle_line,
    _parse_field,
    _parse_info_field,
    _parse_top_level_field,
    _reset_state,
)

# Simple decoder for testing (just returns the value)
TEST_DECODER = lambda x: x


# Use a common fixture for the initial pdf_data structure
@pytest.fixture
def pdf_data_struct():
    """Returns a clean initial pdf_data dictionary."""
    return {
        "Info": {},
        "BookmarkList": [],
        "PageMediaList": [],
        "PageLabelList": [],
    }


@pytest.fixture
def clean_state():
    """Returns a clean parser state."""
    return _reset_state()


class TestParseDumpCoverage:
    def test_handle_line_skip_empty_line(self, pdf_data_struct, clean_state):
        """Covers line 60: return when line is empty/whitespace only."""

        # Set a dummy value to check if it's preserved
        _handle_line("PdfID0: test_id", pdf_data_struct, clean_state, TEST_DECODER)
        initial_data = pdf_data_struct.copy()
        initial_state = clean_state.copy()

        # Call with an empty line
        _handle_line("   ", pdf_data_struct, clean_state, TEST_DECODER)

        # Assert no change in data or state, proving line 60 was hit
        assert pdf_data_struct == initial_data
        assert clean_state == initial_state

    def test_handle_line_parsing_error_warning(self, pdf_data_struct, clean_state, caplog):
        """Covers line 84: logging.warning for unhandled line format (no ':' and not 'Begin')."""
        line = "This is a malformed line"

        with caplog.at_level("WARNING"):
            _handle_line(line, pdf_data_struct, clean_state, TEST_DECODER)

        # Check that line 84 was hit and logged the warning
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert (
            "does not end in 'Begin'" in record.message
            and "This is a malformed line" in record.message
        )

        # Test with a bytes line to cover decode path in _handle_line
        line_bytes = b"Another malformed line"
        caplog.clear()
        with caplog.at_level("WARNING"):
            _handle_line(line_bytes, pdf_data_struct, clean_state, TEST_DECODER)
        expected = (
            "Parsing error for 'update_data': line '%s' does not end in 'Begin'"
            % "Another malformed line"
        )
        assert [rec.message for rec in caplog.records] == [expected]

    def test_handle_begin_tag_unknown_tag(self, pdf_data_struct, clean_state, caplog):
        """Covers lines 99-100: logging.warning and _reset_state for unknown Begin tag."""
        # Setup: initial state is None
        initial_state = clean_state.copy()

        # Call with an unknown tag
        with caplog.at_level("WARNING"):
            _handle_begin_tag("UnknownTag", pdf_data_struct, initial_state, TEST_DECODER)

        # 1. Check line 99: warning logged
        expected = "Unknown Begin tag '%s' in metadata. Ignoring." % "UnknownTag"
        assert [rec.message for rec in caplog.records] == [expected]

        # 2. Check line 100: state reset to None
        assert initial_state["current_type"] is None
        assert initial_state["current_value"] is None
        assert initial_state["last_info_key"] is None

    def test_handle_key_value_prefix_mismatch_warning(self, pdf_data_struct, clean_state, caplog):
        """
        Covers lines 111-118: logging.warning and return when key doesn't start
        with the expected current_type prefix (e.g., PageMedia but key is Title).
        """
        # 1. Simulate 'PageMediaBegin' was just processed, creating a new record
        _handle_begin_tag("PageMedia", pdf_data_struct, clean_state, TEST_DECODER)

        # Use a key that is valid in another block but not prefixed correctly
        key = "Title"
        value = "Some Title"

        # Ensure the record starts empty
        assert pdf_data_struct["PageMediaList"] == [{}]

        with caplog.at_level("WARNING"):
            _handle_key_value(key, value, pdf_data_struct, clean_state, TEST_DECODER)

        # 1. Check lines 111-117: warning logged
        expected = (
            "While parsing metadata: key '%s' in %sBegin block"
            " should start with '%s'. Ignoring this line."
        ) % (key, "PageMedia", "PageMedia")
        assert [rec.message for rec in caplog.records] == [expected]

        # 2. Check line 118: return, meaning the current PageMedia record is still empty/unchanged.
        assert pdf_data_struct["PageMediaList"] == [{}]

    @patch("pdftl.info.parse_dump._parse_field_decode_lookups", autospec=True)
    def test_parse_field_unknown_key_raises_value_error(
        self, mock_lookups, pdf_data_struct, clean_state
    ):
        """Covers line 173: raise ValueError in _parse_field for unknown key in a structured block."""

        # Setup mock lookups to simulate an active block that is recognized
        mock_lookups.return_value = {"Bookmark": {"Title": TEST_DECODER}}

        # Set state to simulate inside BookmarkBegin
        clean_state["current_type"] = "Bookmark"
        current_data = {}

        # The key must start with the prefix, but the short key must not be in the lookup.
        key = "BookmarkUnknownKey"
        value = "some_value"

        # Test the dispatch via _handle_key_value
        with pytest.raises(ValueError, match="Unknown key BookmarkUnknownKey in metadata"):
            _handle_key_value(key, value, pdf_data_struct, clean_state, TEST_DECODER)

        # Also directly test _parse_field to ensure line 173 is hit
        with pytest.raises(ValueError, match="Unknown key BookmarkUnknownKey in metadata"):
            _parse_field(
                key,
                value,
                current_data,
                "Bookmark",
                mock_lookups.return_value["Bookmark"],
            )

    def test_parse_info_field_unknown_key_raises_value_error(self, pdf_data_struct, clean_state):
        """Covers line 188: raise ValueError in _parse_info_field for key not InfoKey/InfoValue."""
        # _parse_info_field is called only if the key is 'InfoKey' or 'InfoValue'.
        # We must call _parse_info_field directly with an invalid key to hit line 188.

        info_dict = pdf_data_struct["Info"]

        with pytest.raises(
            ValueError,
            match="Unknown Info field key 'BadKey' in metadata. This is a bug.",
        ):
            _parse_info_field("BadKey", "some_value", info_dict, clean_state, TEST_DECODER)

    def test_parse_top_level_field_unknown_key_raises_value_error(
        self, pdf_data_struct, clean_state
    ):
        """Covers line 198: raise ValueError in _parse_top_level_field for unknown key."""

        # Ensure state is reset (not inside Info or a List) so that _handle_key_value
        # delegates to _parse_top_level_field.
        clean_state = _reset_state(clean_state, None)

        # Key that is not PdfID0, PdfID1, or NumberOfPages
        key = "UnknownTopLevelKey"
        value = "some_value"

        # Test the dispatch via _handle_key_value
        with pytest.raises(ValueError, match="Unknown key UnknownTopLevelKey in metadata"):
            _handle_key_value(key, value, pdf_data_struct, clean_state, TEST_DECODER)

        # Also directly test _parse_top_level_field to ensure line 198 is hit
        with pytest.raises(ValueError, match="Unknown key AnotherBadKey in metadata"):
            _parse_top_level_field("AnotherBadKey", value, pdf_data_struct, TEST_DECODER)


from unittest.mock import MagicMock, patch

import pikepdf
import pytest
from pikepdf import (
    Name,
    String,
)

# --- Import Functions to Test ---
from pdftl.info.parse_dump import (
    _safe_float_list,
    _safe_int,
    parse_dump_data,
)

# --- Import Modules to Test ---


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
