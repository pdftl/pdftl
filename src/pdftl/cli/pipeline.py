# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/cli/pipeline.py

"""Manage a pipeline of operations"""

import logging
import sys
import types
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Union

if TYPE_CHECKING:
    import pikepdf

import pdftl.core.constants as c
from pdftl.cli.constants import SUB_EACH, SUB_END, SUB_START
from pdftl.cli.whoami import WHOAMI
from pdftl.core.core_types import HelpExample, OpResult
from pdftl.core.executor import run_operation
from pdftl.core.registry import register_help_topic, registry
from pdftl.exceptions import MissingArgumentError, UserCommandLineError
from pdftl.output.save import save_content
from pdftl.utils.arg_helpers import expand_shorthand_args
from pdftl.utils.io_helpers import smart_pikepdf_open
from pdftl.utils.profiling import CliStageProfiler
from pdftl.utils.user_input import pdf_filename_completer

logger = logging.getLogger(__name__)


def _first_or_none(x: list):
    try:
        return x[0]
    except (IndexError, ValueError):
        return None


@dataclass
class InlineSubPipeline:
    """Wrapper for a nested list of stages representing an inline sub-pipeline."""

    stages: list["CliStage"]
    original_text: str = "<inline_sub_pipeline>"
    handle_name: str | None = None

    def __repr__(self):
        return self.original_text


@dataclass
class EachSubPipeline:
    """Wrapper for a per-input iteration sub-pipeline."""

    stages: list["CliStage"]
    original_text: str = "<each_sub_pipeline>"

    def __repr__(self):
        return self.original_text


@dataclass
class CliStage:
    """
    A structured representation of a single stage in a processing pipeline.
    """

    operation: str | None = None
    inputs: list[str] = field(default_factory=list)
    input_passwords: list[str | None] = field(default_factory=list)
    handles: dict[str, int] = field(default_factory=dict)
    operation_args: list[str] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)

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


class PipelineResult:
    """The final payload returned by a pipeline execution."""

    pdf: Union["pikepdf.Pdf", None] = None
    results: list[OpResult] = field(default_factory=list)


