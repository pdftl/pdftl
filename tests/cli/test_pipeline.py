from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.cli.pipeline import CliStage, PipelineManager
from pdftl.core.registry import registry
from pdftl.exceptions import MissingArgumentError, UserCommandLineError


# -----------------------------
# Setup dummy operations for testing
# -----------------------------
def dummy_op(*args, **kwargs):
    return "dummy_result"


registry.operations["single_op"] = {
    "function": dummy_op,
    "args": ([], {}),
    "type": "single input operation",
    "usage": "single_op input",
}

registry.operations["multi_op"] = {
    "function": dummy_op,
    "args": ([], {}),
    "type": "multi input operation",
    "usage": "multi_op input1 input2",
}


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
        manager._open_pdf_from_special_input(is_first=True)

    # '_' input when pipeline_pdf is None
    manager.pipeline_pdf = None
    with pytest.raises(UserCommandLineError):
        manager._open_pdf_from_special_input(is_first=False)


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
    registry.operations["single_op"]["args"] = ([], {}, {})
    registry.operations["single_op"]["function"] = lambda *a, **kw: "OK"
    result = manager._run_operation(stage, [])
    assert result == "OK"


# -----------------------------
# _execute_stage generator and non-generator
# -----------------------------
def test_execute_stage_non_generator(monkeypatch):
    dummy_pdf1 = MagicMock(spec=pikepdf.Pdf)
    dummy_pdf2 = MagicMock(spec=pikepdf.Pdf)
    stage = CliStage(operation="single_op", inputs=["a.pdf"])
    manager = PipelineManager(stages=[], input_context=MagicMock())

    manager._open_input_pdfs = MagicMock(return_value=[dummy_pdf1, dummy_pdf2])
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

    manager._open_input_pdfs = MagicMock(return_value=[dummy_pdf])
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

    pdfs = manager._open_input_pdfs(stage, is_first=True)
    assert pdfs == [dummy_pdf, dummy_pdf]
    assert manager.kept_id == ["id1", "id2"]

    # --- Case 2: keep_final_id ---
    stage_final = CliStage(inputs=["f1.pdf", "f2.pdf"], input_passwords=[None, None])
    stage_final.options["keep_final_id"] = True

    manager = PipelineManager(stages=[stage_final], input_context=MagicMock())

    # Even if is_first=False, keep_final_id should capture the ID of the opened PDFs
    pdfs = manager._open_input_pdfs(stage_final, is_first=False)
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
        PipelineManager, "_run_operation", lambda self, stage, opened_pdfs: DummyPdf()
    )
    with patch("pdftl.cli.pipeline.save_content") as save_mock:
        manager.run()
        assert isinstance(manager.pipeline_pdf, DummyPdf)
        save_mock.assert_called_once()
