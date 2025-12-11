from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.commands.update_info import update_info
from pdftl.exceptions import UserCommandLineError


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
    # If xml_strings is False, the decoder is lambda x: x
    # We can verify this by passing a string that WOULD change if decoded
    # e.g., "&#x41;" (A). If passed raw, it remains "&#x41;".

    # We mock parse_dump_data to check what decoder it receives
    with patch("pdftl.commands.update_info.parse_dump_data") as mock_parse:
        mock_parse.return_value = {}  # Return empty dict to satisfy function

        with patch("builtins.open", new_callable=MagicMock):
            update_info(pdf, ["meta.txt"], None, xml_strings=False)

            # Check the decoder passed to parse_dump_data
            decoder = mock_parse.call_args[0][1]
            assert decoder("&lt;") == "&lt;"  # Should NOT decode

            # Compare with True case
            update_info(pdf, ["meta.txt"], None, xml_strings=True)
            decoder_true = mock_parse.call_args[0][1]
            # Verify decoder_true is actually xml_decode_for_info
            # (assuming xml_decode_for_info("&lt;") == "<")
            # We can just assert they are different functions
            assert decoder != decoder_true
