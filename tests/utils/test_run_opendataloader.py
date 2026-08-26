# tests/utils/test_run_opendataloader.py
import builtins
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

from pdftl.utils.run_opendataloader import _get_converter, run_opendataloader_extraction


def test_get_converter_direct(monkeypatch):
    """Directly test _get_converter helper execution (lines 12-14)."""
    mock_module = MagicMock()
    monkeypatch.setitem(sys.modules, "opendataloader_pdf", mock_module)
    assert _get_converter() == mock_module


def test_run_opendataloader_success(monkeypatch, caplog):
    """Test successful conversion when JSON output is generated."""
    mock_pdf = MagicMock()
    mock_converter = MagicMock()
    expected_data = {"pages": [{"text": "Sample content"}]}

    def mock_convert(input_path, output_dir, format, quiet):
        # Simulate writing input.json in the platform temporary directory
        json_path = os.path.join(output_dir, "input.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(expected_data, f)

    mock_converter.convert.side_effect = mock_convert
    monkeypatch.setitem(sys.modules, "opendataloader_pdf", mock_converter)

    with caplog.at_level("DEBUG"):
        result = run_opendataloader_extraction(mock_pdf)

    assert result == expected_data
    mock_pdf.save.assert_called_once()

    # Validate cross-platform path structure
    saved_pdf_path = mock_pdf.save.call_args[0][0]
    assert saved_pdf_path.endswith(os.path.join("", "input.pdf"))

    mock_converter.convert.assert_called_once_with(
        input_path=saved_pdf_path,
        output_dir=os.path.dirname(saved_pdf_path),
        format="json",
        quiet=True,
    )


def test_run_opendataloader_missing_output_json(monkeypatch, caplog):
    """Test branch where conversion executes but input.json is missing."""
    mock_pdf = MagicMock()
    mock_converter = MagicMock()

    monkeypatch.setitem(sys.modules, "opendataloader_pdf", mock_converter)

    with caplog.at_level("DEBUG"):
        result = run_opendataloader_extraction(mock_pdf)

    assert result is None
    assert "Conversion failed: output JSON not found" in caplog.text


@pytest.mark.parametrize("exception_cls", [ImportError, ModuleNotFoundError])
def test_run_opendataloader_import_failure(monkeypatch, exception_cls, caplog):
    """Test import failure branch without leaving residual sys.modules state."""
    mock_pdf = MagicMock()

    monkeypatch.delitem(sys.modules, "opendataloader_pdf", raising=False)

    orig_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "opendataloader_pdf":
            raise exception_cls("Module missing")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    with caplog.at_level("ERROR"):
        with pytest.raises(exception_cls):
            run_opendataloader_extraction(mock_pdf)

    assert "opendataloader_pdf is not installed." in caplog.text
