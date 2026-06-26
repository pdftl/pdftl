# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/test_dump_tags.py

"""Integration tests for the dump_tags operational module entry-point."""

from __future__ import annotations

import contextlib
import io
from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.core.core_types import OpResult
from pdftl.operations.dump_tags import _parse_args, dump_tags, dump_tags_cli_hook


def test_parse_args_defaults() -> None:
    """Verify default configurations set expected parameters."""
    args = []
    mode, page_specs, json_out, annotate, show_streams = _parse_args(args, "dump_tags")

    assert mode == "reading_order"
    assert page_specs == []
    assert json_out is False
    assert annotate is False
    assert show_streams is True


def test_parse_args_explicit() -> None:
    """Check specific parameters override mode choices correctly."""
    args = ["tree", "json", "1-5", "streams=false"]
    mode, page_specs, json_out, annotate, show_streams = _parse_args(args, "dump_tags")

    assert mode == "tree"
    assert json_out is True
    assert page_specs == ["1-5"]
    assert show_streams is False


def test_parse_args_annotate() -> None:
    """Verify the annotate flag token is parsed correctly."""
    args = ["annotate"]
    mode, page_specs, json_out, annotate, show_streams = _parse_args(args, "dump_tags")

    assert mode == "reading_order"
    assert annotate is True


@patch("pdftl.operations.dump_tags._run_tree")
def test_dump_tags_tree_mode(mock_run_tree) -> None:
    """Test orchestration triggers expected module dependencies on tree execution."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.pages = [MagicMock()]
    mock_run_tree.return_value = [{"tag": "Document"}]

    res = dump_tags("dump_tags", mock_pdf, ["tree"])

    assert res.success is True
    assert res.data == [{"tag": "Document"}]
    assert res.meta["mode"] == "tree"


@patch("pdftl.operations.dump_tags._reading_order_lines")
def test_dump_tags_reading_order_mode(mock_reading) -> None:
    """Check reading order workflows request target lines."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.pages = [MagicMock()]
    mock_reading.return_value = ["[Document]", "  [P]"]

    res = dump_tags("dump_tags", mock_pdf, ["reading_order"])

    assert res.success is True
    assert res.data == ["[Document]", "  [P]"]
    assert res.meta["mode"] == "reading_order"


@patch("pdftl.operations.dump_tags._run_issues")
@patch("pdftl.operations.dump_tags.page_numbers_matching_page_specs")
def test_dump_tags_issues_mode_and_specs(mock_matching, mock_run_issues) -> None:
    """Check explicit page specs resolution and issues routing logic."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.pages = [MagicMock(), MagicMock()]
    mock_matching.return_value = [1]
    mock_run_issues.return_value = [{"severity": "error", "code": "TEST", "message": "msg"}]

    res = dump_tags("dump_tags", mock_pdf, ["issues", "1"])

    assert res.success is True
    assert res.data == [{"severity": "error", "code": "TEST", "message": "msg"}]
    mock_matching.assert_called_once_with(["1"], 2)


def test_dump_tags_invalid_mode() -> None:
    """Ensure an unmanaged mode triggers a proper ValueError exception."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.pages = [MagicMock()]

    with patch("pdftl.operations.dump_tags._parse_args") as mock_parse:
        mock_parse.return_value = ("invalid_mode", [], False, False, True)
        with pytest.raises(ValueError, match="Unknown dump_tags mode"):
            dump_tags("dump_tags", mock_pdf, ["invalid_mode"])


def test_dump_tags_cli_hook_reading_order() -> None:
    """Verify hooks serialize target structural logs cleanly to files."""
    result = OpResult(
        success=True,
        data=["Line 1", "Line 2"],
        meta={"mode": "reading_order"},
    )

    output = io.StringIO()

    @contextlib.contextmanager
    def mock_open(*args, **kwargs):
        yield output

    with patch(
        "pdftl.operations.dump_tags.smart_open_maybe_dash",
        side_effect=mock_open,
    ):
        dump_tags_cli_hook(result, stage=None, _pipeline=None)

    assert output.getvalue() == "Line 1\nLine 2\n"


