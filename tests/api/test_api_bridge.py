# tests/api/test_api_bridge.py
from unittest.mock import MagicMock, patch

import pikepdf
import pytest

import pdftl.api as api
import pdftl.core.constants as c


def test_api_discovery():
    """Verify that operations in the registry are accessible via the api module."""
    assert hasattr(api, "cat")
    assert callable(api.cat)


def test_api_attribute_error():
    """Verify that non-existent operations raise AttributeError."""
    with pytest.raises(AttributeError):
        _ = api.non_existent_operation


# We patch run_operation at the SOURCE (pdftl.core.executor) to ensure
# we intercept the call before any command logic runs.
@patch("pdftl.core.executor.run_operation")
@patch("pikepdf.open")
def test_api_call_routing(mock_open, mock_run):
    """Verify that calling an api function handles IO and routes to executor."""
    mock_run.return_value = "success"

    # Mock the return value of pikepdf.open so api.py doesn't crash
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.filename = "test.pdf"
    mock_pdf.pages = [MagicMock()] * 10

    mock_open.return_value = mock_pdf

    # Simulate a user calling api.cat
    # Because run_operation is mocked, the actual 'cat' command logic (and copy_foreign) is never touched.
    api.cat(inputs=["test.pdf"], operation_args=["1-5"])

    # 1. Verify IO happened
    mock_open.assert_called_once_with("test.pdf")

    # 2. Verify Executor received the correct Context
    args, _ = mock_run.call_args
    context = args[1]

    assert context[c.INPUTS] == ["test.pdf"]
    assert context[c.OPENED_PDFS][0] == mock_pdf
    assert context[c.OPERATION_ARGS] == ["1-5"]


@patch("pdftl.core.executor.run_operation")
@patch("pikepdf.open")
def test_explicit_call_method(mock_open, mock_run):
    """Verify the api.call helper works for dynamic names."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.filename = "a.pdf"
    mock_pdf.pages = [MagicMock()] * 10
    mock_open.return_value = mock_pdf
    mock_run.return_value = "success"

    api.call("cat", inputs=["a.pdf"])

    mock_open.assert_called_once_with("a.pdf")

    args, _ = mock_run.call_args
    context = args[1]

    assert context[c.INPUTS] == ["a.pdf"]
    assert context[c.OPENED_PDFS][0] == mock_pdf
