import types
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch

import pytest

from pdftl.core.core_types import OpResult
from pdftl.utils import hooks
from pdftl.utils.hooks import (
    consume_output_option,
    from_result_meta,
    text_dump_hook,
)


def test_get_output_path_priorities():
    """Test resolution order of output path (Lines 16-29)."""

    # 1. stage.options['output']
    stage = SimpleNamespace(options={"output": "path1"})
    assert hooks._get_output_path(stage) == "path1"

    # 4. None
    stage = SimpleNamespace(options={})
    assert hooks._get_output_path(stage) is None


def test_text_dump_hook_scenarios(capsys, tmp_path):
    """Test text_dump_hook behavior (Lines 37-51)."""

    stage = SimpleNamespace(options={})
    # 1. Failure or empty data -> Return early
    hooks.text_dump_hook(OpResult(success=False), stage, None)
    out, _ = capsys.readouterr()
    assert out == ""

    hooks.text_dump_hook(OpResult(success=True, data=""), stage, None)
    out, _ = capsys.readouterr()
    assert out == ""

    # 2. No output path -> Stdout
    res = OpResult(success=True, data="stdout content")
    stage = SimpleNamespace(options={})
    hooks.text_dump_hook(res, stage, None)
    out, _ = capsys.readouterr()
    assert "stdout content" in out

    # 3. Output path -> File
    output_file = tmp_path / "output.txt"
    stage = SimpleNamespace(options={"output": str(output_file)})
    hooks.text_dump_hook(res, stage, None)

    assert output_file.exists()
    assert output_file.read_text().strip() == "stdout content"


def test_text_dump_hook_early_returns():
    """Test that text_dump_hook returns early on failure or empty data."""
    stage = MagicMock()

    # Case 1: Failure
    res_fail = OpResult(success=False, data="Some error")
    # Should simply return None, not print, not write file
    assert text_dump_hook(res_fail, stage, None) is None

    # Case 2: No Data
    res_no_data = OpResult(success=True, data=None)
    assert text_dump_hook(res_no_data, stage, None) is None


def test_consume_output_option_no_options_attr():
    """
    Covers hooks.py lines 17-19 (defensive check):
    Ensures consume_output_option doesn't crash if stage object has no 'options' attribute.
    """
    stage = types.SimpleNamespace()
    # Should do nothing and not raise AttributeError
    consume_output_option(stage)


def test_text_dump_hook_appends_newline():
    """
    Covers hooks.py line 41-42:
    Ensures a newline is appended if the data string doesn't end with one.
    """
    result = OpResult(success=True, data="Hello World")
    stage = types.SimpleNamespace(options={"output": "out.txt"})

    m = mock_open()
    with patch("pdftl.utils.hooks.smart_open_maybe_dash", m):
        text_dump_hook(result, stage, None)

    handle = m()
    # Check that we wrote the data AND a newline
    handle.write.assert_any_call("Hello World")
    handle.write.assert_any_call("\n")


def test_from_result_meta_none():
    """
    Covers hooks.py line 66-67:
    Ensures ValueError is raised if result.meta is None.
    """
    result = OpResult(success=True, meta=None)
    with pytest.raises(ValueError, match="result.meta is None"):
        from_result_meta(result, "some_attrib")
