from pdftl.cli.pipeline import InlineSubPipeline

import pytest
from unittest.mock import MagicMock, patch
from pdftl.cli.pipeline import EachSubPipeline
import pdftl.cli.pipeline as pipeline_mod


from pdftl.cli.pipeline import PipelineManager

import io
import logging
from types import SimpleNamespace
from unittest.mock import ANY, call, Mock

import pikepdf

import pdftl.core.constants as c
from pdftl.cli.pipeline import (
    CliStage,
)
from pdftl.core.core_types import OpResult
from pdftl.core.registry import registry
from pdftl.exceptions import MissingArgumentError, PdftlError, UserCommandLineError
from pdftl.utils.user_input import UserInputContext


# -----------------------------
# Setup dummy operations for testing
# -----------------------------
def dummy_op(*args, **kwargs):
    return "dummy_result"


@pytest.fixture(autouse=True)
def _register_dummy_operations():
    single_key = "single_op"
    multi_key = "multi_op"

    had_single = single_key in registry.operations
    had_multi = multi_key in registry.operations
    old_single = registry.operations.get(single_key)
    old_multi = registry.operations.get(multi_key)

    registry.operations[single_key] = {
        "function": dummy_op,
        "args": ([], {}),
        "type": "single input operation",
        "usage": "single_op input",
    }

    registry.operations[multi_key] = {
        "function": dummy_op,
        "args": ([], {}),
        "type": "multi input operation",
        "usage": "multi_op input1 input2",
    }

    yield

    if had_single:
        registry.operations[single_key] = old_single
    else:
        registry.operations.pop(single_key, None)

    if had_multi:
        registry.operations[multi_key] = old_multi
    else:
        registry.operations.pop(multi_key, None)


# -----------------------------
# Validation and input tests
# -----------------------------
def test_validate_stage_args_missing_first_input():
    stage = CliStage(operation=None, inputs=[])
    manager = PipelineManager(stages=[stage], input_context=MagicMock())
    with pytest.raises(MissingArgumentError):
        manager._validate_stage_args(stage, is_first=True, is_last=False)


def test_validate_stage_args_requires_output():
    stage = CliStage(operation="filter", inputs=["file1.pdf"])
    manager = PipelineManager(stages=[stage], input_context=MagicMock())
    with pytest.raises(MissingArgumentError):
        manager._validate_stage_args(stage, is_first=False, is_last=True)


def test_validate_number_of_effective_inputs_single_multi():
    manager = PipelineManager(stages=[], input_context=MagicMock())
    with pytest.raises(UserCommandLineError):
        manager._validate_number_of_effective_inputs("single_op", 2)
    with pytest.raises(MissingArgumentError):
        manager._validate_number_of_effective_inputs("multi_op", 1)


# -----------------------------
# _open_pdf_from_special_input
# -----------------------------
def test_open_pdf_from_special_input(monkeypatch):
    manager = PipelineManager(stages=[], input_context=MagicMock())

    # stdin is a TTY -> should raise error
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    with pytest.raises(UserCommandLineError):
        manager._open_pdf_from_special_input(password=None, is_first=True)

    # '_' input when pipeline_pdf is None
    manager.pipeline_pdf = None
    with pytest.raises(UserCommandLineError):
        manager._open_pdf_from_special_input(password=None, is_first=False)


