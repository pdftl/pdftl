# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/api.py

"""
API layer for pdftl.
Provides a functional interface to PDF operations and translates
CLI-specific exceptions into standard Python exceptions.
"""

from __future__ import annotations

import inspect
import logging
import types
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pikepdf

import pdftl.core.constants as c
from pdftl.core import executor
from pdftl.exceptions import MissingArgumentError, OperationError, UserCommandLineError
from pdftl.registry_init import initialize_registry

initialize_registry()
logger = logging.getLogger(__name__)


def _normalize_inputs(
    user_inputs: list[str | pikepdf.Pdf] | None,
    user_opened: dict[int, pikepdf.Pdf] | list[pikepdf.Pdf] | None,
    password: str | None,
) -> tuple[list[str], dict[int, pikepdf.Pdf]]:
    """
    Normalizes user-provided inputs into the internal format expected by commands.
    """
    final_inputs = []
    final_opened = {}
    if user_opened:
        if isinstance(user_opened, list):
            final_opened = {i: pdf for i, pdf in enumerate(user_opened)}
        else:
            final_opened = user_opened.copy()
    if not user_inputs and final_opened:
        if final_opened:
            max_idx = max(final_opened.keys())
            final_inputs = [f"<obj-{i}>" for i in range(max_idx + 1)]
        return (final_inputs, final_opened)
    if not user_inputs:
        return ([], final_opened)
    for i, item in enumerate(user_inputs):
        final_inputs, final_opened = _process_user_input(
            i, item, password, final_inputs, final_opened
        )
    return (final_inputs, final_opened)


def _process_user_input(i, item, password, final_inputs, final_opened):
    import pathlib

    import pikepdf

    if i in final_opened:
        final_inputs.append(f"<explicit-obj-{i}>")
    elif isinstance(item, (str, bytes, pathlib.Path, pathlib.PosixPath)):
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
    return (final_inputs, final_opened)


def _map_positional_args(operation_name, positional_args):
    """
    Intelligently map positional arguments to inputs or operation_args
    based on the registry definition.
    """
    op_data = executor.registry.operations.get(operation_name, {})
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
                mapped_inputs.extend(args_queue)
                args_queue = []
            else:
                mapped_inputs.append(args_queue.pop(0))
        elif param == c.OPERATION_ARGS:
            mapped_op_args.extend(args_queue)
            args_queue = []
    if args_queue:
        mapped_op_args.extend(args_queue)
    return (mapped_inputs, mapped_op_args)


def call(operation_name: str, *args: Any, **kwargs: Any) -> Any:
    """Execute a registered operation by name."""
    return_full = kwargs.pop("full_result", False)
    run_hook = kwargs.pop("run_cli_hook", False)
    context = _prepare_operation_context(operation_name, args, kwargs)
    try:
        result = executor.run_operation(operation_name, context)
    except MissingArgumentError as e:
        raise TypeError(f"missing required argument: {str(e)}") from e
    except UserCommandLineError as e:
        raise ValueError(f"Invalid operation parameters: {str(e)}") from e
    return _process_operation_result(operation_name, result, context, return_full, run_hook)


def _prepare_operation_context(operation_name: str, args: tuple, kwargs: dict) -> dict:
    """Gathers and normalizes all inputs and arguments into a context dict."""
    raw_inputs = kwargs.get(c.INPUTS, [])
    if "pdf" in kwargs:
        raw_inputs = [kwargs.pop("pdf")] + raw_inputs
    op_args = kwargs.get(c.OPERATION_ARGS, [])
    if args:
        pos_inputs, pos_op_args = _map_positional_args(operation_name, args)
        raw_inputs.extend(pos_inputs)
        op_args.extend(pos_op_args)
    op_args = [str(a) for a in op_args]
    raw_opened = kwargs.get(c.OPENED_PDFS, {})
    password = kwargs.get("password") or kwargs.get(c.INPUT_PASSWORD)
    final_inputs, final_opened = _normalize_inputs(raw_inputs, raw_opened, password)
    first_input = final_inputs[0] if final_inputs else None
    first_pdf = None
    if final_opened:
        first_idx = sorted(final_opened.keys())[0]
        first_pdf = final_opened[first_idx]
    context = {
        "operation": operation_name,
        c.OPERATION_ARGS: op_args,
        c.OPTIONS: kwargs.copy(),
        c.INPUTS: final_inputs,
        c.OPENED_PDFS: final_opened,
        c.ALIASES: kwargs.get(c.ALIASES, {"DEFAULT": 0}),
        c.INPUT_FILENAME: first_input,
        c.INPUT_PDF: first_pdf,
        c.INPUT_PASSWORD: password,
        c.OVERLAY_PDF: op_args[0] if op_args else None,
        c.ON_TOP: "stamp" in operation_name,
        c.MULTI: "multi" in operation_name,
        c.OUTPUT: kwargs.get(c.OUTPUT),
        c.OUTPUT_PATTERN: kwargs.get(c.OUTPUT_PATTERN, "pg_%04d.pdf"),
        c.GET_INPUT: kwargs.get(c.GET_INPUT, input),
    }
    for key in [c.INPUTS, c.OPENED_PDFS, c.OPERATION_ARGS, c.ALIASES, "pdf", "password"]:
        context[c.OPTIONS].pop(key, None)
    return context


