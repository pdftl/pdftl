# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/cli/pipeline.py

"""Manage a pipeline of operations"""

import io
import logging
import sys
import types
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)
from typing import Optional

import pikepdf

logger = logging.getLogger(__name__)
from pdftl.cli.whoami import WHOAMI
from pdftl.core.registry import registry
from pdftl.exceptions import MissingArgumentError, UserCommandLineError
from pdftl.output.save import save_pdf
from pdftl.utils.user_input import pdf_filename_completer


def _first_or_none(x: list):
    try:
        return x[0]
    except (IndexError, ValueError):
        return None


@dataclass
class CliStage:
    """
    A structured representation of a single stage in a processing pipeline.
    """

    operation: Optional[str] = None
    inputs: list[str] = field(default_factory=list)
    input_passwords: list[Optional[str]] = field(default_factory=list)
    handles: dict[str, int] = field(default_factory=dict)
    operation_args: list[str] = field(default_factory=list)
    options: dict[str, any] = field(default_factory=dict)

    def resolve_stage_io_prompts(self, get_input, stage_num):
        """
        Looks for "PROMPT" in a parsed stage's inputs
        and prompts the user to resolve them.
        """
        logger.debug("resolve_stage_io_prompts")
        # Create an inverse handle map for nice prompts
        handles_inverse = {index: handle for handle, index in self.handles.items()}
        for i, filename in enumerate(self.inputs):
            logger.debug("i=%s, filename=%s", i, filename)
            if filename == "PROMPT":
                logger.debug("Found a PROMPT, asking user")
                desc = f"input #{i + 1}"
                if (handle := handles_inverse.get(i, None)) is not None:
                    desc += f" with handle {handle}"

                if stage_num > 1:
                    desc = f"pipeline stage {stage_num}, {desc}"

                new_filename = get_input(
                    f"Enter a filename for an input PDF ({desc}): ",
                    completer=pdf_filename_completer,
                )

                self.inputs[i] = new_filename