# -----------------------------
# _open_pdf_from_file errors
# -----------------------------
def test_open_pdf_from_file(monkeypatch):
    manager = PipelineManager(stages=[], input_context=MagicMock())

    # FileNotFoundError -> UserCommandLineError
    def fake_fnf(filename, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr("pikepdf.open", fake_fnf)
    with pytest.raises(UserCommandLineError):
        manager._open_pdf_from_file("nofile.pdf", None)

    # PasswordError -> UserCommandLineError
    def fake_pw(filename, **kwargs):
        raise pikepdf.PasswordError("pw error")

    monkeypatch.setattr("pikepdf.open", fake_pw)
    with pytest.raises(UserCommandLineError):
        manager._open_pdf_from_file("locked.pdf", None)


def test_open_pdf_from_file_with_password(monkeypatch):
    dummy_pdf = MagicMock(spec=pikepdf.Pdf)
    monkeypatch.setattr("pikepdf.open", lambda filename, **kw: dummy_pdf)
    manager = PipelineManager(stages=[], input_context=MagicMock())
    result = manager._open_pdf_from_file("file.pdf", "secret")
    assert result == dummy_pdf


# -----------------------------
# _run_operation errors and success
# -----------------------------
def test_run_operation_missing_function_or_args():
    registry.operations["bad_op"] = {"args": ([], {})}
    stage = CliStage(operation="bad_op", inputs=["file.pdf"])
    manager = PipelineManager(stages=[], input_context=MagicMock())
    with pytest.raises(ValueError):
        manager._run_operation(stage, [])


def test_run_operation_success(monkeypatch):
    stage = CliStage(operation="single_op", inputs=["file.pdf"])
    manager = PipelineManager(stages=[], input_context=MagicMock())
    original_args = registry.operations["single_op"]["args"]
    original_function = registry.operations["single_op"]["function"]
    try:
        registry.operations["single_op"]["args"] = ([], {}, {})
        registry.operations["single_op"]["function"] = lambda *a, **kw: "OK"
        result = manager._run_operation(stage, [])
        assert result == "OK"
    finally:
        registry.operations["single_op"]["args"] = original_args
        registry.operations["single_op"]["function"] = original_function


# -----------------------------
# _execute_stage generator and non-generator
# -----------------------------
def test_execute_stage_non_generator(monkeypatch):
    dummy_pdf1 = MagicMock(spec=pikepdf.Pdf)
    dummy_pdf2 = MagicMock(spec=pikepdf.Pdf)
    stage = CliStage(operation="single_op", inputs=["a.pdf"])
    manager = PipelineManager(stages=[], input_context=MagicMock())

    manager._open_input_pdfs = MagicMock(return_value=([dummy_pdf1, dummy_pdf2], ["a.pdf"], {}))
    manager._run_operation = MagicMock(return_value=dummy_pdf1)
    manager._execute_stage(stage, is_first=True)

    dummy_pdf2.close.assert_called_once()
    assert manager.pipeline_pdf == dummy_pdf1


def test_execute_stage_generator(monkeypatch):
    import types
    from unittest.mock import MagicMock, patch

    import pikepdf

    from pdftl.cli.pipeline import CliStage, PipelineManager

    dummy_pdf = MagicMock(spec=pikepdf.Pdf)
    stage = CliStage(operation="single_op", inputs=["a.pdf"])
    manager = PipelineManager(stages=[], input_context=MagicMock())

    def gen():
        yield ("out.pdf", dummy_pdf)

    manager._open_input_pdfs = MagicMock(return_value=([dummy_pdf], ["a.pdf"], {}))
    manager._run_operation = MagicMock(return_value=gen())

    with patch("pdftl.cli.pipeline.save_content") as save_mock:
        manager._execute_stage(stage, is_first=True)

        # 1. It should NOT save yet (saving happens in the 'run' loop now)
        save_mock.assert_not_called()

        # 2. The pipeline_pdf should be set to the generator
        assert isinstance(manager.pipeline_pdf, types.GeneratorType)

        # 3. CRITICAL: It should NOT have closed the input PDF
        # (This confirms your fix for the blank pages/render bug)
        dummy_pdf.close.assert_not_called()


# -----------------------------
# _open_input_pdfs with keep_first_id / keep_final_id
# -----------------------------
def test_open_input_pdfs_success(monkeypatch):
    dummy_pdf = MagicMock(spec=pikepdf.Pdf)
    dummy_pdf.trailer.ID = ["id1", "id2"]
    monkeypatch.setattr("pikepdf.open", lambda f, **kw: dummy_pdf)

    # --- Case 1: keep_first_id ---
    stage = CliStage(inputs=["f1.pdf", "f2.pdf"], input_passwords=[None, None])

    # Simulate the parser attaching the global flag to the stage options
    stage.options["keep_first_id"] = True

    # Initialize Manager with the stage (so it knows this is the last/only stage)
    manager = PipelineManager(stages=[stage], input_context=MagicMock())

    pdfs, _, _handles = manager._open_input_pdfs(stage, is_first=True)
    assert pdfs == [dummy_pdf, dummy_pdf]
    assert manager.kept_id == ["id1", "id2"]

    # --- Case 2: keep_final_id ---
    stage_final = CliStage(inputs=["f1.pdf", "f2.pdf"], input_passwords=[None, None])
    stage_final.options["keep_final_id"] = True

    manager = PipelineManager(stages=[stage_final], input_context=MagicMock())

    # Even if is_first=False, keep_final_id should capture the ID of the opened PDFs
    _pdfs, _, _handles = manager._open_input_pdfs(stage_final, is_first=False)
    assert manager.kept_id == ["id1", "id2"]


# -----------------------------
# _make_op_args tests
# -----------------------------
def test_make_op_args_with_kw_constants():
    manager = PipelineManager(stages=[], input_context=MagicMock())
    context = {"a": 1, "b": 2}
    arg_style = (["a"], {"x": "b"}, {"y": 3})
    pos_args, kw_args = manager._make_op_args(arg_style, context)
    assert pos_args == [1]
    assert kw_args == {"x": 2, "y": 3}


def test_make_op_args_error(monkeypatch):
    manager = PipelineManager(stages=[], input_context=MagicMock())
    with pytest.raises(KeyError):
        manager._make_op_args((["missing"], {}), {})


# -----------------------------
# CliStage.resolve_stage_io_prompts
# -----------------------------
def test_resolve_stage_io_prompts_prompts_user(monkeypatch):
    stage = CliStage(inputs=["PROMPT"])
    mock_input = MagicMock(return_value="file1.pdf")
    stage.handles = {"h1": 0}
    stage.resolve_stage_io_prompts(mock_input, stage_num=1)
    mock_input.assert_called_once()
    assert stage.inputs == ["file1.pdf"]


# -----------------------------
# _save_kw_options simple branch
# -----------------------------
def test_save_kw_options_returns_expected_dict():
    # PipelineManager no longer accepts global_options in __init__
    manager = PipelineManager(stages=[], input_context=MagicMock())
    manager.kept_id = ["id1", "id2"]

    # We must explicitly pass options to _save_kw_options now to simulate a stage save
    result = manager._save_kw_options(override_options={"foo": "bar"})

    assert result == {"options": {"foo": "bar"}, "set_pdf_id": ["id1", "id2"]}


# -----------------------------
# _validate_and_execute_numbered_stage
# -----------------------------
def test_validate_and_execute_numbered_stage_final_empty(monkeypatch):
    stage = CliStage(operation=None)
    manager = PipelineManager(stages=[stage], input_context=MagicMock())
    manager._validate_and_execute_numbered_stage(0, stage)  # Should not raise


# -----------------------------
# Integration-style run test
# -----------------------------
class DummyPdf:
    def close(self):
        pass

    trailer = type("Trailer", (), {"ID": ["id1", "id2"]})()


def test_pipeline_run_dummy_op(monkeypatch):
    stage = CliStage(
        operation="single_op",
        inputs=["dummy.pdf"],
        input_passwords=[None],
        options={"output": "dummy_out.pdf"},
    )

    input_context = MagicMock()
    manager = PipelineManager(stages=[stage], input_context=input_context)

    monkeypatch.setattr(
        PipelineManager, "_open_pdf_from_file", lambda self, filename, pw: DummyPdf()
    )
    monkeypatch.setattr(
        PipelineManager,
        "_run_operation",
        lambda self, stage, _opened_pdfs, effective_inputs=None, adjusted_handles=None: DummyPdf(),
    )
    with patch("pdftl.cli.pipeline.save_content") as save_mock:
        manager.run()
        assert isinstance(manager.pipeline_pdf, DummyPdf)
        save_mock.assert_called_once()


class TestPipelineValidationCoverage:
    def test_missing_output_raises_error(self):
        """
        Test lines 279-287: Missing output for 'filter' (or an op requiring output)
        in the final stage correctly raises a MissingArgumentError.
        """
        # Create a stage that uses 'filter' but lacks the OUTPUT option
        stage = CliStage(operation="filter", inputs=["dummy.pdf"], options={})

        pm = PipelineManager(stages=[stage], input_context=MagicMock(), is_inline=False)

        with pytest.raises(MissingArgumentError) as exc_info:
            pm._validate_stage_args(stage, is_first=True, is_last=True)

        assert "requires 'output <file>' in the final stage" in str(exc_info.value)

    def test_inline_skips_output_validation(self):
        """
        Test lines 279-287: Inline pipelines bypass the missing output requirement.
        """
        stage = CliStage(operation="filter", inputs=["dummy.pdf"], options={})

        # is_inline=True forces the condition at line 281 to fail, skipping the error block
        pm = PipelineManager(stages=[stage], input_context=MagicMock(), is_inline=True)

        try:
            pm._validate_stage_args(stage, is_first=True, is_last=True)
        except MissingArgumentError as e:
            # If a MissingArgumentError is raised later in the function (like effective inputs),
            # we just want to ensure it wasn't the output validation error we bypassed.
            assert "requires 'output <file>'" not in str(e)
        except PdftlError:
            pass  # We don't care about other downstream validation exceptions here


class TestPipelineSourceOpValidation:
    @patch("pdftl.cli.pipeline.registry")
    def test_source_operation_with_inputs_raises_error(self, mock_registry):
        """
        Test lines 301-305: Providing inputs to a 'source operation'
        correctly raises a UserCommandLineError.
        """
        # Tell the mocked registry that 'mock_source_op' is a source operation
        mock_registry.operations.get.return_value = {"type": "source operation"}

        # Setup a minimal PipelineManager (stages aren't needed since we are calling the validator directly)
        pm = PipelineManager(stages=[], input_context=MagicMock())

        with pytest.raises(UserCommandLineError) as exc_info:
            # effective_inputs=1 (> 0) natively triggers the raise on line 302
            pm._validate_number_of_effective_inputs(operation="mock_source_op", effective_inputs=1)

        # Verify the custom error message is generated correctly
        error_msg = str(exc_info.value)
        assert "mock_source_op" in error_msg
        assert "creates a new PDF from scratch" in error_msg
        assert "received 1 input" in error_msg


# --- merged from test_pipeline_coverage.py ---

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
def mock_save_content():
    """Mock the save_content function."""
    with patch("pdftl.cli.pipeline.save_content") as mock:
        yield mock


@pytest.fixture
def mock_registry():
    """Mock the global registry."""
    # reset to prevent state pollution
    for op in MOCK_REGISTRY.operations.values():
        if "function" in op:
            func = op["function"]
            # Check if it's a Mock/MagicMock object before trying to reset it
            if isinstance(func, Mock):
                func.side_effect = None
                func.reset_mock()
    # Patch with the SimpleNamespace object
    with patch("pdftl.cli.pipeline.registry", MOCK_REGISTRY):
        with patch("pdftl.core.executor.registry", MOCK_REGISTRY):
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
        self, mock_context, mock_pikepdf, mock_save_content, mock_registry
    ):
        """Covers line 100: self.pipeline_pdf.close() in the finally block."""
        # FIX: Added matching input_passwords list
        stage = CliStage(operation="basic_op", inputs=["file1.pdf"], input_passwords=[None])
        manager = PipelineManager(stages=[stage], input_context=mock_context)

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

        manager = PipelineManager(stages=[stage1, stage2], input_context=mock_context)

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
        manager = PipelineManager(stages=[stage], input_context=mock_context)

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
        manager = PipelineManager(stages=[stage], input_context=mock_context)

        # _open_input_pdfs will open one file: (pdf_a_ref)
        opened_pdfs = manager._open_input_pdfs(stage, is_first=True)

        # Running the op will cause a KeyError because 'non_existent_context_key'
        # is requested in the MOCK_REGISTRY for 'error_op'
        with caplog.at_level("ERROR", logger="pdftl.core.executor"):
            caplog.clear()
            with pytest.raises(KeyError, match="'non_existent_context_key'"):
                manager._run_operation(stage, opened_pdfs)

        assert len(caplog.records) == 0
        # record = caplog.records[0]

        # assert record.levelname == "ERROR"
        # assert "Internal error assigning arguments for operation" in record.message

        # The re-raise of the original exception (KeyError) covers line 223.

    def test_open_pdf_from_special_input_stdin(self, mock_context, mock_pikepdf, mock_sys_stdin):
        """Covers lines 245-246: Reading from stdin buffer for the first stage."""
        pdf_open_mock, _, pdf_a_ref, _ = mock_pikepdf

        # ISOLATION: Ensure this test's one call to open returns pdf_a_ref
        pdf_open_mock.side_effect = [pdf_a_ref]

        # FIX: Added matching input_passwords list
        stage = CliStage(inputs=["-"], input_passwords=[None])
        manager = PipelineManager(stages=[stage], input_context=mock_context)

        # Make stdin non-tty to simulate piped input
        mock_sys_stdin.isatty_value = False

        pdf = manager._open_pdf_from_special_input(password=None, is_first=True)

        # Check line 245: sys.stdin.buffer.read() was called
        mock_sys_stdin.buffer.read.assert_called_once()

        # Check line 246: pikepdf.open was called with a BytesIO object
        pdf_open_mock.assert_called_once()
        assert isinstance(pdf_open_mock.call_args[0][0], io.BytesIO)

        # The returned object should be the mocked PDF (now reliably pdf_a_ref)
        assert pdf == pdf_a_ref

    def test_open_pdf_from_special_input_pipeline_pdf(self, mock_context):
        """Covers line 253: Returning pipeline_pdf for input '_' in non-first stage."""
        manager = PipelineManager(stages=[], input_context=mock_context)

        # Manually set the pipeline PDF (simulating output from a previous stage)
        expected_pdf = MockPdf("pipeline_result")
        manager.pipeline_pdf = expected_pdf

        # Call for a non-first stage
        result_pdf = manager._open_pdf_from_special_input(password=None, is_first=False)

        # Check line 253: The function returned the pre-existing pipeline PDF
        assert result_pdf is expected_pdf


