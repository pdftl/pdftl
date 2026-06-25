# tests/operations/test_tag.py

import subprocess
from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.core.core_types import OpResult
from pdftl.exceptions import OperationError
from pdftl.operations.tag import (
    _jar_path,
    _load_and_mark_pdf,
    _prepare_args,
    _run_java_pipeline,
    tag_pdf,
)


# ==============================================================================
# 1. UNIT TESTS FOR HELPER FUNCTIONS
# ==============================================================================


def test_jar_path_success():
    """Verify that _jar_path resolves correctly when the package is installed."""
    with (
        patch("importlib.resources.files") as mock_files,
        patch("importlib.resources.as_file") as mock_as_file,
    ):
        mock_ref = MagicMock()
        mock_files.return_value.joinpath.return_value = mock_ref
        mock_as_file.return_value.__enter__.return_value = "fake_path.jar"

        with _jar_path() as jar:
            assert jar == "fake_path.jar"

        mock_files.assert_called_once_with("opendataloader_pdf")
        mock_files.return_value.joinpath.assert_called_once_with(
            "jar", "opendataloader-pdf-cli.jar"
        )


def test_jar_path_raises_operation_error():
    """Verify that _jar_path translates extraction errors into OperationError."""
    with patch("importlib.resources.files", side_effect=FileNotFoundError):
        with pytest.raises(OperationError) as exc_info:
            with _jar_path():
                pass
        assert "Could not resolve OpenDataLoader JAR path" in str(exc_info.value)


def test_prepare_args_no_options():
    """Verify _prepare_args returns an empty list when no extra arguments are given."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    assert _prepare_args(mock_pdf, []) == []


def test_prepare_args_with_valid_key_vals():
    """Verify standard mapping of keyval pairs into structured CLI flags."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    op_args = ["table_method=cluster", "hybrid=docling-fast"]

    expected = ["--table-method", "cluster", "--hybrid", "docling-fast"]
    assert _prepare_args(mock_pdf, op_args) == expected


def test_prepare_args_with_bare_page_specifiers():
    """Verify that unmapped values are interpreted as page ranges and validated."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.pages = [MagicMock() for _ in range(10)]  # 10 pages total

    op_args = ["1-3", "5"]
    expected = ["--pages", "1,2,3,5"]  # Match 1-indexed human strings from parser
    assert _prepare_args(mock_pdf, op_args) == expected


def test_run_java_pipeline_success():
    """Verify standard invocation structure of subprocess.run inside the pipeline."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)

        _run_java_pipeline("fake.jar", "in.pdf", "out_dir", ["--extra"])

        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        cmd = args[0]
        assert "java" in cmd
        assert "-Djava.io.tmpdir=out_dir" in cmd
        assert "in.pdf" in cmd


def test_run_java_pipeline_execution_failure():
    """Verify SubprocessError maps cleanly onto an OperationError wrapper."""
    with patch("subprocess.run", side_effect=subprocess.SubprocessError("OS error")):
        with pytest.raises(OperationError) as exc_info:
            _run_java_pipeline("fake.jar", "in.pdf", "out_dir", [])
        assert "Failed to execute the OpenDataLoader subprocess" in str(exc_info.value)


def test_run_java_pipeline_non_zero_exit_with_logs():
    """Verify parsing of standard error output stream logs on failed processing execution."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1, stdout=b"Fatal error details\nSecond line of logs"
        )
        with pytest.raises(OperationError) as exc_info:
            _run_java_pipeline("fake.jar", "in.pdf", "out_dir", [])
        assert "OpenDataLoader auto-tagging failed with exit code 1" in str(exc_info.value)
        assert "Error details: Fatal error details" in str(exc_info.value)


def test_run_java_pipeline_non_zero_exit_no_logs():
    """Verify alternative placeholder logging if stdout is completely empty on crash."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=2, stdout=b"")
        with pytest.raises(OperationError) as exc_info:
            _run_java_pipeline("fake.jar", "in.pdf", "out_dir", [])
        assert "Error details: No logs available." in str(exc_info.value)


def test_load_and_mark_pdf_not_found():
    """Verify exception raising if target expected output base name does not resolve."""
    with patch("os.listdir", return_value=["garbage.pdf", "tmp_pdf_file123.pdf"]):
        with pytest.raises(OperationError) as exc_info:
            _load_and_mark_pdf("/fake/dir", "expected_base.pdf")
        assert "the tagged PDF was not found" in str(exc_info.value)


def test_load_and_mark_pdf_creates_markinfo_if_missing():
    """Verify /MarkInfo node injects seamlessly if completely missing from the dictionary."""
    with (
        patch("os.listdir", return_value=["testfile_tagged.pdf"]),
        patch("pikepdf.Pdf.open") as mock_open,
    ):
        mock_pdf = MagicMock()
        mock_pdf.Root = {}  # Empty Root dictionary
        mock_open.return_value = mock_pdf

        result_pdf = _load_and_mark_pdf("/fake/dir", "testfile.pdf")

        assert "/MarkInfo" in result_pdf.Root
        assert result_pdf.Root["/MarkInfo"]["/Marked"] is True