# pylint: disable=too-few-public-methods
class PipelineManager:
    """Orchestrates the execution of a multi-stage PDF processing pipeline."""

    def __init__(self, stages, global_options, input_context):
        self.stages: [CliStage] = stages
        self.global_options = global_options
        self.pipeline_pdf = None
        self.kept_id = None
        self.input_context = input_context

    def run(self):
        """Executes all stages in the pipeline."""
        logger.debug("Running pipeline with %s  stages", len(self.stages))
        try:
            for i, stage in enumerate(self.stages):
                stage.resolve_stage_io_prompts(self.input_context.get_input, i + 1)
                self._validate_and_execute_numbered_stage(i, stage)

            if self.pipeline_pdf:
                save_pdf(
                    self.pipeline_pdf,
                    self.global_options.get("output"),
                    self.input_context,
                    **self._save_kw_options(),
                )
        finally:
            if isinstance(self.pipeline_pdf, pikepdf.Pdf):
                self.pipeline_pdf.close()

    def _save_kw_options(self):
        return {"options": self.global_options, "set_pdf_id": self.kept_id}

    def _validate_and_execute_numbered_stage(self, i, stage):
        if not stage.operation and i == len(self.stages) - 1:
            logger.debug("Final stage is empty, proceeding to save.")
            return

        is_first = i == 0
        is_last = i == len(self.stages) - 1

        logger.debug("--- PIPELINE: STAGE %d ---", i + 1)
        logger.debug("Parsed stage: %s", stage)

        self._validate_stage_args(stage, is_first, is_last)
        self._execute_stage(stage, is_first)

    def _execute_stage(self, stage, is_first):
        """Opens PDFs and runs the operation for a single stage."""
        opened_pdfs = self._open_input_pdfs(stage, is_first)

        if self.pipeline_pdf and self.pipeline_pdf not in opened_pdfs:
            self.pipeline_pdf.close()

        result = self._run_operation(stage, opened_pdfs)

        if isinstance(result, types.GeneratorType):
            self._save_generator_and_cleanup(result, opened_pdfs)
        else:
            for pdf in opened_pdfs:
                if pdf != result:
                    pdf.close()
            self.pipeline_pdf = result

    def _save_generator_and_cleanup(self, result, opened_pdfs):
        """Save a generator pipeline output and clean up"""
        logger.debug("Found a PDF generator in the pipeline. Saving.")
        # we must consume the generator now, before closing opened pdfs
        for filename, pdf in result:
            logger.debug("Saving a generator PDF to '%s'", filename)
            save_pdf(
                pdf, filename, self.input_context.get_input, **self._save_kw_options()
            )
            pdf.close()
        # generator finished, so close all opened pdfs
        for pdf in opened_pdfs:
            pdf.close()

    def _validate_stage_args(self, stage, is_first, is_last):
        """Validates arguments for a given stage."""
        if not stage.inputs and is_first:
            raise MissingArgumentError(
                "No initial input files provided. "
                "\n  Maybe you put an operation before the input file?"
                f"\n  Correct syntax: {WHOAMI} <input>... <operation> [<other arguments>]"
            )

        op_data = registry.operations.get(stage.operation, {})
        op_requires_output = " output " in op_data.get("usage", "")
        if (
            is_last
            and (stage.operation == "filter" or op_requires_output)
            and not self.global_options.get("output")
        ):
            raise MissingArgumentError(
                f"The '{stage.operation}' operation requires 'output <file>' in the final stage."
            )

        num_explicit = len([i for i in stage.inputs if i not in ["-", "_"]])
        effective_inputs = num_explicit + (0 if is_first else 1)

        self._validate_number_of_effective_inputs(stage.operation, effective_inputs)

    def _validate_number_of_effective_inputs(self, operation, effective_inputs):
        if (op_data := registry.operations.get(operation)) is None:
            return
        op_type = op_data.get("type")
        logger.debug("operation=%s, op_type=%s", operation, op_type)
        if op_type == "single input operation" and effective_inputs != 1:
            raise UserCommandLineError(
                f"The '{operation}' operation requires one input, "
                f"but received {effective_inputs} effective input(s)."
            )
        if op_type == "multi input operation" and effective_inputs < 2:
            raise MissingArgumentError(
                f"The '{operation}' operation requires 2 or more inputs, "
                f"but received {effective_inputs} effective input(s)."
            )

    def _run_operation(self, stage, opened_pdfs):
        """Dispatches to the correct command function based on the operation."""
        operation = stage.operation
        op_data = registry.operations.get(operation)
        op_function, arg_style = op_data.get("function"), op_data.get("args")
        if not op_function or not arg_style:
            raise ValueError(f"Operation '{operation}' is not fully configured.")

        call_context = {
            "operation": operation,
            "inputs": stage.inputs,
            "opened_pdfs": opened_pdfs,
            "input_filename": _first_or_none(stage.inputs),
            "input_password": _first_or_none(stage.input_passwords),
            "input_pdf": _first_or_none(opened_pdfs),
            "operation_args": stage.operation_args,
            "aliases": stage.handles,
            "overlay_pdf": _first_or_none(stage.operation_args),
            "on_top": "stamp" in operation,
            "multi": "multi" in operation,
            "output": self.global_options.get("output", None),
            "output_pattern": self.global_options.get("output", "pg_%04d.pdf"),
            "get_input": self.input_context.get_input,
        }

        try:
            pos_args, kw_args = self._make_op_args(arg_style, call_context)
        except Exception as exception:
            logger.error(
                "Internal error assigning arguments for operation '%s'. This is a bug.",
                operation,
            )
            raise exception
        return op_function(*pos_args, **kw_args)

    def _make_op_args(self, arg_style, context):
        pos_arg_names, kw_arg_map = arg_style[:2]
        kw_const_arg_map = arg_style[2] if len(arg_style) > 2 else {}
        pos_args = [context[name] for name in pos_arg_names]
        kw_args = {key: context[val] for key, val in kw_arg_map.items()}
        kw_args.update(kw_const_arg_map)
        return pos_args, kw_args

    def _open_pdf_from_special_input(self, is_first: bool):
        """
        Handles opening a PDF from a special input source (stdin or a
        previous pipeline stage).
        """
        if is_first:
            logger.debug("Reading PDF from stdin for first stage")
            if sys.stdin.isatty():
                raise UserCommandLineError(
                    "Expected PDF data from stdin, but none was provided."
                )
            data = sys.stdin.buffer.read()
            return pikepdf.open(io.BytesIO(data))

        logger.debug("Using PDF from previous stage for input '_'")
        if not self.pipeline_pdf:
            raise UserCommandLineError(
                "Pipeline error: No PDF available from previous stage for input '_'."
            )
        return self.pipeline_pdf

    def _open_pdf_from_file(self, filename: str, password: str or None):
        """
        Opens a PDF from a file path, handling passwords and file-related errors.
        """
        kwargs = {"password": password} if password else {}
        try:
            logger.debug("Opening file '%s'", filename)
            return pikepdf.open(filename, **kwargs)
        except FileNotFoundError as exception:
            raise UserCommandLineError(exception) from exception
        except pikepdf.PasswordError as exc:
            msg = (
                str(exc)
                if password
                else f"File '{filename}' is encrypted and requires a password. "
                f"For help: {WHOAMI} help inputs"
            )
            raise UserCommandLineError(msg) from exc

    def _open_input_pdfs(self, stage, is_first):
        """Opens all PDF inputs required for a stage."""
        opened_pdfs = []

        for i, filename in enumerate(stage.inputs):
            if filename in ["-", "_"]:
                pdf_obj = self._open_pdf_from_special_input(is_first)
            else:
                password = stage.input_passwords[i]
                pdf_obj = self._open_pdf_from_file(filename, password)
            opened_pdfs.append(pdf_obj)
            if (
                self.global_options.get("keep_first_id")
                and is_first
                and i == 0
                and len(opened_pdfs) > 0
            ):
                self.kept_id = list(opened_pdfs[0].trailer.ID)

        if self.global_options.get("keep_final_id") and len(opened_pdfs) > 0:
            self.kept_id = list(opened_pdfs[-1].trailer.ID)

        return opened_pdfs