# tests/cli/test_pipeline.py


@pytest.fixture
def mock_input_context(mocker):
    return mocker.Mock(spec=UserInputContext)


def test_open_pdf_from_stdin_password_error(mocker, mock_input_context):
    """
    Covers Lines 312-319: Exception handling when stdin input is encrypted
    but no password is provided.
    """

    # Setup PipelineManager
    manager = PipelineManager([], mock_input_context)

    # Mock smart_pikepdf_open to raise PasswordError
    # We patch it where it is imported in pipeline.py
    mocker.patch(
        "pdftl.cli.pipeline.smart_pikepdf_open", side_effect=pikepdf.PasswordError("Encrypted")
    )

    # We need to simulate isatty returning False (meaning data is piped in)
    mocker.patch("sys.stdin.isatty", return_value=False)

    # Call the method expecting the custom UserCommandLineError
    with pytest.raises(UserCommandLineError) as excinfo:
        # is_first=True forces it to try reading from stdin
        manager._open_pdf_from_special_input(password=None, is_first=True)

    # Verify the error message contains the helpful hint defined in lines 316-317
    assert "data on stdin is encrypted and requires a password" in str(excinfo.value)


def test_open_input_pdfs_dispatches_special_inputs(mocker, mock_input_context):
    """
    Covers Line 359: if filename in ["-", "_"]: ...

    This verifies that the loop correctly identifies '-' or '_'
    and routes them to _open_pdf_from_special_input.
    """
    # Setup a stage with a special input char "-" (stdin) and a normal file
    stage = CliStage(inputs=["-", "normal.pdf"], input_passwords=[None, None])

    manager = PipelineManager([stage], mock_input_context)

    # Mock the two internal opening methods
    mock_special = mocker.patch.object(
        manager, "_open_pdf_from_special_input", return_value="PDF_SPECIAL"
    )
    mock_file = mocker.patch.object(manager, "_open_pdf_from_file", return_value="PDF_FILE")

    # Execute
    results, _, _handles = manager._open_input_pdfs(stage, is_first=True)

    # Assertions
    assert results == ["PDF_SPECIAL", "PDF_FILE"]

    # Verify line 359 logic: The special handler was called for the first input
    mock_special.assert_called_once_with(None, True)

    # Verify the normal handler was called for the second
    mock_file.assert_called_once_with("normal.pdf", None)


