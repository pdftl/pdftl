# src/pdftl/api.py
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
API layer for pdftl.
Provides a functional interface to PDF operations and translates
CLI-specific exceptions into standard Python exceptions.
"""

from __future__ import annotations

import inspect
import logging
import types
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

if TYPE_CHECKING:
    import pikepdf

import pdftl.core.constants as c
from pdftl.core import executor
from pdftl.exceptions import MissingArgumentError, OperationError, UserCommandLineError
from pdftl.registry_init import initialize_registry

# Initialize the registry to ensure all operations are discovered
initialize_registry()

logger = logging.getLogger(__name__)


def _normalize_inputs(
    user_inputs: List[Union[str, "pikepdf.Pdf"]] | None,
    user_opened: Union[Dict[int, "pikepdf.Pdf"], List["pikepdf.Pdf"]] | None,
    password: str | None,
) -> tuple[List[str], Dict[int, "pikepdf.Pdf"]]:
    """
    Normalizes user-provided inputs into the internal format expected by commands.
    """
    final_inputs = []

    # Handle user_opened being a list (e.g. opened_pdfs=[pdf])
    final_opened = {}
    if user_opened:
        if isinstance(user_opened, list):
            final_opened = {i: pdf for i, pdf in enumerate(user_opened)}
        else:
            final_opened = user_opened.copy()

    if not user_inputs and final_opened:
        if final_opened:
            max_idx = max(final_opened.keys())
            # Fill inputs list with placeholders up to max index
            final_inputs = [f"<obj-{i}>" for i in range(max_idx + 1)]
        return final_inputs, final_opened

    if not user_inputs:
        return [], final_opened

    for i, item in enumerate(user_inputs):
        final_inputs, final_opened = _process_user_input(
            i, item, password, final_inputs, final_opened
        )

    return final_inputs, final_opened


def _process_user_input(i, item, password, final_inputs, final_opened):
    import pikepdf

    if i in final_opened:
        final_inputs.append(f"<explicit-obj-{i}>")

    elif isinstance(item, (str, bytes)):
        try:
            pdf = pikepdf.open(item, password=password) if password else pikepdf.open(item)
            final_opened[i] = pdf
            final_inputs.append(str(item))
        except Exception as e:
            raise ValueError(f"Failed to open input '{item}': {e}") from e

    elif isinstance(item, pikepdf.Pdf):
        final_opened[i] = item
        final_inputs.append(f"<memory-obj-{i}>")

    else:
        raise TypeError(
            f"Input at index {i} must be a file path or pikepdf.Pdf object, not {type(item)}"
        )

    return final_inputs, final_opened


def _map_positional_args(operation_name, positional_args):
    """
    Intelligently map positional arguments to inputs or operation_args
    based on the registry definition.
    """
    op_data = executor.registry.operations.get(operation_name, {})

    # Safely get positional args config, handling 2-tuple or 3-tuple registry entries
    args_conf = op_data.get("args", ([], {}))
    reg_pos_args = args_conf[0] if args_conf else []

    mapped_inputs = []
    mapped_op_args = []

    args_queue = list(positional_args)

    for param in reg_pos_args:
        if not args_queue:
            break

        if param in (c.INPUTS, c.INPUT_PDF, c.INPUT_FILENAME):
            if param == c.INPUTS:
                # If the command takes multiple inputs (e.g., cat),
                # assume all remaining positional args are inputs
                # unless we want to be stricter. For simplicity in API usage,
                # we usually treat *args as inputs for multi-input commands.
                mapped_inputs.extend(args_queue)
                args_queue = []
            else:
                # Single input command (e.g. dump_data), take one.
                mapped_inputs.append(args_queue.pop(0))

        elif param == c.OPERATION_ARGS:
            # Command takes generic args (e.g. rotate <angle>)
            # Consume the rest as op args
            mapped_op_args.extend(args_queue)
            args_queue = []

    # If anything remains (and wasn't mapped), fallback to treating as op_args
    # This covers edge cases or variadic args not strictly in the tuple
    if args_queue:
        mapped_op_args.extend(args_queue)

    return mapped_inputs, mapped_op_args


def call(operation_name: str, *args: Any, **kwargs: Any) -> Any:
    """
    Execute a registered operation by name.
    """
    return_full = kwargs.pop("full_result", False)
    run_hook = kwargs.pop("run_cli_hook", False)

    # 1. Gather Inputs
    raw_inputs = kwargs.get(c.INPUTS, [])
    if "pdf" in kwargs:
        raw_inputs = [kwargs.pop("pdf")] + raw_inputs

    # 2. Gather Op Args
    op_args = kwargs.get(c.OPERATION_ARGS, [])

    # 3. Handle Positional Arguments (User Friendliness)
    # This maps args like ('file.pdf') to inputs/op_args based on the command def.
    if args:
        pos_inputs, pos_op_args = _map_positional_args(operation_name, args)
        raw_inputs.extend(pos_inputs)
        op_args.extend(pos_op_args)

    # FIX: Ensure all operation arguments are strings.
    # The internal command parsers (cli-centric) expect strings and will fail with integers.
    # This allows API calls like .rotate(90) to work correctly.
    op_args = [str(a) for a in op_args]

    # 4. Normalize Inputs (Open files, etc.)
    raw_opened = kwargs.get(c.OPENED_PDFS, {})
    password = kwargs.get("password") or kwargs.get(c.INPUT_PASSWORD)
    final_inputs, final_opened = _normalize_inputs(raw_inputs, raw_opened, password)

    # Helper to get the first item safely
    first_input = final_inputs[0] if final_inputs else None

    # Safely get the first opened PDF
    first_pdf = None
    if final_opened:
        first_idx = sorted(final_opened.keys())[0]
        first_pdf = final_opened[first_idx]

    # Derived context values matching pipeline.py logic
    overlay_pdf = op_args[0] if op_args else None
    output_file = kwargs.get(c.OUTPUT)
    output_pattern = kwargs.get(c.OUTPUT_PATTERN, "pg_%04d.pdf")

    context = {
        "operation": operation_name,
        c.OPERATION_ARGS: op_args,
        c.OPTIONS: kwargs.copy(),
        c.INPUTS: final_inputs,
        c.OPENED_PDFS: final_opened,
        c.ALIASES: kwargs.get(c.ALIASES, {"DEFAULT": 0}),
        # Convenience keys required by many commands
        c.INPUT_FILENAME: first_input,
        c.INPUT_PDF: first_pdf,
        c.INPUT_PASSWORD: password,
        # Advanced keys for stamp/burst/overlay operations
        c.OVERLAY_PDF: overlay_pdf,
        c.ON_TOP: "stamp" in operation_name,
        c.MULTI: "multi" in operation_name,
        c.OUTPUT: output_file,
        c.OUTPUT_PATTERN: output_pattern,
        # Interactive input hook (defaults to built-in input for API usage)
        c.GET_INPUT: kwargs.get(c.GET_INPUT, input),
    }

    # Cleanup context options to prevent recursive keys in OPTIONS
    for key in [c.INPUTS, c.OPENED_PDFS, c.OPERATION_ARGS, c.ALIASES, "pdf", "password"]:
        if key in context[c.OPTIONS]:
            del context[c.OPTIONS][key]

    try:
        result = executor.run_operation(operation_name, context)
    except MissingArgumentError as e:
        raise TypeError(f"missing required argument: {str(e)}") from e
    except UserCommandLineError as e:
        raise ValueError(f"Invalid operation parameters: {str(e)}") from e

    from pdftl.core.types import OpResult

    if not isinstance(result, OpResult):
        return result

    if not result.success:
        raise OperationError(f"Operation '{operation_name}' failed: {result.summary}")

    if result.summary:
        logger.info(f"[{operation_name}] {result.summary}")

    # Optionally execute the CLI hook (e.g. for printing/saving output)
    if run_hook:
        op_data = executor.registry.operations.get(operation_name, {})

        # Safely access 'cli_hook' whether op_data is a dict or an object
        if isinstance(op_data, dict):
            hook = op_data.get("cli_hook")
        else:
            hook = getattr(op_data, "cli_hook", None)

        if hook:
            # Construct a mock stage object with options, as hooks expect a 'stage'
            # hooks.py typically accesses stage.options.get("output")
            stage_options = context[c.OPTIONS]
            mock_stage = types.SimpleNamespace(options=stage_options)
            hook(result, mock_stage)
        else:
            raise ValueError(
                f"Operation '{operation_name}' does not support run_cli_hook (no hook registered)."
            )

    if return_full:
        return result

    if result.data is not None:
        return result.data

    if result.pdf is not None:
        return result.pdf

    return None


def _create_signature(op_name):
    """
    Helper to generate a proper signature for dynamic functions.
    This allows help() to show useful arguments instead of just **kwargs.
    """
    import pikepdf

    from pdftl.core import executor

    # Try to determine if the op takes inputs or op_args to make help signature cleaner
    op_data = executor.registry.operations.get(op_name, {})

    # Safely unpack just the first element (positional args list),
    # ignoring whether the tuple has 2 or 3 elements.
    args_conf = op_data.get("args", ([], {}))
    reg_pos_args = args_conf[0] if args_conf else []

    parameters = []

    # If the registry says it takes inputs, allow positional args in signature roughly
    # We can't perfectly model the *args mapping logic in signature, but we can hint.

    # Just standardizing for now to avoid confusion
    parameters = [
        inspect.Parameter(
            c.INPUTS, inspect.Parameter.KEYWORD_ONLY, default=None, annotation=List[str]
        ),
        inspect.Parameter(
            c.OPENED_PDFS,
            inspect.Parameter.KEYWORD_ONLY,
            default=None,
            annotation=List[pikepdf.Pdf],
        ),
        inspect.Parameter(
            c.OPERATION_ARGS, inspect.Parameter.KEYWORD_ONLY, default=None, annotation=List[str]
        ),
        inspect.Parameter(
            "password", inspect.Parameter.KEYWORD_ONLY, default=None, annotation=str
        ),
        inspect.Parameter(c.OUTPUT, inspect.Parameter.KEYWORD_ONLY, default=None, annotation=str),
        inspect.Parameter(
            "run_cli_hook", inspect.Parameter.KEYWORD_ONLY, default=False, annotation=bool
        ),
        inspect.Parameter(
            "full_result", inspect.Parameter.KEYWORD_ONLY, default=False, annotation=bool
        ),
        inspect.Parameter(
            c.ALIASES, inspect.Parameter.KEYWORD_ONLY, default=None, annotation=Dict[str, Any]
        ),
        inspect.Parameter(
            c.OPTIONS, inspect.Parameter.KEYWORD_ONLY, default=None, annotation=Dict[str, Any]
        ),
    ]

    # Dynamically determine return annotation from the underlying function
    return_annotation = inspect.Signature.empty
    op_function = op_data.get("function")
    if op_function:
        try:
            return_annotation = inspect.signature(op_function).return_annotation
        except (ValueError, TypeError):
            # Fallback if signature extraction fails
            pass

    return inspect.Signature(parameters, return_annotation=return_annotation)


def __getattr__(name: str) -> Any:
    """
    The Dynamic Bridge.
    Intercepts calls to non-existent attributes and checks if they match
    a registered PDF operation.
    """
    if name in executor.registry.operations:

        def dynamic_op(*args, **kwargs):
            return call(name, *args, **kwargs)

        dynamic_op.__name__ = name
        # Attach docstring from registry or underlying function
        op_data = executor.registry.operations[name]

        # Access properties safely whether op_data is dict or object
        get_val = lambda k: (
            op_data.get(k) if isinstance(op_data, dict) else getattr(op_data, k, None)
        )

        op_function = get_val("function")
        long_desc = get_val("long_desc")
        short_desc = get_val("desc")

        # Prioritize the docstring of the actual Python function, which typically
        # describes the programmatic return values (e.g. OpResult, dicts)
        # rather than the CLI behavior.
        if op_function and op_function.__doc__:
            dynamic_op.__doc__ = op_function.__doc__
        elif long_desc:
            dynamic_op.__doc__ = long_desc
        elif short_desc:
            dynamic_op.__doc__ = short_desc

        # Attach a real signature for help() introspection
        dynamic_op.__signature__ = _create_signature(name)

        return dynamic_op

    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


def __dir__():
    """Expose registered operations for tab completion."""
    return list(globals().keys()) + list(executor.registry.operations.keys())


# Re-export run_operation so tests can patch 'pdftl.api.run_operation'
run_operation = executor.run_operation

__all__ = ["call", "run_operation"]