# pylint: disable=too-few-public-methods
class PipelineManager:
    """Orchestrates the execution of a multi-stage PDF processing pipeline."""

    def __init__(
        self, stages, input_context, is_inline=False, is_each=False, handles=None
    ) -> None:
        self.stages: list[CliStage] = stages
        self.pipeline_pdf = None
        self.kept_id = None
        self.input_context = input_context
        self.results: list[OpResult] = []
        self.result_discardable = False
        self.is_inline = is_inline
        self.is_each = is_each
        self.handles = handles or {}

    def save_pdf_file(self, pdf, filename, stage):
        # Pass stage.options directly.
        # This ensures intermediate files don't inherit global flags like 'uncompress'.
        save_options = stage.options

        # Call save. _save_kw_options will handle the isolation logic.
        save_content(
            pdf,
            filename,
            self.input_context,
            **self._save_kw_options(override_options=save_options),
        )

    def run(self):
        """Executes all stages in the pipeline."""
        logger.info("Running pipeline with %s stage(s)", len(self.stages))
        try:
            for i, stage in enumerate(self.stages):
                stage.resolve_stage_io_prompts(self.input_context.get_input, i + 1)
                stage_name = stage.operation or "filter"
                stage_args = stage.operation_args
                with CliStageProfiler(stage_name, stage_args):
                    self._validate_and_execute_numbered_stage(i, stage)
                stage_output = stage.options.get(c.OUTPUT)
                logger.debug("stage_output=%s", stage_output)

                op_entry = registry.operations.get(stage.operation, {})

                # Check if we have an output file AND a PDF to save.
                # Some operations (like dump_text) handle output themselves via hooks
                # and leave pipeline_pdf as None. We must skip save_pdf in that case.
                #
                # OR they can set skip_pipeline_save in the @register_operation decorator
                #
                skip_pipeline_save = op_entry.get("skip_pipeline_save", False)
                if stage_output and self.pipeline_pdf and not skip_pipeline_save:
                    logger.info("Persisting stage output path -> %s", stage_output)
                    self.save_pdf_file(self.pipeline_pdf, stage_output, stage)
                    logger.info("Success: Output written to %s", stage_output)
                else:
                    logger.debug(
                        (
                            "Skipping save. skip_pipeline_save=%s, "
                            "stage_output='%s', self.pipeline_pdf is truthy-%s"
                        ),
                        skip_pipeline_save,
                        stage_output,
                        bool(self.pipeline_pdf),
                    )
        finally:
            if self.pipeline_pdf is not None and not self.is_inline:
                import pikepdf

                if isinstance(self.pipeline_pdf, pikepdf.Pdf):
                    self.pipeline_pdf.close()

    def _save_kw_options(self, override_options=None):
        """
        Construct the keyword arguments for the save_pdf function.
        """
        # If override_options is provided, we use it.
        # Otherwise, we default to empty (standard defaults apply).
        final_options = override_options.copy() if override_options else {}

        # Return the kwargs expected by save_pdf
        return {c.OPTIONS: final_options, "set_pdf_id": self.kept_id}

    def _output_targets_info(self, stage, is_first):
        if stage.inputs:
            logger.info("Input Targets:")
            for idx, inp in enumerate(stage.inputs):
                has_pw = (
                    stage.input_passwords
                    and idx < len(stage.input_passwords)
                    and stage.input_passwords[idx]
                )
                pw_status = " [encrypted payload secured]" if has_pw else ""

                if isinstance(inp, str):
                    logger.info("   %s%s", inp, pw_status)
                else:
                    logger.info("   %s", repr(inp))
        elif not is_first:
            logger.info(
                "Input Targets: Implicit pipeline stream '_' (reusing preceding stage output)"
            )

    def _output_other_info(self, i, stage):
        if stage.operation_args:
            logger.info("Arguments: %s", ", ".join(map(str, stage.operation_args)))

        stage_output = stage.options.get(c.OUTPUT)
        if stage_output:
            logger.info("Output Destination: %s", stage_output)

        local_settings = {k: v for k, v in stage.options.items() if k != c.OUTPUT}
        if local_settings:
            logger.info("Localized Settings: %s", local_settings)

        logger.debug("--- PIPELINE: STAGE %d ---", i + 1)
        logger.debug("Parsed stage: %s", stage)
        # fixme: resolve - here for stdin! or fix validation

    def _output_stage_header_info(self, i, stage):
        # Build clean verbose mapping details
        pipeline_context = (
            "Inline Sub-Pipeline "
            if self.is_inline
            else "Each Sub-Pipeline "
            if self.is_each
            else ""
        )
        logger.info("--- %sStage %d ---", pipeline_context, i + 1)

    def _output_operation_info(self, stage):
        operation_name = stage.operation or "filter"
        op_data = registry.operations.get(stage.operation, {})
        op_desc = op_data.get("desc", "Apply filters or configuration pass-through settings.")
        logger.info("Operation to execute: %s (%s)", operation_name, op_desc)

    def _output_info(self, i, stage, is_first):
        self._output_stage_header_info(i, stage)
        self._output_operation_info(stage)
        self._output_targets_info(stage, is_first)
        self._output_other_info(i, stage)

    def _output_stage_finished(self, i):
        logger.info("Stage %s: operation complete.", i + 1)

    def _validate_and_execute_numbered_stage(self, i, stage):
        is_first = i == 0 and not self.is_each
        is_last = i == len(self.stages) - 1
        if not stage.operation and is_last:
            stage_output = stage.options.get(c.OUTPUT)
            if stage_output:
                logger.info("--- Finalizing Pipeline Execution ---")
                logger.info("Operation: Saving final pipeline asset context")
                logger.info("Target File: %s", stage_output)
            else:
                logger.debug("Final stage is empty, proceeding to save.")
            return
        self._validate_stage_args(stage, is_first, is_last)
        self._output_info(i, stage, is_first)
        self._execute_stage(stage, is_first)
        self._output_stage_finished(i)

    def _execute_stage(self, stage, is_first):
        logger.debug("_execute_stage")
        opened_pdfs, effective_inputs, adjusted_handles = self._open_input_pdfs(stage, is_first)

        if self.pipeline_pdf and self.pipeline_pdf not in opened_pdfs:
            self.pipeline_pdf.close()

        result = self._run_operation(stage, opened_pdfs, effective_inputs, adjusted_handles)
        self._process_result(result, stage, opened_pdfs)

    def _unpack_result_value_and_run_hooks(self, result, stage, opened_pdfs):
        # Unpack OpResult if present
        if isinstance(result, OpResult):
            self.results.append(result)
            self.result_discardable = result.is_discardable

            # CLI Hooks (like printing text to console)
            hook_ran = False
            if not getattr(self.input_context, "is_api", False):
                op_entry = registry.operations.get(stage.operation, {})
                if hook := op_entry.get("cli_hook"):
                    hook(result, stage, self)
                    hook_ran = True

            # Update the pipeline data
            #
            # Some operations (e.g. burst) set OpResult.pdf to a source
            # PDF purely as CLI-hook bookkeeping (e.g. for a post-drain
            # dump_data call), even though the "real" result is the
            # generator in .data. That .pdf is only safe to treat as
            # authoritative once something has actually drained the
            # generator -- which only the CLI hook does, and only when it
            # ran. If no hook drained it, the generator is still the live,
            # not-yet-consumed result, and must flow into pipeline_pdf
            # instead so cleanup is deferred to whoever drains it later.
            # This takes priority over result.pdf regardless of whether
            # result.pdf happens to be None or not.
            if not hook_ran and isinstance(result.data, types.GeneratorType):
                result_val = result.data
            else:
                result_val = result.pdf
        else:
            self.result_discardable = False
            result_val = result

        if not result_val and opened_pdfs:
            result_val = opened_pdfs[0]

        return result_val

    def _process_result(self, result, stage, opened_pdfs):
        """
        Updates pipeline state and manages file cleanup.
        Crucially, it defers cleanup if the result is a generator.
        """

        import pikepdf

        result_val = self._unpack_result_value_and_run_hooks(result, stage, opened_pdfs)

        # Update the Pipeline State Variable
        self.pipeline_pdf = result_val

        # Smart Cleanup Logic
        # CASE A: Generator (Lazy Evaluation)
        # We CANNOT close opened_pdfs here because the generator hasn't run yet.
        # We rely on the generator having a `finally` block to close these when done.
        if isinstance(result_val, types.GeneratorType):
            logger.debug("Stage returned a generator. Deferring cleanup to the generator.")
            # Do nothing. The generator "owns" the opened_pdfs now.

        # CASE B: Standard Object (Immediate Evaluation)
        # We can safely close the inputs that aren't the result.
        else:
            for pdf in opened_pdfs:
                if pdf != result_val:
                    if isinstance(pdf, pikepdf.Pdf):
                        pdf.close()

    def _validate_stage_args(self, stage, is_first, is_last):
        """Validates arguments for a given stage."""
        op_data = registry.operations.get(stage.operation, {})
        op_type = op_data.get("type")

        # Allow 'source operation' to bypass the first-stage input check
        if not stage.inputs and is_first and op_type != "source operation":
            raise MissingArgumentError(
                "No initial input files provided. "
                "\n  Maybe you put an operation before the input file?"
                f"\n  Correct syntax: {WHOAMI} <input>... <operation> [<other arguments>]"
            )

        op_requires_output = " output " in op_data.get("usage", "")

        # Check if the stage has an output option
        has_output = bool(stage.options.get(c.OUTPUT))

        if (
            is_last
            and not self.is_inline
            and (stage.operation == "filter" or op_requires_output)
            and not has_output
        ):
            raise MissingArgumentError(
                f"The '{stage.operation}' operation requires 'output <file>' in the final stage."
            )

        num_explicit = len([i for i in stage.inputs if i != "_"])
        effective_inputs = num_explicit + (0 if is_first else 1)

        self._validate_number_of_effective_inputs(stage.operation, effective_inputs)

    def _validate_number_of_effective_inputs(self, operation, effective_inputs):
        if (op_data := registry.operations.get(operation)) is None:
            return
        op_type = op_data.get("type")
        logger.debug("operation=%s, op_type=%s", operation, op_type)

        # Ensure source operations don't receive unexpected inputs
        if op_type == "source operation" and effective_inputs > 0:
            raise UserCommandLineError(
                f"The '{operation}' operation creates a new PDF from scratch "
                f"and does not take input files, but received {effective_inputs} input(s)."
            )

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

    def _run_operation(self, stage, opened_pdfs, effective_inputs=None, adjusted_handles=None):
        """Dispatches to the correct command function based on the operation."""
        operation = stage.operation
        op_data = registry.operations.get(operation)
        if op_data:
            op_function, arg_style = op_data.get("function"), op_data.get("args")
        else:
            op_function, arg_style = None, None
        if not op_function or not arg_style:
            raise ValueError(f"Operation '{operation}' is not fully configured.")

        # Determine output pattern from local stage options or default
        output_pattern = stage.options.get(c.OUTPUT, "pg_%04d.pdf")

        try:
            args_expanded = expand_shorthand_args(stage.operation_args)
        except TypeError as exc:
            raise UserCommandLineError(exc)

        call_context = {
            c.OPERATION_NAME: operation,
            c.INPUTS: stage.inputs if effective_inputs is None else effective_inputs,
            c.OPENED_PDFS: opened_pdfs,
            c.INPUT_FILENAME: _first_or_none(stage.inputs),
            c.INPUT_PASSWORD: _first_or_none(stage.input_passwords),
            c.INPUT_PDF: _first_or_none(opened_pdfs),
            c.OPERATION_ARGS: stage.operation_args,
            c.OPERATION_ARGS_EXPANDED: args_expanded,
            c.ALIASES: stage.handles if adjusted_handles is None else adjusted_handles,
            c.OVERLAY_PDF: _first_or_none(stage.operation_args),
            c.OUTPUT: stage.options.get(c.OUTPUT, None),
            c.OUTPUT_PATTERN: output_pattern,
            c.GET_INPUT: self.input_context.get_input,
        }

        return run_operation(operation, call_context)

    def _make_op_args(self, arg_style, context):
        pos_arg_names, kw_arg_map = arg_style[:2]
        kw_const_arg_map = arg_style[2] if len(arg_style) > 2 else {}
        pos_args = [context[name] for name in pos_arg_names]
        kw_args = {key: context[val] for key, val in kw_arg_map.items()}
        kw_args.update(kw_const_arg_map)
        return pos_args, kw_args

    def _open_pdf_from_special_input(self, password: str | None, is_first: bool):
        """
        Handles opening a PDF from a special input source (stdin or a
        previous pipeline stage).
        """
        import pikepdf

        if is_first:
            logger.debug("Reading PDF from stdin for first stage")
            if sys.stdin.isatty():
                raise UserCommandLineError("Expected PDF data from stdin, but none was provided.")

            try:
                return smart_pikepdf_open(None, password)
            except pikepdf.PasswordError as exc:
                msg = (
                    str(exc)
                    if password
                    else f"PDF data on stdin is encrypted and requires a password. "
                    f"For help: {WHOAMI} help inputs"
                )
                raise UserCommandLineError(msg) from exc

        logger.debug("Using PDF from previous stage for input '_'")
        if not self.pipeline_pdf:
            raise UserCommandLineError(
                "Pipeline error: No PDF available from previous stage for input '_'."
            )
        return self.pipeline_pdf

    def _open_pdf_from_file(self, filename: str, password: str | None):
        """
        Opens a PDF from a file path, handling passwords and file-related errors.
        """
        import pikepdf

        try:
            logger.debug("Opening file '%s'", filename)
            return smart_pikepdf_open(filename, password)
        except pikepdf.PasswordError as exc:
            msg = (
                str(exc)
                if password
                else f"File '{filename}' is encrypted and requires a password. "
                f"For help: {WHOAMI} help inputs"
            )
            raise UserCommandLineError(msg) from exc
        except (FileNotFoundError, pikepdf.PdfError) as exception:
            raise UserCommandLineError(
                f"Could not open '{filename}': the file may be corrupted or is not a valid PDF.\n"
                f"  (Details: {exception})"
            ) from exception
        except OSError as exc:
            if exc.errno == 24:  # EMFILE
                raise UserCommandLineError(
                    f"Too many input files: could not open '{filename}'.\n"
                    f"  Hint: increase the number of files that can be opened"
                    f" by running 'ulimit -n 4096' (for example) in your shell before retrying."
                ) from exc
            raise UserCommandLineError(f"Could not open '{filename}': {exc}") from exc

    def _open_input_pdfs(self, stage, is_first):
        """Opens all PDF inputs required for a stage."""
        logger.debug("_open_input_pdfs")
        opened_pdfs = []
        effective_inputs = []
        final_stage_options = self.stages[-1].options if self.stages else {}
        adjusted_handles = {}  # handle name -> actual index in opened_pdfs after expansion

        for i, item in enumerate(stage.inputs):
            password = stage.input_passwords[i]
            if isinstance(item, EachSubPipeline):
                logger.debug("Detected EachSubPipeline input at index %s", i)
                self._expand_each_in_place(item, opened_pdfs, effective_inputs)
                continue
            pdf_obj = self._resolve_input_item(item, i, stage, opened_pdfs, password, is_first)
            opened_pdfs.append(pdf_obj)
            effective_inputs.append(item if isinstance(item, str) else repr(item))
            # Register this item's adjusted index for any handle pointing to it
            for h_name, h_idx in stage.handles.items():
                if h_idx == i:
                    adjusted_handles[h_name] = len(opened_pdfs) - 1
            self._maybe_keep_first_id(final_stage_options, is_first, i, opened_pdfs)

        if final_stage_options.get("keep_final_id") and len(opened_pdfs) > 0:
            self.kept_id = list(opened_pdfs[-1].trailer.ID)

        return opened_pdfs, effective_inputs, adjusted_handles

    def _expand_each_in_place(self, item, opened_pdfs, effective_inputs):
        """Drain accumulated inputs, run EACH sub-pipeline over each, expand in place."""
        if not opened_pdfs:
            raise UserCommandLineError("EACH requires at least one input before it.")
        per_input_pdfs = list(opened_pdfs)
        per_input_names = list(effective_inputs)
        opened_pdfs.clear()
        effective_inputs.clear()
        for source_pdf, name in zip(per_input_pdfs, per_input_names):
            opened_pdfs.append(self._get_each_output_pdf(item, source_pdf))
            effective_inputs.append(name)

    def _resolve_input_item(
        self, item, i, stage, opened_pdfs, password, is_first, adjusted_handles=None
    ):
        if isinstance(item, InlineSubPipeline):
            logger.debug("Detected InlineSubPipeline input at index %s", i)
            return self._get_subpipeline_output_pdf(stage, i, item, opened_pdfs, adjusted_handles)
        if isinstance(item, str) and item in stage.handles and stage.handles[item] < i:
            idx = (adjusted_handles or {}).get(item, stage.handles[item])
            return opened_pdfs[idx]
        if isinstance(item, str) and item in self.handles:
            return self.handles[item]
        if item in ["-", "_"]:
            return self._open_pdf_from_special_input(password, is_first)
        return self._open_pdf_from_file(item, password)

    def _maybe_keep_first_id(self, final_stage_options, is_first, i, opened_pdfs):
        if (
            final_stage_options.get("keep_first_id")
            and is_first
            and i == 0
            and len(opened_pdfs) > 0
        ):
            self.kept_id = list(opened_pdfs[0].trailer.ID)

    def _get_each_output_pdf(
        self, item: "EachSubPipeline", source_pdf: "pikepdf.Pdf"
    ) -> "pikepdf.Pdf":
        sub_manager = PipelineManager(
            stages=item.stages,
            input_context=self.input_context,
            is_inline=True,
            is_each=True,
            handles=self.handles.copy(),
        )
        sub_manager.pipeline_pdf = source_pdf
        sub_manager.run()
        if sub_manager.pipeline_pdf is None:
            raise UserCommandLineError("EACH sub-pipeline returned no output PDF.")
        result = sub_manager.pipeline_pdf
        sub_manager.pipeline_pdf = None
        return result

    def _get_subpipeline_output_pdf(
        self, stage, item_idx, item, opened_pdfs, adjusted_handles=None
    ):
        child_handles = self.handles.copy()
        for h_name, h_idx in stage.handles.items():
            if h_idx < item_idx:
                actual_idx = (adjusted_handles or {}).get(h_name, h_idx)
                if actual_idx < len(opened_pdfs):
                    child_handles[h_name] = opened_pdfs[actual_idx]
        sub_manager = PipelineManager(
            stages=item.stages,
            input_context=self.input_context,
            is_inline=True,
            handles=child_handles,
        )
        sub_manager.run()
        if sub_manager.pipeline_pdf is None:
            raise UserCommandLineError("Inline pipeline returned no output PDF.")
        pdf_obj = sub_manager.pipeline_pdf
        sub_manager.pipeline_pdf = None
        return pdf_obj