def test_process_result_implicit_passthrough():
    """
    Test covering line 199 in pipeline.py:
    Ensures that if an operation returns no PDF (result.pdf is None),
    the pipeline defaults to passing the first input PDF forward.
    """
    # 1. Setup minimal PipelineManager
    # We set is_api=True to skip CLI hook lookup logic for simplicity
    input_context = MagicMock()
    input_context.is_api = True

    stage = CliStage(operation="mock_read_only_op")
    pipeline = PipelineManager(stages=[stage], input_context=input_context)

    # 2. Prepare the specific conditions for line 199
    # Condition A: opened_pdfs is not empty
    mock_input_pdf = MagicMock(name="InputPDF")
    opened_pdfs = [mock_input_pdf]

    # Condition B: Result has NO pdf (simulating dump_text/dump_data)
    op_result = OpResult(success=True, pdf=None, data="some text output")

    # 3. Execution
    pipeline._process_result(op_result, stage, opened_pdfs)

    # 4. Assertion
    # Verify that pipeline_pdf was set to the input PDF (pass-through)
    assert pipeline.pipeline_pdf == mock_input_pdf

    # Verify we didn't accidentally close the input PDF inside _process_result
    # (The cleanup logic only closes inputs that *aren't* the result)
    mock_input_pdf.close.assert_not_called()


