# src/pdftl/utils/hooks.py

import json
import sys
from typing import Any

from pdftl.core.types import OpResult
from pdftl.utils.io_helpers import smart_open_output


def _get_output_path(stage):
    """
    Helper to resolve the output path from a stage.
    Prioritizes stage-specific options, then falls back to global options
    if the stage object has access to them.
    """
    # 1. Check specific stage options (e.g., from 'output_file' arg mapping)
    path = stage.options.get("output") or stage.options.get("output_file")
    if path:
        return path

    # 2. Check for global options attached to the stage (Architecture Fix)
    # If the parser/pipeline attaches global_options to the stage, use it.
    if hasattr(stage, "global_options") and stage.global_options:
        return stage.global_options.get("output")

    # 3. Check for a context object
    if hasattr(stage, "context") and stage.context:
        return stage.context.get("output")

    return None


def text_dump_hook(result, stage):
    """
    Hook for text-based commands (dump_text, list_files).
    Writes result.data (str) to the configured output file or stdout.
    """
    if not result.success or not result.data:
        return

    output_path = _get_output_path(stage)

    # If no output file is found, default to stdout (Legacy behavior)
    if not output_path:
        print(result.data)
        return

    with smart_open_output(output_path) as f:
        f.write(str(result.data))
        # Ensure trailing newline for terminal niceness
        if not str(result.data).endswith("\n"):
            f.write("\n")


def json_dump_hook(result, stage):
    """
    Hook for data-structure commands (dump_data, dump_layers).
    Writes result.data (dict/list) as formatted JSON.
    """
    if not result.success or result.data is None:
        return

    output_path = _get_output_path(stage)

    # JSON commands usually print to stdout if no file is given
    f = smart_open_output(output_path) if output_path else sys.stdout

    try:
        json.dump(result.data, f, indent=2, default=str)
        f.write("\n")
    finally:
        if output_path:
            f.close()


def from_result_meta(result: OpResult, attrib: str) -> Any:
    """Utility to get a value from result.meta or error out if impossible"""
    assert result.meta is not None
    return result.meta.get(attrib)


def str_from_result_meta(result: OpResult, attrib: str) -> str:
    """Utility to get a value from result.meta, asserting that it is a string"""
    assert isinstance(ret := from_result_meta(result, attrib), str)
    return ret