_PIPELINE_HELP_EXAMPLES = [
    # --- Existing Examples ---
    HelpExample(
        desc="Shuffle two documents, then crop the resulting pages to A4",
        cmd="a.pdf b.pdf shuffle --- crop '(a4)' output out.pdf",
    ),
    HelpExample(
        desc=(
            "Crop all pages to A3 in landscape,\n"
            "and preview the effect of cropping odd pages to A4"
        ),
        cmd="in.pdf crop '(A3_l)' --- crop 'odd(A4)' preview output out.pdf",
    ),
    HelpExample(
        desc="Save a snapshot of a rotated file, then apply a stamp and save the final version",
        cmd=(
            "in.pdf rotate right output rotated_snapshot.pdf --- "
            "background watermark.pdf output final.pdf"
        ),
    ),
    HelpExample(
        desc=(
            "Use pipeline substitution (JOB...DONE) to rotate one file"
            "before merging it with another."
        ),
        cmd=f"{SUB_START} in.pdf cat right {SUB_END} main.pdf cat output final.pdf",
    ),
    HelpExample(
        desc="Rotate and stamp a.pdf, crop b.pdf, then combine selected pages from both",
        cmd=(
            f"A={SUB_START} a.pdf rotate right --- stamp logo.pdf {SUB_END} "
            f"B={SUB_START} b.pdf crop '(a4)' {SUB_END} "
            "cat A1-3 B2-end output combined.pdf"
        ),
    ),
    HelpExample(
        desc="Join a contract with a stamped copy of itself",
        cmd=f"contract.pdf {SUB_START} contract.pdf stamp logo.pdf {SUB_END} output combined.pdf",
    ),
    # --- New Examples (Extracted from docstring) ---
    HelpExample(
        desc=(
            "Chain multiple operations together "
            "where the output of one becomes the input of the next"
        ),
        cmd="in.pdf rotate right --- crop '(a4)' output out.pdf",
    ),
    HelpExample(
        desc="Assign named handles to inputs to reuse them later in the pipeline",
        cmd="A=logo.pdf B=content.pdf cat A B A output out.pdf",
    ),
    HelpExample(
        desc="Assign the result of a sub-pipeline to a named handle",
        cmd=f"S={SUB_START} in.pdf rotate right {SUB_END} S main.pdf cat output out.pdf",
    ),
    HelpExample(
        desc="Chain multiple operations inside a sub-pipeline",
        cmd=f"A={SUB_START} in.pdf rotate right --- stamp logo.pdf {SUB_END} cat A output out.pdf",
    ),
    HelpExample(
        desc=(
            "Apply a sub-pipeline to multiple files independently "
            "replacing each with its transformed result"
        ),
        cmd=f"a.pdf b.pdf c.pdf {SUB_EACH} rotate right {SUB_END} cat output out.pdf",
    ),
    HelpExample(
        desc="Chain multiple operations inside an EACH block (e.g., select pages, then rotate)",
        cmd=f"a.pdf b.pdf {SUB_EACH} cat 1-3 --- rotate right {SUB_END} cat output out.pdf",
    ),
    HelpExample(
        desc=(
            "Use filename substitution inside an EACH block "
            "to add a cover page to each file, with its filename, then merge"
        ),
        cmd=(
            f"a.pdf b.pdf {SUB_EACH} insert before 1 --- "
            f"add_text '1/{{filename}}/(position=center)' {SUB_END} cat output combined.pdf"
        ),
    ),
    HelpExample(
        desc=(
            "Wrap EACH inside a JOB to collect the results of all iterations into a single handle"
        ),
        cmd=f"R={SUB_START} a.pdf b.pdf {SUB_EACH} cat 1 {SUB_END} {SUB_END} cat R output out.pdf",
    ),
    HelpExample(
        desc=(
            "Use a JOB inside EACH to process a specific page "
            "and prepend it to the current iteration's file"
        ),
        cmd=(
            f"a.pdf b.pdf {SUB_EACH} R={SUB_START} _ cat 1 {SUB_END} "
            f"cat R _ {SUB_END} cat output out.pdf"
        ),
    ),
    HelpExample(
        desc="Combine EACH and JOB as siblings in the same pipeline",
        cmd=(
            f"a.pdf b.pdf {SUB_EACH} cat 1 {SUB_END} "
            f"R={SUB_START} cover.pdf stamp logo.pdf {SUB_END} cat R output out.pdf"
        ),
    ),
]


