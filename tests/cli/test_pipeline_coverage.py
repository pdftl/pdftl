import io
import logging
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, call, patch

import pytest

# Import the code being tested
from pdftl.cli.pipeline import (
    CliStage,
    PipelineManager,
)

# --- Mock Classes and Setup ---


class MockPdf(MagicMock):
    """A mock pikepdf.Pdf object that tracks its own closing and provides a trailer ID."""

    def __init__(self, name="default", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.closed = False
        # Mock trailer.ID for keeping PDF ID features
        self.trailer = SimpleNamespace(ID=[name.encode("utf-8") * 2, b"1" * 32])
        self.name = name

    def close(self):
        self.closed = True

    def __eq__(self, other):
        # Allow equality comparison by name for testing
        # NOTE: This comparison is based on the object's identity in the test setup.
        if isinstance(other, MockPdf):
            return self.name == other.name
        return NotImplemented


# Mock the registry for operations
MOCK_REGISTRY_OPERATIONS = {
    "basic_op": {
        "function": MagicMock(return_value=MockPdf(name="default_result")),
        "args": (["input_pdf"], {}),
        "usage": "basic_op <input>",
    },
    "error_op": {
        # This operation's args will cause an error in _make_op_args
        # by requiring a non-existent context variable
        "function": MagicMock(),
        "args": (["non_existent_context_key"], {}),
        "usage": "error_op <input>",
    },
    "no_type_op": {
        # No type defined, to test line 177
        "function": MagicMock(),
        "args": ([], {}),
    },
}

# SIMPLIFIED: Using SimpleNamespace now that production code uses attribute access
MOCK_REGISTRY = SimpleNamespace(operations=MOCK_REGISTRY_OPERATIONS)


# --- Fixtures ---


@pytest.fixture
def mock_context():
    """Returns a mock input context."""
    return SimpleNamespace(
        get_input=MagicMock(side_effect=lambda prompt, completer=None: "prompted_file.pdf")
    )


@pytest.fixture
def mock_save_pdf():
    """Mock the save_pdf function."""
    with patch("pdftl.cli.pipeline.save_pdf") as mock:
        yield mock


@pytest.fixture
def mock_registry():
    """Mock the global registry."""
    # reset to prevent state pollution
    for op in MOCK_REGISTRY.operations.values():
        if "function" in op:
            op["function"].side_effect = None
            op["function"].reset_mock()

    # Patch with the SimpleNamespace object
    with patch("pdftl.cli.pipeline.registry", MOCK_REGISTRY):
        yield MOCK_REGISTRY


@pytest.fixture
def mock_pikepdf():
    """Mock pikepdf.open and Pdf class for instance checking."""
    with patch("pikepdf.open", autospec=True) as mock_open_pdf:
        # We need a different mock object for each open call
        pdf_a = MockPdf("A")
        pdf_b = MockPdf("B")

        # Note: The side_effect here is only kept for backwards compatibility with other tests.
        # The failing tests will now locally override this side_effect to ensure isolation.
        mock_open_pdf.side_effect = [
            pdf_a,
            pdf_b,
            pdf_a,
            pdf_b,
        ]  # Cycle through A, B, A, B...

        with patch("pikepdf.Pdf", MockPdf) as mock_pdf_class:
            yield mock_open_pdf, mock_pdf_class, pdf_a, pdf_b


@pytest.fixture
def mock_logger(monkeypatch):
    """Fixture to mock the logging module for catching warnings."""
    mock_log = MagicMock()
    monkeypatch.setattr(logging, "error", mock_log)
    return mock_log


@pytest.fixture
def mock_sys_stdin():
    """Fixture to mock sys.stdin and sys.stdin.buffer."""

    class MockStdin:
        def __init__(self):
            self.isatty_value = True
            self.buffer = SimpleNamespace(read=MagicMock(return_value=b"pdf data from stdin"))

        def isatty(self):
            return self.isatty_value

    mock_stdin = MockStdin()
    with patch("pdftl.cli.pipeline.sys.stdin", mock_stdin):
        yield mock_stdin


class TestPipelineManagerCoverage:

    def test_cli_stage_resolve_io_prompts_stage_num_gt_one(self, mock_context):
        """Covers line 62: Prompt text includes stage number when stage_num > 1."""
        # FIX: Added matching input_passwords list
        stage = CliStage(
            inputs=["PROMPT", "PROMPT"],
            input_passwords=[None, None],
            handles={"main": 0},
        )

        # Test with stage_num = 2
        stage.resolve_stage_io_prompts(mock_context.get_input, stage_num=2)

        # Check the calls to get_input
        # The first call should contain "pipeline stage 2"
        mock_context.get_input.assert_has_calls(
            [
                call(
                    "Enter a filename for an input PDF (pipeline stage 2, input #1 with handle main): ",
                    completer=ANY,
                ),
                call(
                    "Enter a filename for an input PDF (pipeline stage 2, input #2): ",
                    completer=ANY,
                ),
            ]
        )

    def test_run_final_pipeline_pdf_close(
        self, mock_context, mock_pikepdf, mock_save_pdf, mock_registry
    ):
        """Covers line 100: self.pipeline_pdf.close() in the finally block."""
        # FIX: Added matching input_passwords list
        stage = CliStage(operation="basic_op", inputs=["file1.pdf"], input_passwords=[None])
        manager = PipelineManager(stages=[stage], global_options={}, input_context=mock_context)

        # Access the operation dictionary via the SimpleNamespace object
        # Note: The 'basic_op' mock returns a static object (default_result) for the op result.
        # The pikepdf.open mock returns pdf_a for the input.

        manager.run()

        # The PipelineManager sets pipeline_pdf to the result of the op, which is a static mock object.
        # This static result object (from MOCK_REGISTRY) should be closed.
        result_pdf = MOCK_REGISTRY.operations["basic_op"]["function"].return_value

        # Check that the final PDF was closed (line 100)
        assert result_pdf.closed

    def test_execute_stage_close_previous_pdf(self, mock_context, mock_pikepdf, mock_registry):
        """Covers line 124: Closing self.pipeline_pdf before running operation if not reused."""

        pdf_open_mock = mock_pikepdf[0]
        _, _, pdf_a_ref, pdf_b_ref = mock_pikepdf

        # --- ISOLATION: 1. Isolate pikepdf.open calls ---
        # Stage 1 will consume pdf_a_ref, Stage 2 will consume pdf_b_ref.
        pdf_open_mock.side_effect = [pdf_a_ref, pdf_b_ref]

        # --- ISOLATION: 2. Isolate basic_op returns (A then B) ---
        # The operation should return its input for in-place modification simulation.
        stage1_input_pdf = pdf_a_ref  # Expected input PDF object for Stage 1
        stage2_input_pdf = pdf_b_ref  # Expected input PDF object for Stage 2

        # Set the operation's side_effect to return the sequence of expected results (A, then B).
        op_func_mock = MOCK_REGISTRY.operations["basic_op"]["function"]
        op_func_mock.side_effect = [
            stage1_input_pdf,
            stage2_input_pdf,
        ]  # Return A on first call, B on second call
        op_func_mock.reset_mock()  # Reset call count for the test

        stage1 = CliStage(operation="basic_op", inputs=["file1.pdf"], input_passwords=[None])
        stage2 = CliStage(operation="basic_op", inputs=["file2.pdf"], input_passwords=[None])

        manager = PipelineManager(
            stages=[stage1, stage2], global_options={}, input_context=mock_context
        )

        # Manually run stage 1 to set pipeline_pdf
        # Stage 1 opens A, op returns A. manager.pipeline_pdf = A.
        manager._validate_and_execute_numbered_stage(0, stage1)

        # Assertion now passes because manager.pipeline_pdf is A and stage1_input_pdf is A
        assert manager.pipeline_pdf == stage1_input_pdf
        assert not stage1_input_pdf.closed  # pdf_a_ref should be open after stage 1

        # Run stage 2
        # Before this executes, pdf_a_ref (previous manager.pipeline_pdf) should be closed (line 124)
        # Stage 2 opens B, op returns B. manager.pipeline_pdf = B.
        manager._validate_and_execute_numbered_stage(1, stage2)

        # Assert previous pipeline PDF (pdf_a_ref) was closed
        assert stage1_input_pdf.closed

        # Assert the new pipeline PDF is pdf_b_ref (B) and is still open
        assert manager.pipeline_pdf == stage2_input_pdf
        assert not stage2_input_pdf.closed

    def test_validate_effective_inputs_no_type_returns(self, mock_context, mock_registry):
        """Covers line 177: return if op_data doesn't have a 'type' key."""
        # FIX: Added matching input_passwords list
        stage = CliStage(operation="no_type_op", inputs=["file.pdf"], input_passwords=[None])
        manager = PipelineManager(stages=[stage], global_options={}, input_context=mock_context)

        # The function should return without raising an error
        # effective_inputs will be 1 (is_first=True)
        try:
            manager._validate_stage_args(stage, is_first=True, is_last=True)
        except Exception as e:
            pytest.fail(f"_validate_stage_args raised unexpected exception: {e}")

    def test_run_operation_make_op_args_internal_error(
        self, mock_context, mock_pikepdf, mock_registry, caplog
    ):
        """Covers lines 218-223: Catching, logging, and re-raising internal error in _make_op_args."""
        pdf_open_mock, _, pdf_a_ref, _ = mock_pikepdf

        # ISOLATION: Ensure this test's one call to open returns pdf_a_ref
        pdf_open_mock.side_effect = [pdf_a_ref]

        # We need to ensure a PDF is opened first to test the exception handling post-opening.

        # FIX: Added matching input_passwords list
        stage = CliStage(operation="error_op", inputs=["file.pdf"], input_passwords=[None])
        manager = PipelineManager(stages=[stage], global_options={}, input_context=mock_context)

        # _open_input_pdfs will open one file: (pdf_a_ref)
        opened_pdfs = manager._open_input_pdfs(stage, is_first=True)

        # Running the op will cause a KeyError because 'non_existent_context_key'
        # is requested in the MOCK_REGISTRY for 'error_op'
        with caplog.at_level("ERROR", logger="pdftl.cli.pipeline"):
            caplog.clear()
            with pytest.raises(KeyError, match="'non_existent_context_key'"):
                manager._run_operation(stage, opened_pdfs)

        assert len(caplog.records) == 1
        record = caplog.records[0]

        assert record.levelname == "ERROR"
        assert "Internal error assigning arguments for operation" in record.message

        # The re-raise of the original exception (KeyError) covers line 223.

    def test_open_pdf_from_special_input_stdin(self, mock_context, mock_pikepdf, mock_sys_stdin):
        """Covers lines 245-246: Reading from stdin buffer for the first stage."""
        pdf_open_mock, _, pdf_a_ref, _ = mock_pikepdf

        # ISOLATION: Ensure this test's one call to open returns pdf_a_ref
        pdf_open_mock.side_effect = [pdf_a_ref]

        # FIX: Added matching input_passwords list
        stage = CliStage(inputs=["-"], input_passwords=[None])
        manager = PipelineManager(stages=[stage], global_options={}, input_context=mock_context)

        # Make stdin non-tty to simulate piped input
        mock_sys_stdin.isatty_value = False

        pdf = manager._open_pdf_from_special_input(is_first=True)

        # Check line 245: sys.stdin.buffer.read() was called
        mock_sys_stdin.buffer.read.assert_called_once()

        # Check line 246: pikepdf.open was called with a BytesIO object
        pdf_open_mock.assert_called_once()
        assert isinstance(pdf_open_mock.call_args[0][0], io.BytesIO)

        # The returned object should be the mocked PDF (now reliably pdf_a_ref)
        assert pdf == pdf_a_ref

    def test_open_pdf_from_special_input_pipeline_pdf(self, mock_context):
        """Covers line 253: Returning pipeline_pdf for input '_' in non-first stage."""
        manager = PipelineManager(stages=[], global_options={}, input_context=mock_context)

        # Manually set the pipeline PDF (simulating output from a previous stage)
        expected_pdf = MockPdf("pipeline_result")
        manager.pipeline_pdf = expected_pdf

        # Call for a non-first stage
        result_pdf = manager._open_pdf_from_special_input(is_first=False)

        # Check line 253: The function returned the pre-existing pipeline PDF
        assert result_pdf is expected_pdf

    @patch.object(PipelineManager, "_open_pdf_from_special_input", autospec=True)
    def test_open_input_pdfs_special_input_dispatch(
        self, mock_open_special, mock_context, mock_pikepdf
    ):
        """Covers line 280: Dispatching to _open_pdf_from_special_input for '-' and '_'."""
        pdf_open_mock = mock_pikepdf[0]

        # We need one pdf object returned for the 'file.pdf' input. Use pdf_a_ref.
        pdf_open_mock.side_effect = [mock_pikepdf[2]]
        mock_open_special.return_value = MockPdf("special_input")

        # Use both special inputs and a regular file
        # This test already had matching input_passwords, validating its intent
        stage = CliStage(inputs=["-", "_", "file.pdf"], input_passwords=[None, None, None])
        manager = PipelineManager(stages=[stage], global_options={}, input_context=mock_context)

        # Ensure special PDF exists for the '_' case
        manager.pipeline_pdf = MockPdf("previous_stage")

        # Run for first stage (is_first=True)
        opened_pdfs = manager._open_input_pdfs(stage, is_first=True)

        # Check line 280: _open_pdf_from_special_input was called twice (for '-' and '_')
        assert mock_open_special.call_count == 2
        mock_open_special.assert_has_calls(
            [call(manager, True), call(manager, True)]  # for '-'  # for '_'
        )

        # Check that _open_pdf_from_file (via pikepdf.open mock) was called once (for 'file.pdf')
        assert pdf_open_mock.call_count == 1
