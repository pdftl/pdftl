# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/server/subprocess_workers.py

"""Functions that only ever run inside a spawned child process, isolating
a single operation or pipeline request from the parent HTTP server thread.

Everything here must be picklable-by-reference (module-level, no closures
over handler state) since `multiprocessing.get_context("spawn")` re-imports
this module fresh in the child and hands these functions off by reference.
"""

import io as _io
import os
import logging
import multiprocessing
from collections.abc import Callable
from typing import Any, TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.server.server_pipeline import run_pipeline
from pdftl.exceptions import UserCommandLineError

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _serialize_operation_data(data: Any, meta: dict[str, Any]) -> dict[str, Any]:
    """Flattens a non-PDF OpResult.data value (plus its json/text-rendering
    meta flags) into the picklable {"kind": ..., ...} shape used for the
    subprocess -> parent handoff."""
    json_output = meta.get("json_output", False)
    escape_xml = meta.get("escape_xml", True)
    extra_info = meta.get("extra_info", False)

    if json_output and hasattr(data, "to_dict"):
        return {"kind": "data", "data": data.to_dict()}

    if type(data).__name__ == "PdfInfo" and not json_output:
        from pdftl.info.output_info import write_info

        text_buf = _io.StringIO()

        def _writer(text: str) -> None:
            text_buf.write(text + "\n")

        write_info(_writer, data, escape_xml=escape_xml, extra_info=extra_info)
        return {"kind": "text", "text": text_buf.getvalue()}

    if isinstance(data, (dict, list, str, int, float, bool)) or data is None:
        return {"kind": "data", "data": data}

    return {"kind": "data", "data": str(data)}


def _serialize_operation_result(result: Any) -> tuple[bytes | None, dict[str, Any]]:
    """Flattens a live OpResult (data or pdf) into the (bytes, meta) shape
    that crosses the subprocess boundary."""
    if hasattr(result, "data") and result.data is not None:
        meta = result.meta if (hasattr(result, "meta") and isinstance(result.meta, dict)) else {}
        return None, _serialize_operation_data(result.data, meta)
    if hasattr(result, "pdf") and result.pdf is not None:
        buf = _io.BytesIO()
        result.pdf.save(buf)
        return buf.getvalue(), {"kind": "pdf"}
    return None, {"kind": "empty"}


def _raise_on_file_arg(operation_args, allowed_paths):
    for arg in operation_args:
        # Intercept absolute paths, path traversals, or raw files on disk
        if (
            os.path.isabs(arg)
            or "../" in arg
            or "..\\" in arg
            or (os.path.exists(arg) and os.path.isfile(arg))
        ):
            # Deny access if they do not match a valid backend temporary upload spool
            if arg not in allowed_paths:
                raise UserCommandLineError(
                    f"Access to local path '{arg}' is forbidden over the REST API."
                )


def _run_single_operation_in_subprocess(
    operation: str,
    operation_args: list[str],
    raw_file_paths: list[dict[str, Any]],
) -> tuple[bytes | None, dict[str, Any]]:
    """Runs entirely inside an isolated child process: reopens PDFs from
    on-disk paths, enforces a strict filesystem sandbox to prevent local file inclusion (LFI),
    executes the operation, and serializes the result back."""
    import pikepdf
    from pdftl.core.executor import run_operation
    from pdftl.utils.arg_helpers import expand_shorthand_args

    allowed_paths = {f["path"] for f in raw_file_paths}

    _raise_on_file_arg(operation_args, allowed_paths)

    opened_pdfs = [pikepdf.open(f["path"]) for f in raw_file_paths]
    aliases = {f["name"]: idx for idx, f in enumerate(raw_file_paths) if f["name"]}
    try:
        args_expanded = expand_shorthand_args(operation_args)
        call_context = {
            c.OPERATION_NAME: operation,
            c.INPUTS: [f["filename"] for f in raw_file_paths],
            c.OPENED_PDFS: opened_pdfs,
            c.INPUT_FILENAME: raw_file_paths[0]["filename"] if raw_file_paths else None,
            c.INPUT_PASSWORD: None,
            c.INPUT_PDF: opened_pdfs[0] if opened_pdfs else None,
            c.OPERATION_ARGS: operation_args,
            c.OPERATION_ARGS_EXPANDED: args_expanded,
            c.ALIASES: aliases,
            c.OVERLAY_PDF: operation_args[0] if operation_args else None,
            c.OUTPUT: None,
            c.OUTPUT_PATTERN: "pg_%04d.pdf",
            c.GET_INPUT: None,
        }
        result = run_operation(operation, call_context)
        return _serialize_operation_result(result)
    finally:
        import pikepdf as _pikepdf

        for pdf_obj in opened_pdfs:
            try:
                pdf_obj.close()
            except (AttributeError, _pikepdf.PdfError, OSError) as exc:
                logger.debug("Failed to close pdf_obj during subprocess cleanup: %s", exc)


def _run_pipeline_in_subprocess(
    steps: list[dict[str, Any]],
    raw_file_paths: list[dict[str, Any]],
) -> bytes:
    """Pipeline counterpart of _run_single_operation_in_subprocess: applies
    the same LFI filesystem sandbox validation for all stages within a
    pipelined request before executing."""
    import pikepdf

    allowed_paths = {f["path"] for f in raw_file_paths}

    for step in steps:
        if "args" in step:
            _raise_on_file_arg(step["args"], allowed_paths)

    opened_pdfs = [pikepdf.open(f["path"]) for f in raw_file_paths]
    aliases = {f["name"]: idx for idx, f in enumerate(raw_file_paths) if f["name"]}
    try:
        return run_pipeline(steps, opened_pdfs, aliases)
    finally:
        import pikepdf as _pikepdf

        for pdf_obj in opened_pdfs:
            try:
                pdf_obj.close()
            except (AttributeError, _pikepdf.PdfError, OSError) as exc:
                logger.debug("Failed to close pdf_obj during subprocess cleanup: %s", exc)


def _subprocess_worker_entrypoint(
    q: "multiprocessing.Queue", fn: Callable[..., Any], args: tuple
) -> None:
    """Module-level target for the child process started in the handler's
    _run_with_timeout."""
    try:
        q.put(("ok", fn(*args)))
    except Exception as exc:  # noqa: BLE001 - forward any exception to parent
        q.put(("err", exc))