@register_help_topic(
    "pipeline",
    title="pipeline syntax",
    desc="Chaining operations, named handles, and pipeline substitution",
    examples=_PIPELINE_HELP_EXAMPLES,
)
def _pipeline_help_topic():
    """
    Construct complex workflows using pipelines, handles, and substitution.

    **1. Chaining (`---`)**

    Multiple operations can be chained together using `---`. The output of
    one stage becomes the input for the next stage. If a stage has no
    explicit inputs, it automatically uses the result from the previous
    stage.

    ```
    pdftl in.pdf rotate right --- crop '(a4)' output out.pdf
    ```

    You can have as many stages as you like. Each `---` separator marks
    the boundary between stages.

    **2. Named Handles (`X=...`)**

    You can assign single capital letter handles (A-Z) to inputs to refer
    to them later:

    ```
    pdftl A=logo.pdf B=content.pdf ...
    ```

    This allows you to reuse a specific file or result multiple times in
    different stages (e.g., `cat A B A`). Handles are visible to all
    subsequent stages in the same pipeline.

    **3. Pipeline Substitution (`JOB ... DONE`)**

    Similar to command substitution in a shell (`$(...)` in bash), you can
    process files in a temporary sub-pipeline and use the result as an
    input — inline, without saving to a file first.

    ```
    pdftl JOB in.pdf rotate right DONE main.pdf cat output out.pdf
    ```

    The commands between `JOB` and `DONE` run first, and their output is
    used as an input at that position. You can also assign the result to a
    named handle:

    ```
    pdftl S=JOB in.pdf rotate right DONE S main.pdf cat output out.pdf
    ```

    Sub-pipelines can themselves contain `---` separators to chain
    operations inside the substitution:

    ```
    pdftl A=JOB in.pdf rotate right --- stamp logo.pdf DONE ...
    ```

    Sub-pipelines can be nested arbitrarily. Each `DONE` always closes the
    innermost open `JOB`.

    **4. Per-file Iteration (`EACH ... DONE`)**

    `EACH ... DONE` applies a sub-pipeline to every input file that
    precedes it, independently, replacing each input with its transformed
    result. This is similar to `map` in functional programming, or
    `xargs` in the shell.

    ```
    pdftl a.pdf b.pdf c.pdf EACH rotate right DONE cat output out.pdf
    ```

    Here, `rotate right` is applied to each of `a.pdf`, `b.pdf`, and
    `c.pdf` in turn. The results are then passed to `cat` for merging.

    The sub-pipeline inside `EACH ... DONE` receives each input file via
    the implicit pipeline input `_`, exactly as if it were a stage after
    `---`. This means you can chain multiple operations inside `EACH`:

    ```
    pdftl a.pdf b.pdf EACH cat 1-3 --- rotate right DONE cat output out.pdf
    ```

    This selects pages 1–3 from each file, rotates them, and merges the
    results.

    **`EACH` and filename substitution**

    Because each iteration runs as an isolated sub-pipeline with one input
    file, operations that substitute the current filename (such as
    `add_text`) work naturally inside `EACH`:

    ```
    pdftl *.pdf EACH insert before 1 --- add_text '/{filename}/(position=center)' DONE\
      cat output combined.pdf
    ```

    This inserts a blank title page stamped with each file's own name
    before merging everything together.

    **5. Combining `JOB` and `EACH`**

    `JOB` and `EACH` can be freely combined. Both use `DONE` as their
    terminator; `DONE` always closes the innermost open block.

    *`EACH` inside `JOB`*: the `JOB` collects the results of all
    iterations into a single output:

    ```
    pdftl R=JOB a.pdf b.pdf EACH cat 1 DONE DONE cat R output out.pdf
    ```

    `EACH` selects page 1 from each of `a.pdf` and `b.pdf`; the enclosing
    `JOB` merges them and assigns the 2-page result to `R`.

    *`JOB` inside `EACH`*: each iteration can use its own sub-pipeline as
    an additional input. Pass `_` explicitly to the `JOB` to refer to the
    current iteration's file:

    ```
    pdftl a.pdf b.pdf EACH R=JOB _ cat 1 DONE cat R _ DONE cat output out.pdf
    ```

    For each input file, `JOB` extracts its first page into `R`, then
    `cat R _` prepends that first page to the full file.

    *Siblings*: `EACH` and `JOB` can appear as siblings in the same input
    list. Each gets its own `DONE`:

    ```
    pdftl a.pdf b.pdf EACH cat 1 DONE R=JOB cover.pdf stamp logo.pdf DONE cat R output out.pdf
    ```

    The two `DONE` tokens close `EACH` and `JOB` respectively. The outer
    `cat` receives: the `EACH` results (one page from each of `a` and `b`)
    followed by `R` (the stamped cover), and merges all three.
    """


