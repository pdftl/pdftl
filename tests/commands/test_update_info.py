from unittest.mock import Mock, patch

import pytest

from pdftl.exceptions import InvalidArgumentError
from pdftl.info.info_types import PdfInfo
from pdftl.operations.update_info import update_info

# Mock objects
mock_pdf = Mock()
mock_get_input = Mock()


def test_update_info_invalid_args():
    """Test extra arguments error (Line 132)."""
    with pytest.raises(InvalidArgumentError, match="Unexpected argument"):
        update_info(mock_pdf, ["meta.txt", "extra_arg"], mock_get_input)


def test_update_info_stdin_flag():
    """Test passing '-' converts to None (Line 144)."""
    # We mock resolve_operation_spec so we don't actually try to read from stdin
    with patch("pdftl.operations.update_info.resolve_operation_spec") as mock_resolve:
        mock_resolve.return_value = PdfInfo()  # Return a dummy result
        with patch("pdftl.operations.update_info.execute_update_info"):
            update_info(mock_pdf, ["-"], mock_get_input)

        # Check that resolve was called with None, not "-"
        args, _ = mock_resolve.call_args
        assert args[0] == [None]


def test_update_info_json_file_legacy_decoding(tmp_path):
    """
    Test loading a JSON file via @file logic with legacy update_info.
    This hits Lines 161-165 (decoding XML entities).
    """
    # Create a dummy json file
    json_file = tmp_path / "meta.json"
    # Note: &#9731; is the snowman emoji ☃
    json_file.write_text('{"Info": {"Title": "Snowman &#9731;"}}', encoding="utf-8")

    # We can't easily mock resolve_operation_spec to simulate the @file loading
    # perfectly without replicating its internal logic, so we will Integration Test
    # this by actually running update_info with the mock_pdf.

    # We need to rely on resolve_operation_spec actually loading the json
    # because we passed model_class=None.

    with patch("pdftl.operations.update_info.execute_update_info") as mock_exec:
        # 1. Test update_info (legacy) -> Should Decode
        # We simulate the user typing '@path/to/meta.json'
        # But since we are calling the python function directly, resolve_operation_spec
        # handles the list arguments.

        # However, resolve_operation_spec relies on arg_helpers logic.
        # To strictly unit test the logic inside update_info.py lines 161+,
        # it is easier to Mock resolve_operation_spec to return a dict.

        with patch("pdftl.operations.update_info.resolve_operation_spec") as mock_resolve:
            # Simulate resolve returning a RAW DICT (as if loaded from JSON)
            mock_resolve.return_value = {"Info": {"Title": "Snowman &#9731;"}}

            # Run with xml_strings=True (default for update_info)
            update_info(mock_pdf, ["dummy"], mock_get_input, xml_strings=True)

            # Verify the result passed to execute was decoded
            spec_arg = mock_exec.call_args[0][1]
            assert isinstance(spec_arg, PdfInfo)
            assert spec_arg.doc_info[0].value == "Snowman ☃"


def test_update_info_json_file_utf8_no_decoding():
    """
    Test loading a JSON file via @file logic with update_info_utf8.
    This checks that we skip decoding (Line 164 checks xml_strings).
    """
    with patch("pdftl.operations.update_info.execute_update_info") as mock_exec:
        with patch("pdftl.operations.update_info.resolve_operation_spec") as mock_resolve:
            # Raw dict input
            mock_resolve.return_value = {"Info": {"Title": "Raw &#9731;"}}

            # Run with xml_strings=False
            update_info(mock_pdf, ["dummy"], mock_get_input, xml_strings=False)

            spec_arg = mock_exec.call_args[0][1]
            # Should REMAIN encoded/raw
            assert spec_arg.doc_info[0].value == "Raw &#9731;"


def test_update_info_type_error():
    """Test unexpected result type from resolve (Line 169)."""
    with patch("pdftl.operations.update_info.resolve_operation_spec") as mock_resolve:
        mock_resolve.return_value = 12345  # Not PdfInfo, Not dict

        # Expect TypeError directly, not UserCommandLineError
        with pytest.raises(TypeError, match="Unexpected result type"):
            update_info(mock_pdf, ["dummy"], mock_get_input)
