import inspect
from unittest.mock import MagicMock, patch

import pytest

import pdftl.core.constants as c
from pdftl import api
from pdftl.core.types import OpResult


def test_map_positional_args_single_input():
    """Test mapping positional args for single-input command."""
    op_name = "test_single"
    # Registry config: Takes 1 INPUT_PDF
    op_data = {"args": ([c.INPUT_PDF], {})}

    with patch("pdftl.core.executor.registry.operations", {op_name: op_data}):
        inputs, op_args = api._map_positional_args(op_name, ["in.pdf", "extra_arg"])

        # Should consume 1 input, leave rest as op_args
        assert inputs == ["in.pdf"]
        assert op_args == ["extra_arg"]


def test_map_positional_args_multi_input():
    """Test mapping positional args for multi-input command."""
    op_name = "test_multi"
    # Registry config: Takes INPUTS list
    op_data = {"args": ([c.INPUTS], {})}

    with patch("pdftl.core.executor.registry.operations", {op_name: op_data}):
        inputs, op_args = api._map_positional_args(op_name, ["a.pdf", "b.pdf", "c.pdf"])

        # Should consume ALL args as inputs
        assert inputs == ["a.pdf", "b.pdf", "c.pdf"]
        assert op_args == []


def test_map_positional_args_operation_args():
    """Test mapping positional args for command taking only arguments."""
    op_name = "test_args"
    # Registry config: Takes OPERATION_ARGS
    op_data = {"args": ([c.OPERATION_ARGS], {})}

    with patch("pdftl.core.executor.registry.operations", {op_name: op_data}):
        inputs, op_args = api._map_positional_args(op_name, ["arg1", "arg2"])

        # Should consume ALL args as op_args
        assert inputs == []
        assert op_args == ["arg1", "arg2"]


def test_run_cli_hook_success():
    """Test running CLI hook via API call."""
    op_name = "test_hook"
    mock_hook = MagicMock()
    op_data = {"cli_hook": mock_hook, "function": MagicMock()}

    mock_run = MagicMock(return_value=OpResult(success=True))

    with patch("pdftl.core.executor.registry.operations", {op_name: op_data}):
        with patch("pdftl.core.executor.run_operation", mock_run):
            # Pass run_cli_hook=True and output="out.pdf"
            api.call(op_name, run_cli_hook=True, output="out.pdf")

            mock_hook.assert_called_once()
            # Verify the mock stage passed to hook has the options
            args, _ = mock_hook.call_args
            # args[0] is result, args[1] is stage
            assert args[1].options.get("output") == "out.pdf"


def test_run_cli_hook_missing_error():
    """Test error when run_cli_hook=True but no hook exists."""
    op_name = "test_no_hook"
    op_data = {}  # No cli_hook
    mock_run = MagicMock(return_value=OpResult(success=True))

    with patch("pdftl.core.executor.registry.operations", {op_name: op_data}):
        with patch("pdftl.core.executor.run_operation", mock_run):
            with pytest.raises(ValueError, match="does not support run_cli_hook"):
                api.call(op_name, run_cli_hook=True)


def test_create_signature_exception_fallback():
    """Test fallback when inspect.signature fails (Lines 305-307)."""
    op_name = "test_sig_fail"
    mock_func = MagicMock()

    op_data = {"function": mock_func}

    with patch("pdftl.core.executor.registry.operations", {op_name: op_data}):
        # Force inspect.signature to raise TypeError
        with patch("inspect.signature", side_effect=TypeError("Bad sig")):
            sig = api._create_signature(op_name)
            # Should return signature with empty return annotation
            assert sig.return_annotation is inspect.Signature.empty