def _process_operation_result(
    op_name: str, result: Any, context: dict, return_full: bool, run_hook: bool
) -> Any:
    """Unpacks OpResult and optionally runs CLI hooks."""
    from pdftl.core.types import OpResult

    if not isinstance(result, OpResult):
        return result
    if not result.success:
        raise OperationError(f"Operation '{op_name}' failed: {result.summary}")
    if result.summary:
        logger.info("[%s] %s", op_name, result.summary)
    if run_hook:
        _run_cli_hook(op_name, result, context)
    if return_full:
        return result
    return result.data if result.data is not None else result.pdf


def _run_cli_hook(op_name: str, result: Any, context: dict):
    """Executes the registered CLI hook for an operation."""
    op_data = executor.registry.operations.get(op_name, {})
    hook = (
        op_data.get("cli_hook")
        if isinstance(op_data, dict)
        else getattr(op_data, "cli_hook", None)
    )
    if not hook:
        raise ValueError(f"Operation '{op_name}' does not support run_cli_hook.")
    mock_stage = types.SimpleNamespace(options=context[c.OPTIONS])
    hook(result, mock_stage)


def _create_signature(op_name):
    """
    Helper to generate a proper signature for dynamic functions.
    This allows help() to show useful arguments instead of just **kwargs.
    """
    import pikepdf

    from pdftl.core import executor

    op_data = executor.registry.operations.get(op_name, {})
    parameters = []
    parameters = [
        inspect.Parameter(
            c.INPUTS, inspect.Parameter.KEYWORD_ONLY, default=None, annotation=list[str]
        ),
        inspect.Parameter(
            c.OPENED_PDFS,
            inspect.Parameter.KEYWORD_ONLY,
            default=None,
            annotation=list[pikepdf.Pdf],
        ),
        inspect.Parameter(
            c.OPERATION_ARGS, inspect.Parameter.KEYWORD_ONLY, default=None, annotation=list[str]
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
            c.ALIASES, inspect.Parameter.KEYWORD_ONLY, default=None, annotation=dict[str, Any]
        ),
        inspect.Parameter(
            c.OPTIONS, inspect.Parameter.KEYWORD_ONLY, default=None, annotation=dict[str, Any]
        ),
    ]
    return_annotation = inspect.Signature.empty
    op_function = op_data.get("function")
    if op_function:
        try:
            return_annotation = inspect.signature(op_function).return_annotation
        except (ValueError, TypeError):
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
        op_data = executor.registry.operations[name]

        def get_val(k):
            return op_data.get(k) if isinstance(op_data, dict) else getattr(op_data, k, None)

        op_function = get_val("function")
        long_desc = get_val("long_desc")
        short_desc = get_val("desc")
        if op_function and op_function.__doc__:
            dynamic_op.__doc__ = op_function.__doc__
        elif long_desc:
            dynamic_op.__doc__ = long_desc
        elif short_desc:
            dynamic_op.__doc__ = short_desc
        dynamic_op.__signature__ = _create_signature(name)
        return dynamic_op
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


def __dir__():
    """Expose registered operations for tab completion."""
    return list(globals().keys()) + list(executor.registry.operations.keys())


run_operation = executor.run_operation
__all__ = ["call", "run_operation"]
