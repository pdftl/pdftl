# tests/core/test_executor_hardening.py
from unittest.mock import MagicMock, patch

import pytest

import pdftl.core.constants as c
from pdftl.core.executor import _resolve_arguments, run_operation


def test_executor_floor_defaults():
    """
    Test 1: Verify the 'Floor'.
    If the context is empty, the executor should provide 'None' or '[]'
    rather than crashing, because the constants are defined in the floor.
    """
    # A registry-style arg definition asking for common constants
    arg_style = ([c.INPUT_PDF, c.INPUT_FILENAME], {c.ALIASES: c.ALIASES}, {})
    context = {}  # Empty context

    pos, kw = _resolve_arguments(arg_style, context)

    assert pos == [None, None]  # Default floor values
    assert kw == {c.ALIASES: {}}  # Default floor empty dict


def test_executor_strict_indexing_typo():
    """
    Test 2: Verify 'Strict Indexing'.
    If the Registry asks for a key that is NOT in constants (a typo),
    it should raise a KeyError immediately.
    """
    TYPO_CONSTANT = "this_key_does_not_exist_in_floor"
    arg_style = ([TYPO_CONSTANT], {}, {})
    context = {}

    with pytest.raises(KeyError):
        # This proves the executor is 'hardened' against registry typos
        _resolve_arguments(arg_style, context)


def test_executor_successful_mapping():
    """
    Test 3: Verify data delivery.
    Ensure that provided context values override the floor and reach the function.
    """
    arg_style = ([c.INPUT_PDF], {"output": c.OUTPUT}, {})
    mock_pdf = MagicMock()
    context = {c.INPUT_PDF: mock_pdf, c.OUTPUT: "final.pdf"}

    pos, kw = _resolve_arguments(arg_style, context)

    assert pos == [mock_pdf]
    assert kw == {"output": "final.pdf"}


def test_executor_full_run():
    """
    Test 4: Integration test of run_operation.
    Mocks the registry operations dictionary to ensure the flow from
    op_name to function call is solid.
    """
    mock_func = MagicMock(return_value="Success")

    # Mocking the operation data object
    class MockOp:
        def __init__(self):
            self.function = mock_func
            self.args = ([c.INPUT_FILENAME], {}, {})

    # We patch the operations dictionary directly.
    # This satisfies the 'key in src' check in _get_keyval_or_attr if it uses dict lookup,
    # or getattr if it uses object lookup.
    # Note: run_operation uses _get_keyval_or_attr(registry.operations, op_name)

    with patch("pdftl.core.executor.registry") as mock_registry:
        # Configure the mock registry to behave like a dict for the lookup
        mock_registry.operations = {"test_op": MockOp()}

        result = run_operation("test_op", {c.INPUT_FILENAME: "test.pdf"})

        assert result == "Success"
        mock_func.assert_called_once_with("test.pdf")