def test_dump_tags_cli_hook_tree_json() -> None:
    """Check CLI Hook dumps target structures in JSON on request."""
    result = OpResult(
        success=True,
        data={"tag": "Document", "children": []},
        meta={"mode": "tree", "json_output": True},
    )

    output = io.StringIO()

    @contextlib.contextmanager
    def mock_open(*args, **kwargs):
        yield output

    with patch(
        "pdftl.operations.dump_tags.smart_open_maybe_dash",
        side_effect=mock_open,
    ):
        dump_tags_cli_hook(result, stage=None, _pipeline=None)

    assert "Document" in output.getvalue()


def test_dump_tags_cli_hook_tree_yaml_success() -> None:
    """Verify standard tree mode attempts YAML serialization using dependencies."""
    result = OpResult(
        success=True,
        data={"tag": "Document"},
        meta={"mode": "tree", "json_output": False},
    )
    output = io.StringIO()

    @contextlib.contextmanager
    def mock_open(*args, **kwargs):
        yield output

    with (
        patch("pdftl.operations.dump_tags.smart_open_maybe_dash", side_effect=mock_open),
        patch("pdftl.utils.dependencies.ensure_dependencies") as mock_ensure,
        patch("yaml.dump") as mock_yaml_dump,
    ):
        dump_tags_cli_hook(result, stage=None, _pipeline=None)
        mock_ensure.assert_called_once()
        mock_yaml_dump.assert_called_once()


def test_dump_tags_cli_hook_tree_yaml_fallback() -> None:
    """Verify tree mode cleanly drops back to JSON formatting when YAML components fail."""
    result = OpResult(
        success=True,
        data={"tag": "Document"},
        meta={"mode": "tree", "json_output": False},
    )
    output = io.StringIO()

    @contextlib.contextmanager
    def mock_open(*args, **kwargs):
        yield output

    with (
        patch("pdftl.operations.dump_tags.smart_open_maybe_dash", side_effect=mock_open),
        patch("pdftl.utils.dependencies.ensure_dependencies", side_effect=ImportError),
    ):
        dump_tags_cli_hook(result, stage=None, _pipeline=None)

        assert "structure_tree" in output.getvalue()


def test_dump_tags_cli_hook_issues_json() -> None:
    """Verify issues list converts uniformly to structural JSON streams on demand."""
    result = OpResult(
        success=True,
        data=[{"severity": "error", "code": "ERR", "message": "X"}],
        meta={"mode": "issues", "json_output": True},
    )
    output = io.StringIO()

    @contextlib.contextmanager
    def mock_open(*args, **kwargs):
        yield output

    with patch("pdftl.operations.dump_tags.smart_open_maybe_dash", side_effect=mock_open):
        dump_tags_cli_hook(result, stage=None, _pipeline=None)

    assert "issues" in output.getvalue()


def test_dump_tags_cli_hook_issues_plaintext_empty() -> None:
    """Verify structural integrity confirmation text prints when no issues are found."""
    result = OpResult(
        success=True,
        data=[],
        meta={"mode": "issues", "json_output": False},
    )
    output = io.StringIO()

    @contextlib.contextmanager
    def mock_open(*args, **kwargs):
        yield output

    with patch("pdftl.operations.dump_tags.smart_open_maybe_dash", side_effect=mock_open):
        dump_tags_cli_hook(result, stage=None, _pipeline=None)

    assert "No structural issues found." in output.getvalue()


def test_dump_tags_cli_hook_issues_plaintext_populated() -> None:
    """Verify severe accessibility problem messages are ordered, stylized, and quantified properly."""
    result = OpResult(
        success=True,
        data=[
            {
                "severity": "error",
                "code": "ERR_CODE",
                "message": "Error encountered",
                "page": 1,
                "tag": "H1",
            },
            {"severity": "warning", "code": "WARN_CODE", "message": "Warning encountered"},
            {"severity": "info", "code": "INFO_CODE", "message": "Info encountered", "page": 2},
        ],
        meta={"mode": "issues", "json_output": False},
    )
    output = io.StringIO()

    @contextlib.contextmanager
    def mock_open(*args, **kwargs):
        yield output

    with patch("pdftl.operations.dump_tags.smart_open_maybe_dash", side_effect=mock_open):
        dump_tags_cli_hook(result, stage=None, _pipeline=None)

    val = output.getvalue()
    assert "[✗] [ERR_CODE] [page 1] <H1> Error encountered" in val
    assert "[!] [WARN_CODE] Warning encountered" in val
    assert "[·] [INFO_CODE] [page 2] Info encountered" in val
    assert "3 issue(s): 1 error(s), 1 warning(s)" in val