@register_help_topic(
    "input",
    title=c.INPUTS,
    desc="Specifying input files and passwords",
)
def _inputs_help_topic():
    """
    The general syntax for providing input to an operation is:

    ```
    <inputs> [ input_pw <password>... ]
    ```

    `<inputs>` is a space-separated list of one or more input PDF
    sources. Each source can be:

      - A file path: `my_doc.pdf`

      - A handle assignment (for referring to files in
        operations): `A=my_doc.pdf`

      - A single dash `-` to read from standard input (stdin).

      - The keyword `PROMPT` to be interactively asked for a
        file path.

    `[ input_pw <password>... ]` is an optional block to provide
    owner passwords for encrypted files. The passwords in the
    `<password>...` list can be assigned in two ways:

      - By position: Passwords are applied sequentially to the
        encrypted input files in the order they appear, as in:

        `enc1.pdf plain.pdf enc2.pdf input_pw pass1 pass2`

      - By handle: If an input file has a handle (e.g.,
        `A=file.pdf`), its password can be assigned using the same
        handle. This is the most reliable method when using
        multiple encrypted files. As in:

        `A=enc1.pdf B=enc2.pdf input_pw B=pass2 A=pass1`

    The keyword `PROMPT` can be used in the list to be securely
    prompted for a password. This is recommended.
    """
