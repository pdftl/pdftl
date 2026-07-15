# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/server/server_pipeline.py

"""Multi-stage pipeline execution support for the pdftl HTTP server."""

import io
import json
import logging
import os
import tempfile
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pikepdf

from pdftl.cli.pipeline import CliStage, PipelineManager
from pdftl.core.registry import registry
from pdftl.exceptions import UserCommandLineError

logger = logging.getLogger(__name__)

# Operations that must never be reachable as a pipeline step: 'server' for
# the same recursion/resource-exhaustion reason single-op dispatch blocks
# it, and 'pipeline' itself since it isn't a registered operation.
_BLOCKED_STEP_OPERATIONS = {"server", "pipeline"}


class ApiInputContext:
    """Non-interactive stand-in for the CLI's input context.

    The pipeline manager only calls ``get_input`` when a stage is missing
    a required filename (e.g. the ``PROMPT`` sentinel). Over the API there
    is no terminal to prompt, so any such request is a client error rather
    than something we can resolve.
    """

    is_api = True

    def get_input(self, prompt_text: str, completer: Any = None) -> str:
        raise UserCommandLineError(
            f"Interactive input was requested ({prompt_text!r}) but the API "
            "cannot prompt a user; provide all inputs explicitly."
        )


def parse_pipeline_steps(args_raw: str) -> list[dict[str, Any]]:
    """Parse the ``args`` form field for a pipeline request into step dicts.

    Expects a JSON array of objects, each with an ``operation`` key and
    optional ``args``, ``inputs``, and ``options`` keys, e.g.::

        [
          {"operation": "cat", "args": ["A", "B"]},
          {"operation": "rotate", "args": ["right"]}
        ]

    ``options`` is only meaningful on the final step, where it configures
    output encryption/linearization for the returned PDF.
    """
    try:
        steps = json.loads(args_raw) if args_raw else []
    except json.JSONDecodeError as exc:
        raise UserCommandLineError(f"Pipeline 'args' field is not valid JSON: {exc}") from exc

    if not isinstance(steps, list) or not steps:
        raise UserCommandLineError(
            "Pipeline 'args' field must be a non-empty JSON array of step objects."
        )

    for step in steps:
        _validate_step(step)

    return steps


def _validate_step(step: Any) -> None:
    if not isinstance(step, dict) or "operation" not in step:
        raise UserCommandLineError("Each pipeline step must be an object with an 'operation' key.")

    operation = step["operation"]
    if operation in _BLOCKED_STEP_OPERATIONS:
        raise UserCommandLineError(
            f"The '{operation}' operation cannot be used as a pipeline step."
        )
    if operation not in registry.operations:
        raise UserCommandLineError(f"Operation '{operation}' is not supported.")

    if "args" in step and not isinstance(step["args"], list):
        raise UserCommandLineError("Pipeline step 'args' must be a list of strings.")

    if "inputs" in step and not isinstance(step["inputs"], list):
        raise UserCommandLineError("Pipeline step 'inputs' must be a list of handle names.")

    if "options" in step and not isinstance(step["options"], dict):
        raise UserCommandLineError("Pipeline step 'options' must be an object.")


