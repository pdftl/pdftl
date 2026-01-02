from unittest.mock import MagicMock, patch

import pytest

from pdftl import api
from pdftl.core import constants as c
from pdftl.core.types import OpResult


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

    def mock_hook(res, stage):
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