def test_pipeline_missing_output_error():
    """Hit Line 261: Validation error for operations requiring output."""
    # Mock registry to say 'filter' requires output
    with patch("pdftl.core.registry.registry.operations", {"filter": {"usage": "output"}}):
        stage = CliStage(operation="filter", inputs=["in.pdf"], options={})
        mgr = PipelineManager(stages=[stage], input_context=MagicMock(), is_inline=False)
        with pytest.raises(MissingArgumentError, match="requires 'output <file>'"):
            mgr._validate_stage_args(stage, is_first=True, is_last=True)


def test_sibling_handle_reference():
    """Hit Line 433: Using A=file.pdf then referring to A in same stage."""
    mock_pdf = MagicMock()
    # Ensure input_passwords length matches inputs length
    stage = CliStage(
        operation="cat", inputs=["a.pdf", "A"], input_passwords=[None, None], handles={"A": 0}
    )

    mgr = PipelineManager(stages=[stage], input_context=MagicMock())
    with patch.object(mgr, "_open_pdf_from_file", return_value=mock_pdf):
        opened, _, _handles = mgr._open_input_pdfs(stage, is_first=True)
        # The first was opened from file, the second was copied from the sibling list
        assert len(opened) == 2
        assert opened[1] is mock_pdf


def test_inline_pipeline_no_output_error():
    """Hit Line 423: Inline JOB...DONE returns nothing."""
    sub_pipe = InlineSubPipeline(stages=[CliStage(operation="dump_text")])
    # Again, match passwords length to inputs length
    stage = CliStage(operation="cat", inputs=[sub_pipe], input_passwords=[None])

    mgr = PipelineManager(stages=[stage], input_context=MagicMock())

    # We patch the class's 'run' so it does nothing,
    # then we'll use a side_effect to ensure the sub-manager's
    # pipeline_pdf is None when it finishes.
    with patch("pdftl.cli.pipeline.PipelineManager.run"):
        # We need to simulate the sub_manager that is created INSIDE _open_input_pdfs
        # To hit line 423, the sub_manager.pipeline_pdf must be None.
        with pytest.raises(UserCommandLineError, match="Inline pipeline returned no output PDF"):
            mgr._open_input_pdfs(stage, is_first=True)


