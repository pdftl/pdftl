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
