# src/pdftl/cli/pipeline.py

"""Manage a pipeline of operations"""

import logging
import sys
import types
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Union

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)
import pdftl.core.constants as c
from pdftl.cli.whoami import WHOAMI
from pdftl.core.executor import run_operation
from pdftl.core.registry import register_help_topic, registry
from pdftl.core.types import HelpExample, OpResult
from pdftl.exceptions import MissingArgumentError, UserCommandLineError
from pdftl.output.save import save_content
from pdftl.utils.io_helpers import smart_pikepdf_open
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

    def __init__(self, stages, input_context) -> None:
        self.stages: list[CliStage] = stages
        self.pipeline_pdf = None
        self.kept_id = None
        self.input_context = input_context
        self.results: list[OpResult] = []
        self.result_discardable = False

    def run(self):
        """Executes all stages in the pipeline."""
        logger.debug("Running pipeline with %s  stages", len(self.stages))
        try:
            for i, stage in enumerate(self.stages):
                stage.resolve_stage_io_prompts(self.input_context.get_input, i + 1)
                self._validate_and_execute_numbered_stage(i, stage)
                stage_output = stage.options.get(c.OUTPUT)

                # Check if we have an output file AND a PDF to save.
                # Some operations (like dump_text) handle output themselves via hooks
                # and leave pipeline_pdf as None. We must skip save_pdf in that case.
                if stage_output and self.pipeline_pdf:
                    # Pass stage.options directly.
                    # This ensures intermediate files don't inherit global flags like 'uncompress'.
                    save_options = stage.options

                    # Call save. _save_kw_options will handle the isolation logic.
                    save_content(
                        self.pipeline_pdf,
                        stage_output,
                        self.input_context,
                        **self._save_kw_options(override_options=save_options),
                    )

            # 4. Final Fallback Save
            # If the last stage DID NOT save (no output arg), and we have a result...
            # We check if the last stage options contained an output but for some reason
            # (logic flow) it wasn't caught above, or if we need to warn.
            # With global_options gone, we assume the Parser attached the 'output'
            # option to the final stage if it was present on the CLI.

            # NOTE: If the loop above ran for the last stage, it handled the save.
            # The only case we might be here with an unsaved result is if the
            # last stage had no 'output' option.

        finally:
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

    def _validate_and_execute_numbered_stage(self, i, stage):
        if not stage.operation and i == len(self.stages) - 1:
            logger.debug("Final stage is empty, proceeding to save.")
            return

        is_first = i == 0
        is_last = i == len(self.stages) - 1

        logger.debug("--- PIPELINE: STAGE %d ---", i + 1)
        logger.debug("Parsed stage: %s", stage)
        # fixme: resolve - here for stdin! or fix validation
        self._validate_stage_args(stage, is_first, is_last)
        self._execute_stage(stage, is_first)

    def _execute_stage(self, stage, is_first):
        """Opens PDFs and runs the operation for a single stage."""
        logger.debug("_execute_stage")
        opened_pdfs = self._open_input_pdfs(stage, is_first)

        if self.pipeline_pdf and self.pipeline_pdf not in opened_pdfs:
            self.pipeline_pdf.close()

        result = self._run_operation(stage, opened_pdfs)
        self._process_result(result, stage, opened_pdfs)

    def _process_result(self, result, stage, opened_pdfs):
        """
        Updates pipeline state and manages file cleanup.
        Crucially, it defers cleanup if the result is a generator.
        """

        import pikepdf

        from pdftl.core.types import OpResult

        # 1. Unpack OpResult if present
        if isinstance(result, OpResult):
            self.results.append(result)
            self.result_discardable = result.is_discardable

            # CLI Hooks (like printing text to console)
            if not getattr(self.input_context, "is_api", False):
                op_entry = registry.operations.get(stage.operation, {})
                if hook := op_entry.get("cli_hook"):
                    hook(result, stage)

            # Update the pipeline data
            result_val = result.pdf
        else:
            self.result_discardable = False
            result_val = result

        # 2. Update the Pipeline State Variable
        self.pipeline_pdf = result_val

        # 3. Smart Cleanup Logic
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
        if not stage.inputs and is_first:
            raise MissingArgumentError(
                "No initial input files provided. "
                "\n  Maybe you put an operation before the input file?"
                f"\n  Correct syntax: {WHOAMI} <input>... <operation> [<other arguments>]"
            )

        op_data = registry.operations.get(stage.operation, {})
        op_requires_output = " output " in op_data.get("usage", "")

        # Check if the stage has an output option
        has_output = bool(stage.options.get(c.OUTPUT))

        if is_last and (stage.operation == "filter" or op_requires_output) and not has_output:
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

        # Determine output pattern from local stage options or default
        output_pattern = stage.options.get(c.OUTPUT, "pg_%04d.pdf")

        call_context = {
            c.OPERATION_NAME: operation,
            c.INPUTS: stage.inputs,
            c.OPENED_PDFS: opened_pdfs,
            c.INPUT_FILENAME: _first_or_none(stage.inputs),
            c.INPUT_PASSWORD: _first_or_none(stage.input_passwords),
            c.INPUT_PDF: _first_or_none(opened_pdfs),
            c.OPERATION_ARGS: stage.operation_args,
            c.ALIASES: stage.handles,
            c.OVERLAY_PDF: _first_or_none(stage.operation_args),
            c.ON_TOP: "stamp" in operation,
            c.MULTI: "multi" in operation,
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
        if is_first:
            logger.debug("Reading PDF from stdin for first stage")
            if sys.stdin.isatty():
                raise UserCommandLineError("Expected PDF data from stdin, but none was provided.")
            # data = sys.stdin.buffer.read()
            # import pikepdf

            try:
                # if password:
                #     return pikepdf.open(io.BytesIO(data), password=password)
                # return pikepdf.open(io.BytesIO(data))
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
            raise UserCommandLineError(exception) from exception

    def _open_input_pdfs(self, stage, is_first):
        """Opens all PDF inputs required for a stage."""
        logger.debug("_open_input_pdfs")
        opened_pdfs = []

        # We assume global/final options (like keep_first_id) are attached to the LAST stage.
        final_stage_options = self.stages[-1].options if self.stages else {}

        for i, filename in enumerate(stage.inputs):
            password = stage.input_passwords[i]
            if filename in ["-", "_"]:
                pdf_obj = self._open_pdf_from_special_input(password, is_first)
            else:
                pdf_obj = self._open_pdf_from_file(filename, password)
            opened_pdfs.append(pdf_obj)
            if (
                final_stage_options.get("keep_first_id")
                and is_first
                and i == 0
                and len(opened_pdfs) > 0
            ):
                self.kept_id = list(opened_pdfs[0].trailer.ID)

        if final_stage_options.get("keep_final_id") and len(opened_pdfs) > 0:
            self.kept_id = list(opened_pdfs[-1].trailer.ID)

        return opened_pdfs


@register_help_topic(
    "pipeline",
    title="pipeline syntax",
    desc="Using `---` to pipe multiple operations together",
    examples=[
        HelpExample(
            desc="Shuffle two documents, then crop the resulting pages to A4",
            cmd="a.pdf b.pdf shuffle --- crop '(a4)' output out.pdf",
        ),
        HelpExample(
            desc=(
                "Shuffle doc_B with the even pages of doc_A, with B's pages first:\n"
                "'_' is required to place the piped-in pages second in the given order."
            ),
            cmd="doc_A.pdf cat even --- B=doc_B.pdf shuffle B _ output final.pdf",
        ),
        HelpExample(
            desc=(
                "Save a snapshot of a rotated file,"
                " then apply a stamp and save the final version"
            ),
            cmd=(
                "in.pdf rotate right output rotated_snapshot.pdf --- "
                "stamp watermark.pdf output final.pdf"
            ),
        ),
        HelpExample(
            desc=(
                "Crop all pages to A3 in landscape,\n"
                "and preview the effect of cropping odd pages to A4"
            ),
            cmd="in.pdf crop (A3_l) --- crop odd(A4) output out.pdf",
        ),
    ],
)
def _pipeline_help_topic():
    """Multiple operations can be chained together using `---` as a
    separator. The output of one stage becomes the input for the next
    stage.

    If the next stage has no input files, the result from the previous
    is used automatically. For multi-input commands where order matters,
    you can use the special `_` handle to refer to the piped-in input.

    You can use the `output` command in any stage (not just
    the last one) to save the current state of the
    document. The pipeline then continues to the next stage
    using that same document state.

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
