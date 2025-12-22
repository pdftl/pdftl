# src/pdftl/core/executor.py
import logging

import pdftl.core.constants as c
from pdftl.core.registry import registry

logger = logging.getLogger(__name__)


def _get_keyval_or_attr(src, key, default=None):
    if isinstance(src, dict):
        return src.get(key, default)
    return getattr(src, key, default)


def run_operation(operation_name, call_context):
    """
    Central execution point for all operations.
    Resolves arguments from context based on registry metadata.
    """
    op_data = _get_keyval_or_attr(registry.operations, operation_name)
    if not op_data:
        raise ValueError(f"Operation '{operation_name}' is not registered.")

    op_function = _get_keyval_or_attr(op_data, "function")
    arg_style = _get_keyval_or_attr(op_data, "args")

    if not op_function or not arg_style:
        raise ValueError(f"Operation '{operation_name}' is missing function or arg_style.")

    # We do NOT catch MissingArgumentError or UserCommandLineError here anymore.
    # Bubbling these up allows api.py to apply the correct "missing required argument" prefix.
    pos_args, kw_args = _resolve_arguments(arg_style, call_context)

    try:
        return op_function(*pos_args, **kw_args)
    except Exception as e:
        # Only log unexpected internal errors, don't re-wrap custom pdftl exceptions
        if not hasattr(e, "__module__") or "pdftl.exceptions" not in e.__module__:
            logger.error("Internal error in operation '%s': %s", operation_name, e)
        raise


def _resolve_arguments(arg_style, context):
    """Maps call_context values to the function's expected signature."""
    pos_arg_names = arg_style[0]
    kw_arg_map = arg_style[1]
    kw_const_arg_map = arg_style[2] if len(arg_style) > 2 else {}

    # Apply the "Floor" of default values to prevent KeyErrors
    full_context = {
        c.ALIASES: {},
        c.OPTIONS: {},
        c.OPERATION_ARGS: [],
        c.INPUTS: [],
        c.OPENED_PDFS: [],
        c.INPUT_FILENAME: None,
        c.INPUT_PASSWORD: None,
        c.INPUT_PDF: None,
        c.OVERLAY_PDF: None,
        c.OUTPUT: None,
        c.OUTPUT_PATTERN: "pg_%04d.pdf",
        c.GET_INPUT: None,
        c.ON_TOP: False,
        c.MULTI: False,
    }
    full_context.update(context)

    pos_args = [full_context[name] for name in pos_arg_names]
    kw_args = {key: full_context[val] for key, val in kw_arg_map.items()}
    kw_args.update(kw_const_arg_map)

    return pos_args, kw_args
