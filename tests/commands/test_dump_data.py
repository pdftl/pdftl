import json
from unittest.mock import MagicMock, mock_open, patch

import pytest

import pdftl.core.constants as c
from pdftl.core.types import OpResult
from pdftl.exceptions import InvalidArgumentError
from pdftl.info.info_types import PdfInfo
from pdftl.operations.dump_data import dump_data_cli_hook, pdf_info


def test_dump_data_cli_hook_json(tmp_path):
    """Test dump_data_cli_hook with JSON output enabled."""
    output_file = tmp_path / "out.json"

    # Mock data with a to_dict method (like PdfInfo)
    mock_data = MagicMock()
    mock_data.to_dict.return_value = {"key": "value"}

    res = OpResult(
        success=True,
        data=mock_data,
        meta={
            c.META_OUTPUT_FILE: str(output_file),
            c.META_JSON_OUTPUT: True,  # <--- Triggers the JSON branch
        },
    )

    dump_data_cli_hook(res, None)

    with open(output_file) as f:
        content = json.load(f)
    assert content == {"key": "value"}


def test_dump_data_cli_hook_missing_meta():
    """Test that hook raises AttributeError if meta is None."""
    res = OpResult(success=True, meta=None)
    with pytest.raises(AttributeError, match="No result metadata"):
        dump_data_cli_hook(res, None)


def test_pdf_info_argument_validation():
    """Test invalid arguments passed to pdf_info."""
    mock_pdf = MagicMock()

    # Case 1: Too many arguments
    with pytest.raises(InvalidArgumentError, match="Too many arguments"):
        pdf_info("dump_data", mock_pdf, "in.pdf", ["json", "extra"])

    # Case 2: Invalid argument (not 'json')
    with pytest.raises(InvalidArgumentError, match="Only valid argument is 'json'"):
        pdf_info("dump_data", mock_pdf, "in.pdf", ["xml"])
