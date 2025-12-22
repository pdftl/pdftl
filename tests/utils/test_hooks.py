import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pdftl.core.types import OpResult
from pdftl.utils import hooks


def test_get_output_path_priorities():
    """Test resolution order of output path (Lines 16-29)."""

    # 1. stage.options['output']
    stage = SimpleNamespace(options={"output": "path1"})
    assert hooks._get_output_path(stage) == "path1"

    # 1b. stage.options['output_file'] (alias)
    stage = SimpleNamespace(options={"output_file": "path2"})
    assert hooks._get_output_path(stage) == "path2"

    # 2. stage.global_options['output'] (Legacy/Architecture fix)
    stage = SimpleNamespace(options={}, global_options={"output": "path3"})
    assert hooks._get_output_path(stage) == "path3"

    # 3. stage.context['output']
    stage = SimpleNamespace(options={}, context={"output": "path4"})
    assert hooks._get_output_path(stage) == "path4"

    # 4. None
    stage = SimpleNamespace(options={})
    assert hooks._get_output_path(stage) is None


def test_text_dump_hook_scenarios(capsys, tmp_path):
    """Test text_dump_hook behavior (Lines 37-51)."""

    # 1. Failure or empty data -> Return early
    hooks.text_dump_hook(OpResult(success=False), None)
    out, _ = capsys.readouterr()
    assert out == ""

    hooks.text_dump_hook(OpResult(success=True, data=""), None)
    out, _ = capsys.readouterr()
    assert out == ""

    # 2. No output path -> Stdout
    res = OpResult(success=True, data="stdout content")
    stage = SimpleNamespace(options={})
    hooks.text_dump_hook(res, stage)
    out, _ = capsys.readouterr()
    assert "stdout content" in out

    # 3. Output path -> File
    output_file = tmp_path / "output.txt"
    stage = SimpleNamespace(options={"output": str(output_file)})
    hooks.text_dump_hook(res, stage)

    assert output_file.exists()
    assert output_file.read_text().strip() == "stdout content"


def test_json_dump_hook_scenarios(capsys, tmp_path):
    """Test json_dump_hook behavior (Lines 59-72)."""

    data = {"key": "value"}
    res = OpResult(success=True, data=data)

    # 1. Stdout
    stage = SimpleNamespace(options={})
    hooks.json_dump_hook(res, stage)
    out, _ = capsys.readouterr()
    assert '"key": "value"' in out

    # 2. File
    output_file = tmp_path / "output.json"
    stage = SimpleNamespace(options={"output": str(output_file)})
    hooks.json_dump_hook(res, stage)

    assert output_file.exists()
    assert '"key": "value"' in output_file.read_text()

    # 3. Failure -> No op
    hooks.json_dump_hook(OpResult(success=False), None)
    # Implicit pass if no error raised
