import io
from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl import api
from pdftl.api import _normalize_inputs
from pdftl.core import constants as c
from pdftl.core.core_types import OpResult


def test_map_positional_args_empty_break():
    """Hits line 101: break when args_queue is empty."""
    # Setup registry with an operation that expects args
    with patch(
        "pdftl.core.executor.registry.operations",
        {"test_op": {"args": ([c.INPUT_PDF, c.OPERATION_ARGS], {})}},
    ):
        # Provide only 1 arg even though registry lists 2 types of params
        # This will exhaust args_queue and hit the 'break' at line 101
        inputs, op_args = api._map_positional_args("test_op", ["file1.pdf"])
        assert inputs == ["file1.pdf"]
        assert op_args == []


def test_call_with_positional_args():
    """Hits lines 145-147: positional arguments handling in call()."""
    mock_result = OpResult(success=True, data="ok")
    mock_pdf = MagicMock()

    with patch(
        "pdftl.core.executor.registry.operations", {"test_op": {"args": ([c.INPUT_PDF], {})}}
    ):
        with patch("pdftl.core.executor.run_operation", return_value=mock_result):
            # Mock pikepdf.open to avoid FileNotFoundError
            with patch("pikepdf.open", return_value=mock_pdf):
                # Pass "input.pdf" as a positional arg (*args)
                # This triggers the _map_positional_args call and extension of raw_inputs
                result = api.call("test_op", "input.pdf")
                assert result == "ok"


def test_run_cli_hook_object_fallback():
    """Hits line 220: getattr fallback for non-dict op_data."""
    mock_result = OpResult(success=True, data="ok")

    # Create a mock hook
    hook_called = False

    def mock_hook(res, stage, mgr):
        nonlocal hook_called
        hook_called = True

    # Create an object (not a dict) to trigger the 'else' at line 219
    class OpObject:
        def __init__(self):
            self.cli_hook = mock_hook
            self.function = lambda: None

    op_instance = OpObject()

    with patch("pdftl.core.executor.registry.operations", {"test_op": op_instance}):
        with patch("pdftl.core.executor.run_operation", return_value=mock_result):
            # run_cli_hook=True forces the hook execution logic
            api.call("test_op", run_cli_hook=True)
            assert hook_called is True


def test_call_unsuccessful_operation():
    """Hits line 209: raise OperationError on failed result."""
    from pdftl.exceptions import OperationError

    mock_result = OpResult(success=False, summary="Failed specifically")

    with patch("pdftl.core.executor.run_operation", return_value=mock_result):
        with pytest.raises(OperationError, match="Failed specifically"):
            api.call("some_op")


def test_normalize_inputs_with_list(temp_pdf):
    """Hits line 44: user_opened is a list."""
    # We pass a list of open PDF objects
    opened_list = [temp_pdf]
    inputs, opened_dict = api._normalize_inputs(
        user_inputs=None, user_opened=opened_list, password=None
    )

    assert opened_dict[0] == temp_pdf
    assert inputs == ["<obj-0>"]


def test_process_bytes_input():
    """Hits lines 71-72: input item is bytes."""
    # Create a tiny valid PDF in memory
    buf = io.BytesIO()
    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        pdf.save(buf)
    pdf_bytes = buf.getvalue()

    # Call _process_user_input directly or via call()
    final_inputs, final_opened = api._process_user_input(
        i=0, item=pdf_bytes, password=None, final_inputs=[], final_opened={}
    )

    assert final_inputs == ["<bytes-obj-0>"]
    assert isinstance(final_opened[0], pikepdf.Pdf)


@patch("pdftl.api._process_user_input")
def test_normalize_inputs_closes_opened_pdfs_on_exception(mock_process):
    mock_pdf_1 = MagicMock()

    def mock_process_side_effect(i, item, password, final_inputs, final_opened):
        if i == 0:
            # FIX: Just return a new list containing the mock PDF directly
            return final_inputs + [item], {0: mock_pdf_1}
        else:
            raise ValueError("Simulated corruption on file 2")

    mock_process.side_effect = mock_process_side_effect

    with pytest.raises(ValueError, match="Simulated corruption on file 2"):
        _normalize_inputs(["file1.pdf", "file2.pdf"], [], "")

    mock_pdf_1.close.assert_called_once()
