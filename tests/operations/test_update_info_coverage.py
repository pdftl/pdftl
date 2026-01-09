from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.exceptions import UserCommandLineError
from pdftl.operations.update_info import update_info


@pytest.fixture
def pdf():
    return pikepdf.new()


def test_update_info_prompt(pdf):
    """Test PROMPT argument (Line 124)."""
    # Mock input to return a dummy filename (which we will also mock opening)
    mock_input = lambda msg, **kwargs: "meta.txt"

    with patch("builtins.open", new_callable=MagicMock) as mock_file:
        mock_file.return_value.__enter__.return_value.readlines.return_value = []

        # Should call get_input and then open "meta.txt"
        update_info(pdf, ["PROMPT"], mock_input)

        assert mock_file.call_args[0][0] == "meta.txt"


def test_update_info_os_error(pdf):
    """Test OSError handling (Lines 141-142)."""
    with patch("builtins.open") as mock_file:
        mock_file.side_effect = OSError("Access Denied")

        with pytest.raises(UserCommandLineError):
            update_info(pdf, ["meta.txt"], None)


def test_update_info_no_xml_strings(pdf):
    """Test xml_strings=False (Line 131)."""

    # We patch 'parse_dump_data' where it is USED inside the parser module.
    # This captures the call made by update_info_parser.
    target = "pdftl.operations.parsers.update_info_parser.parse_dump_data"

    with patch(target) as mock_parse_dump:
        # Return an empty dict so PdfInfo.from_dict doesn't crash
        mock_parse_dump.return_value = {}

        # We also mock open so the file reading part doesn't fail
        with patch("builtins.open", new_callable=MagicMock):

            # 1. Run with xml_strings=False
            update_info(pdf, ["meta.txt"], None, xml_strings=False)

            # Check the decoder passed to parse_dump_data(lines, decoder)
            # args[0] is lines, args[1] is the decoder function
            decoder_false = mock_parse_dump.call_args[0][1]

            # Verify it is a passthrough (identity) function
            assert decoder_false("&lt;") == "&lt;"
            assert decoder_false("Foo") == "Foo"

            # 2. Run with xml_strings=True
            update_info(pdf, ["meta.txt"], None, xml_strings=True)

            decoder_true = mock_parse_dump.call_args[0][1]

            # Verify it decodes XML entities
            assert decoder_true("&lt;") == "<"