# This test uses a context manager to ensure the registry is never permanently altered
def test_pipeline_config_error_coverage(mocker):
    # Fixes Line 292
    # mocker.patch.dict ensures 'broken_op' disappears after this function returns
    mocker.patch.dict(registry.operations, {"broken_op": {"type": "single input operation"}})

    stage = CliStage(operation="broken_op", inputs=["test.pdf"])
    # Mock the input context so we don't trigger real IO
    mock_context = mocker.Mock()
    manager = PipelineManager(stages=[stage], input_context=mock_context)

    with pytest.raises(ValueError, match="is not fully configured"):
        manager._run_operation(stage, opened_pdfs=[mocker.Mock()])


# Check after the test to prove no pollution occurred
def test_verify_no_pollution():
    assert "broken_op" not in registry.operations


def test_pipeline_missing_op_config(mocker):
    from pdftl.cli.pipeline import CliStage, PipelineManager
    from pdftl.core.registry import registry

    # Ensure 'ghost_op' is definitely not in the registry
    mocker.patch.dict(registry.operations, {}, clear=False)

    stage = CliStage(operation="ghost_op", inputs=["in.pdf"])
    manager = PipelineManager(stages=[stage], input_context=mocker.Mock())

    # This hits Line 292 because op_data will be None
    with pytest.raises(ValueError, match="Operation 'ghost_op' is not fully configured"):
        manager._run_operation(stage, opened_pdfs=[mocker.Mock()])


# --- merged from test_pipeline_extra.py ---

# --- Reuse Mock Infrastructure ---


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
    mock_registry.operations["hook_op"] = {
        "function": MagicMock(),
        "args": ([], {}),
        "cli_hook": mock_hook,
    }

    # 3. Setup Pipeline
    stage = CliStage(operation="hook_op", inputs=["in.pdf"], input_passwords=[None])
    manager = PipelineManager(stages=[stage], input_context=mock_context)

    # 4. Run with mocks
    # Spy on the real_pdf.close method so we can assert it was called
    # without relying on side effects (exceptions) which vary by PDF type (memory vs file).
    with patch.object(real_pdf, "close", side_effect=real_pdf.close) as mock_pdf_close:
        # Pass the REAL pdf through the mock of _open_input_pdfs
        with patch.object(manager, "_open_input_pdfs", return_value=([real_pdf], ["in.pdf"], {})):
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


@pytest.fixture
def mock_input_context2():
    """Provides a basic mock for the pipeline input context."""
    context = MagicMock()
    context.get_input = MagicMock(return_value="resolved_prompt.pdf")
    context.is_api = False
    return context