def build_pipeline_stages(
    steps: list[dict[str, Any]], alias_names: list[str], final_output_path: str
) -> list[CliStage]:
    """Convert parsed step dicts into `CliStage` objects for `PipelineManager`.

    The first stage defaults to consuming every uploaded file handle, in
    upload order, unless it explicitly specifies its own ``inputs``. Later
    stages default to the implicit pipeline chain (empty ``inputs``), which
    feeds forward the previous stage's output exactly like ``---`` on the
    CLI.
    """
    stages = []
    last_index = len(steps) - 1

    for i, step in enumerate(steps):
        args = list(step.get("args", []))

        if "inputs" in step:
            inputs = list(step["inputs"])
        elif i == 0:
            inputs = list(alias_names)
        else:
            inputs = ["_"]  # implicit chain: consume previous stage's output

        # Map each handle name to its position within *this stage's* inputs list,
        # mirroring what the CLI parser does for `A=file.pdf` syntax. Without this,
        # operations like `cat` that reference handles in their args (e.g. "A", "B")
        # have no way to resolve them and instead try to parse them as page specs.
        handles = {name: idx for idx, name in enumerate(inputs) if name in alias_names}

        options = dict(step.get("options", {}))
        if "output" in options:
            # not allowed for security reasons
            raise UserCommandLineError("The 'output' option is forbidden in pipeline steps.")

        if i == last_index:
            op_data = registry.operations.get(step["operation"], {})
            op_requires_output = " output " in op_data.get("usage", "")
            if step["operation"] == "filter" or op_requires_output:
                options["output"] = final_output_path

        stages.append(
            CliStage(
                operation=step["operation"],
                inputs=inputs,
                input_passwords=[None] * len(inputs),
                handles=handles,
                operation_args=args,
                options=options,
            )
        )

    return stages


def run_pipeline(
    steps: list[dict[str, Any]],
    opened_pdfs: list["pikepdf.Pdf"],
    aliases: dict[str, int],
) -> tuple[bytes | None, dict[str, Any]]:
    """Execute a parsed pipeline against already-opened input PDFs.

    Uploaded-file handles (e.g. ``A``, ``B``) are exposed to every stage via
    `PipelineManager`'s top-level handle map, so steps can reference them
    directly (``{"operation": "cat", "args": ["A", "B"]}``) without needing
    per-stage handle bookkeeping. Returns the final output as raw PDF
    bytes.

    If the final stage's operation requires 'output <file>' on the CLI,
    that save happens exactly once, inside PipelineManager.run(), to a
    unique per-request temp file; we then read those bytes back rather
    than calling .save() a second time on the same pikepdf.Pdf object
    (pikepdf.Pdf objects are not safe to save more than once). If no
    stage wrote an output file, pipeline_pdf is saved here, exactly once.

    If the final stage's operation is flagged skip_pipeline_save (i.e. it
    doesn't produce a PDF -- e.g. dump_data, dump_bookmarks), the final
    stage's OpResult.data is serialized instead, using the same
    (bytes|None, meta) shape single-operation execution already returns.
    """
    final_op_is_data_producing = False
    if steps:
        final_op = steps[-1].get("operation")
        op_entry = registry.operations.get(final_op, {})
        final_op_is_data_producing = op_entry.get("skip_pipeline_save", False)

    handles = {name: opened_pdfs[idx] for name, idx in aliases.items()}

    fd, tmp_path = tempfile.mkstemp(suffix=".pdf", prefix="pdftl_server_")
    os.close(fd)
    try:
        stages = build_pipeline_stages(steps, list(aliases.keys()), tmp_path)

        manager = PipelineManager(
            stages=stages,
            input_context=ApiInputContext(),
            handles=handles,
        )
        manager.run()

        if final_op_is_data_producing:
            # Deferred import: subprocess_workers imports run_pipeline from
            # this module at load time, so importing it back at module
            # level here would create a circular import.
            from pdftl.server.subprocess_workers import _serialize_operation_result

            if not manager.results:
                raise UserCommandLineError(
                    f"The '{final_op}' operation completed but produced no result."
                )
            return _serialize_operation_result(manager.results[-1])

        last_options = stages[-1].options if stages else {}
        if last_options.get("output") == tmp_path and os.path.getsize(tmp_path) > 0:
            # CLI already saved the final PDF to disk exactly once, inside
            # PipelineManager.run(). Read those bytes back rather than
            # calling .save() again on the same pikepdf.Pdf object.
            with open(tmp_path, "rb") as f:
                return f.read(), {"kind": "pdf"}

        if manager.pipeline_pdf is None:
            raise UserCommandLineError("Pipeline completed but produced no output PDF.")

        buf = io.BytesIO()
        manager.pipeline_pdf.save(buf)
        return buf.getvalue(), {"kind": "pdf"}
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
