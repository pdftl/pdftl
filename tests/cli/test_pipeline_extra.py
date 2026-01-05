from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.cli.pipeline import CliStage, PipelineManager
from pdftl.core.types import OpResult

# --- Reuse Mock Infrastructure ---


@pytest.fixture
def mock_context():
    return SimpleNamespace(get_input=MagicMock(return_value="input.pdf"))


@pytest.fixture
def mock_registry():
    """Patches the registry used in pipeline.py"""
    ops = {}
    registry_obj = SimpleNamespace(operations=ops)
    with patch("pdftl.cli.pipeline.registry", registry_obj):
        yield ops


@pytest.fixture
def mock_save_content():
    with patch("pdftl.cli.pipeline.save_content") as mock:
        yield mock


# --- Tests ---


def test_pipeline_op_result_hook_and_discard(mock_context, mock_registry, mock_save_content):
    """
    Covers:
    - Lines 150-156: Handling OpResult, setting discardable, calling cli_hook.
    - Lines 111-114: Skipping save when result is discardable and no output file.
    """
    # 1. Setup Data: Use a REAL in-memory PDF
    # This ensures isinstance(obj, pikepdf.Pdf) returns True naturally.
    real_pdf = pikepdf.new()

    # Create an OpResult marked as discardable
    op_result = OpResult(pdf=real_pdf, is_discardable=True)

    mock_hook = MagicMock()

    # 2. Configure Registry
    mock_registry["hook_op"] = {"function": MagicMock(), "args": ([], {}), "cli_hook": mock_hook}

    # 3. Setup Pipeline
    stage = CliStage(operation="hook_op", inputs=["in.pdf"], input_passwords=[None])
    manager = PipelineManager(stages=[stage], input_context=mock_context)

    # 4. Run with mocks
    # Spy on the real_pdf.close method so we can assert it was called
    # without relying on side effects (exceptions) which vary by PDF type (memory vs file).
    with patch.object(real_pdf, "close", side_effect=real_pdf.close) as mock_pdf_close:
        # Pass the REAL pdf through the mock of _open_input_pdfs
        with patch.object(manager, "_open_input_pdfs", return_value=[real_pdf]):
            with patch("pdftl.cli.pipeline.run_operation", return_value=op_result):
                manager.run()

        # --- Assertions ---

        # Cover Lines 154-155: Verify Hook was called
        mock_hook.assert_called_once()
        args, _ = mock_hook.call_args
        assert args[0] == op_result
        assert args[1] == stage

        # Cover Lines 150-151, 156: Verify OpResult unpacking
        assert manager.results[0] == op_result
        assert manager.result_discardable is True
        assert manager.pipeline_pdf == real_pdf

        # Cover Lines 111-114: Verify Save Skipped
        mock_save_content.assert_not_called()

        # Cleanup verification (Line 119)
        mock_pdf_close.assert_called_once()


def test_validate_unknown_operation(mock_context, mock_registry):
    """
    Covers:
    - Line 207: Early return when validating effective inputs for unknown operation.
    """
    manager = PipelineManager(stages=[], input_context=mock_context)
    manager._validate_number_of_effective_inputs("ghost_op", 1)