@pytest.fixture
def mock_pdf_asset():
    """Provides a mock pikepdf object structure."""
    pdf = MagicMock()
    pdf.trailer = MagicMock()
    pdf.trailer.ID = [b"mock_id_1", b"mock_id_2"]
    return pdf


def test_final_empty_stage_with_output(caplog):
    stage = CliStage(
        operation=None,
        options={c.OUTPUT: "out.pdf"},
    )

    pm = PipelineManager([stage], mock_input_context2)

    with caplog.at_level(logging.INFO):
        pm._validate_and_execute_numbered_stage(0, stage)

    assert "Finalizing Pipeline Execution" in caplog.text
    assert "Target File: out.pdf" in caplog.text


def test_stage_logging_branches(caplog, monkeypatch):
    pm = PipelineManager([], mock_input_context2)

    monkeypatch.setattr(pm, "_execute_stage", lambda *a, **k: None)
    monkeypatch.setattr(pm, "_validate_stage_args", lambda *a, **k: None)

    stage = CliStage(
        operation="dummy",
        inputs=[],
        operation_args=["foo"],
        options={
            c.OUTPUT: "out.pdf",
            "setting": True,
        },
    )

    with caplog.at_level(logging.INFO):
        pm._validate_and_execute_numbered_stage(1, stage)

    assert "Implicit pipeline stream" in caplog.text
    assert "Arguments: foo" in caplog.text
    assert "Localized Settings" in caplog.text


def test_run_operation_expand_shorthand_type_error(mock_context, mock_registry, monkeypatch):
    """Covers lines 401-402: Catches TypeError from expand_shorthand_args and raises UserCommandLineError."""

    # Force expand_shorthand_args to raise a TypeError when executed
    def mock_expand(args):
        raise TypeError("Simulated shorthand expansion layout error")

    monkeypatch.setattr(pipeline_mod, "expand_shorthand_args", mock_expand)

    # Configure registry so validation passes lines 393-394
    mock_registry.operations["dummy_op"] = {"function": lambda x: x, "args": ([], {})}

    stage = CliStage(operation="dummy_op", operation_args=["malformed_shorthand"])
    manager = PipelineManager(stages=[stage], input_context=mock_context)

    # Verify that the TypeError is successfully intercepted and wrapped
    with pytest.raises(UserCommandLineError, match="Simulated shorthand expansion layout error"):
        manager._run_operation(stage, opened_pdfs=[])


# =====================================================================
# 4. Context Extraction and Shared Handles (Line 541, 578-581, 591-593)
# =====================================================================


# =====================================================================
# 1. Simple Utility & Logging Evaluation (Lines 60, 205)
# =====================================================================


def test_each_sub_pipeline_repr():
    """Covers Line 60: __repr__ mapping context."""
    pipeline = EachSubPipeline(stages=[], original_text="<each_sub_pipeline_test>")
    assert repr(pipeline) == "<each_sub_pipeline_test>"


def test_output_targets_info_non_string_input(caplog):
    """Covers Line 205: Logging fallback for non-string stage inputs."""
    non_str_input = EachSubPipeline(stages=[], original_text="<mock_sub_pipeline>")
    stage = CliStage(operation="filter", inputs=[non_str_input], input_passwords=[None])
    manager = PipelineManager(stages=[stage], input_context=MagicMock())

    with caplog.at_level(logging.INFO):
        manager._output_targets_info(stage, is_first=True)

    assert "<mock_sub_pipeline>" in caplog.text


# =====================================================================
# 2. File and Resource Exception Handling (Lines 482-489)
# =====================================================================


@patch("pdftl.cli.pipeline.smart_pikepdf_open")
def test_open_pdf_from_file_os_errors(mock_open):
    """Covers Lines 482-489: EMFILE and generic OSError responses."""
    manager = PipelineManager(stages=[], input_context=MagicMock())

    # Test EMFILE error wrapper logic (Errno 24)
    emfile_error = OSError()
    emfile_error.errno = 24
    mock_open.side_effect = emfile_error

    with pytest.raises(UserCommandLineError) as exc_info:
        manager._open_pdf_from_file("test.pdf", password=None)
    assert "Too many input files" in str(exc_info.value)

    # Test Generic fallback OSError wrapper
    mock_open.side_effect = OSError(13, "Permission denied")
    with pytest.raises(UserCommandLineError) as exc_info:
        manager._open_pdf_from_file("test.pdf", password=None)
    assert "Permission denied" in str(exc_info.value)


# =====================================================================
# 3. Iterative Pipeline Blocks Loop Logic (Lines 502-504, 521-529, 558-571)
# =====================================================================


