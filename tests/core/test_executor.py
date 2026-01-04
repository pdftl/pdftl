# tests/core/test_executor_hardening.py
from unittest.mock import MagicMock, patch

import pytest

import pdftl.core.constants as c
from pdftl.core.executor import _resolve_arguments, run_operation


def test_executor_floor_defaults():
    """Verify the 'Floor'. Empty context returns defaults, not KeyError."""
    arg_style = ([c.INPUT_PDF, c.INPUT_FILENAME], {c.ALIASES: c.ALIASES}, {})
    context = {}
    pos, kw = _resolve_arguments(arg_style, context)
    assert pos == [None, None]
    assert kw == {c.ALIASES: {}}


def test_executor_strict_indexing_typo():
    """Verify 'Strict Indexing' against registry-constant mismatches."""
    TYPO_CONSTANT = "this_key_does_not_exist_in_floor"
    arg_style = ([TYPO_CONSTANT], {}, {})
    with pytest.raises(KeyError):
        _resolve_arguments(arg_style, {})


def test_executor_successful_mapping():
    """Verify data delivery overrides floor."""
    arg_style = ([c.INPUT_PDF], {"output": c.OUTPUT}, {})
    mock_pdf = MagicMock()
    context = {c.INPUT_PDF: mock_pdf, c.OUTPUT: "final.pdf"}
    pos, kw = _resolve_arguments(arg_style, context)
    assert pos == [mock_pdf]
    assert kw == {"output": "final.pdf"}


def test_executor_full_run():
    """Integration test of run_operation using a mock registry."""
    mock_func = MagicMock(return_value="Success")

    class MockOp:
        def __init__(self):
            self.function = mock_func
            self.args = ([c.INPUT_FILENAME], {}, {})

    with patch("pdftl.core.executor.registry") as mock_registry:
        # We must mock .operations as a dict to satisfy the 'in' check in the executor
        mock_registry.operations = {"test_op": MockOp()}

        result = run_operation("test_op", {c.INPUT_FILENAME: "test.pdf"})

        assert result == "Success"
        mock_func.assert_called_once_with("test.pdf")


# tests/core/test_executor_coverage.py

import logging


from pdftl.core.registry import registry


def test_run_operation_unregistered():
    """
    Covers line 23: raise ValueError(f"Operation '{operation_name}' is not registered.")
    """
    with pytest.raises(ValueError, match="is not registered"):
        run_operation("non_existent_op", {})


def test_run_operation_malformed_entry(monkeypatch):
    """
    Covers line 29: raise ValueError(... is missing function or arg_style)
    """
    # Inject a bad registry entry
    monkeypatch.setitem(registry.operations, "bad_op", {"desc": "Missing stuff"})

    with pytest.raises(ValueError, match="missing function or arg_style"):
        run_operation("bad_op", {})


def test_run_operation_internal_error_logging(monkeypatch, caplog):
    """
    Covers line 40: logger.error("Internal error in operation '%s': %s", ...)
    """

    # 1. Define a dummy function that raises a generic Exception (not UserCommandLineError)
    def crashing_func(*args, **kwargs):
        raise RuntimeError("Something exploded")

    # 2. Register it temporarily
    op_data = {"function": crashing_func, "args": ([], {}, {})}  # Empty arg spec
    monkeypatch.setitem(registry.operations, "crashing_op", op_data)

    # 3. Run and verify it logs the error before re-raising
    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="Something exploded"):
            run_operation("crashing_op", {})

    assert "Internal error in operation 'crashing_op'" in caplog.text
