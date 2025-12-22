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