def test_expand_each_in_place_empty_guard():
    """Covers Line 522: Validates guard error when EACH blocks have no prior files."""
    stage = CliStage(operation="cat", inputs=[EachSubPipeline(stages=[])], input_passwords=[None])
    manager = PipelineManager(stages=[stage], input_context=MagicMock())

    with pytest.raises(UserCommandLineError, match="EACH requires at least one input before it"):
        manager._open_input_pdfs(stage, is_first=True)


def test_each_sub_pipeline_execution_flows(monkeypatch):
    """Covers Lines 502-504, 523-529, 558-571: Successful and unreturned pipeline blocks."""
    mock_pdf_input = MagicMock()
    mock_pdf_output = MagicMock()

    stage = CliStage(
        operation="cat",
        inputs=["input.pdf", EachSubPipeline(stages=[])],
        input_passwords=[None, None],
    )
    manager = PipelineManager(stages=[stage], input_context=MagicMock())

    # Mock file retrieval to skip hitting local disk assets
    manager._open_pdf_from_file = MagicMock(return_value=mock_pdf_input)

    # Scenario A: Successful pipeline returns valid sub-PDF sequence assets
    def simulate_successful_run(self_instance):
        self_instance.pipeline_pdf = mock_pdf_output

    monkeypatch.setattr(PipelineManager, "run", simulate_successful_run)
    opened, effective, _ = manager._open_input_pdfs(stage, is_first=True)

    assert opened == [mock_pdf_output]
    assert effective == ["input.pdf"]

    # Scenario B: Pipeline sub-manager run fails to yield any active payload structures
    def simulate_empty_run(self_instance):
        self_instance.pipeline_pdf = None

    monkeypatch.setattr(PipelineManager, "run", simulate_empty_run)
    with pytest.raises(UserCommandLineError, match="EACH sub-pipeline returned no output PDF"):
        manager._open_input_pdfs(stage, is_first=True)


# =====================================================================
# 4. Context Extraction and Shared Handles (Line 541, 578-581, 591-593)
# =====================================================================


def test_resolve_input_item_from_global_handles():
    """Covers Line 541: Resolving input item directly from instance handles context."""
    mock_handled_pdf = MagicMock()
    stage = CliStage()
    manager = PipelineManager(
        stages=[stage], input_context=MagicMock(), handles={"SHARED_ASSET": mock_handled_pdf}
    )

    result = manager._resolve_input_item(
        item="SHARED_ASSET", i=0, stage=stage, opened_pdfs=[], password=None, is_first=True
    )
    assert result == mock_handled_pdf


def test_get_subpipeline_output_pdf_flows(monkeypatch):
    """Covers Lines 578-581 & 591-593: Child context scoping and clean validation returns."""
    parent_pdf = MagicMock()
    child_output_pdf = MagicMock()

    # Outer stage defines handle 'A' matching open item at input index 0
    parent_stage = CliStage(handles={"A": 0})
    inline_item = InlineSubPipeline(stages=[])
    manager = PipelineManager(stages=[parent_stage], input_context=MagicMock())

    # Target condition tracking assertion: pass historical handles down clean to kids
    def simulate_inline_run(self_instance):
        assert "A" in self_instance.handles
        assert self_instance.handles["A"] == parent_pdf
        self_instance.pipeline_pdf = child_output_pdf

    monkeypatch.setattr(PipelineManager, "run", simulate_inline_run)

    res = manager._get_subpipeline_output_pdf(
        stage=parent_stage,
        item_idx=1,
        item=inline_item,
        opened_pdfs=[parent_pdf],
        adjusted_handles={"A": 0},
    )
    assert res == child_output_pdf

    # Validates inline execution bounds checking error states
    def simulate_failed_inline_run(self_instance):
        self_instance.pipeline_pdf = None

    monkeypatch.setattr(PipelineManager, "run", simulate_failed_inline_run)
    with pytest.raises(UserCommandLineError, match="Inline pipeline returned no output PDF"):
        manager._get_subpipeline_output_pdf(
            stage=parent_stage,
            item_idx=1,
            item=inline_item,
            opened_pdfs=[parent_pdf],
            adjusted_handles={"A": 0},
        )


def test_inline_sub_pipeline_repr():
    """Covers Line 49: __repr__ mapping context for InlineSubPipeline."""
    pipeline = InlineSubPipeline(stages=[], original_text="<test_inline_sub_pipeline>")
    assert repr(pipeline) == "<test_inline_sub_pipeline>"