def test_load_and_mark_pdf_updates_markinfo_if_exists():
    """Verify /MarkInfo value is driven to True even if the root node already exists."""
    with (
        patch("os.listdir", return_value=["testfile.pdf"]),
        patch("pikepdf.Pdf.open") as mock_open,
    ):
        mock_pdf = MagicMock()
        mock_pdf.Root = {"/MarkInfo": {"/Marked": False}}  # Use a standard mutable dict
        mock_open.return_value = mock_pdf

        result_pdf = _load_and_mark_pdf("/fake/dir", "testfile.pdf")

        assert result_pdf.Root["/MarkInfo"]["/Marked"] is True


# ==============================================================================
# 2. INTEGRATION TESTS FOR ORCHESTRATOR (tag_pdf)
# ==============================================================================


@patch("pdftl.operations.tag.ensure_dependencies")
@patch("pdftl.operations.tag._prepare_args")
@patch("pdftl.operations.tag._jar_path")
@patch("pdftl.operations.tag._run_java_pipeline")
@patch("pdftl.operations.tag._load_and_mark_pdf")
@patch("tempfile.NamedTemporaryFile")
@patch("tempfile.mkdtemp")
@patch("os.path.exists")
@patch("os.unlink")
@patch("shutil.rmtree")
def test_tag_pdf_full_success_flow(
    mock_rmtree,
    mock_unlink,
    mock_exists,
    mock_mkdtemp,
    mock_named_tmp,
    mock_load_mark,
    mock_run_java,
    mock_jar,
    mock_prep,
    mock_deps,
):
    """Verify the entire end-to-end operational lifetime of tag_pdf orchestrator."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_op_result_pdf = MagicMock(spec=pikepdf.Pdf)

    mock_prep.return_value = ["--mapped-flag"]

    mock_jar_ctx = MagicMock()
    mock_jar_ctx.__enter__.return_value = "actual.jar"
    mock_jar.return_value = mock_jar_ctx

    mock_tmp_in = MagicMock()
    mock_tmp_in.name = "/tmp/in_file.pdf"
    mock_named_tmp.return_value.__enter__.return_value = mock_tmp_in
    mock_mkdtemp.return_value = "/tmp/out_dir"

    mock_load_mark.return_value = mock_op_result_pdf
    mock_exists.return_value = True

    res = tag_pdf(mock_pdf, ["args"])

    mock_deps.assert_called_once_with(
        feature_name="tag",
        dependencies={"opendataloader_pdf": "opendataloader-pdf"},
        extra_tag="tag",
        required_executables=["java"],
    )
    mock_pdf.save.assert_called_once_with("/tmp/in_file.pdf")
    mock_run_java.assert_called_once_with(
        "actual.jar", "/tmp/in_file.pdf", "/tmp/out_dir", ["--mapped-flag"]
    )
    mock_load_mark.assert_called_once_with("/tmp/out_dir", "in_file.pdf")

    assert isinstance(res, OpResult)
    assert res.success is True
    assert res.pdf == mock_op_result_pdf

    mock_unlink.assert_called_once_with("/tmp/in_file.pdf")
    mock_rmtree.assert_called_once_with("/tmp/out_dir")


@patch("pdftl.operations.tag.ensure_dependencies")
@patch("pdftl.operations.tag._prepare_args")
@patch("pdftl.operations.tag._jar_path")
@patch("pdftl.operations.tag._run_java_pipeline")
@patch("tempfile.NamedTemporaryFile")
@patch("tempfile.mkdtemp")
@patch("os.path.exists")
@patch("os.unlink")
@patch("shutil.rmtree")
def test_tag_pdf_lifecycle_cleanup_on_exception(
    mock_rmtree,
    mock_unlink,
    mock_exists,
    mock_mkdtemp,
    mock_named_tmp,
    mock_run_java,
    mock_jar,
    mock_prep,
    mock_deps,
):
    """Verify that cleanup steps are fully triggered even if the pipeline crashes."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)

    mock_jar_ctx = MagicMock()
    mock_jar_ctx.__enter__.return_value = "actual.jar"
    mock_jar.return_value = mock_jar_ctx

    mock_tmp_in = MagicMock()
    mock_tmp_in.name = "/tmp/error_in.pdf"
    mock_named_tmp.return_value.__enter__.return_value = mock_tmp_in
    mock_mkdtemp.return_value = "/tmp/error_out_dir"

    mock_run_java.side_effect = OperationError("Java execution collapsed")
    mock_exists.return_value = True

    with pytest.raises(OperationError):
        tag_pdf(mock_pdf, [])

    mock_unlink.assert_called_once_with("/tmp/error_in.pdf")
    mock_rmtree.assert_called_once_with("/tmp/error_out_dir")
