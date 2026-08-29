# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/arg_helpers.py

"""Utilities to help operations gather arguments in different formats"""

import logging
import re


from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar, cast

from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.page_specs import is_valid_page_spec

logger = logging.getLogger(__name__)

T = TypeVar("T")


def resolve_operation_spec(
    args_or_spec: list[Any] | T,
    parser_func: Callable[..., T],
    model_class: type[T] | None = None,
    data: Any = None,
) -> T:
    """
    Resolves an operation specification from CLI arguments, a file, or a direct object.

    Strategies (in order):
    1. Direct Object: If input is already type T (API usage), return it.
    2. File Reference: If input is ['@file'], load and parse the file.
    3. Manual Parse: Otherwise, pass strings to the command's parser function.

    :param args_or_spec: The input arguments (list of strings) or the Spec object itself.
    :param parser_func: The function to parse raw CLI strings (e.g., parse_move_args).
    :param model_class: The dataclass/type to instantiate when loading from files.
    """

    # 1. API Strategy: Direct Object Pass-through
    if model_class and isinstance(args_or_spec, model_class):
        return args_or_spec

    # 2. File Strategy: @filename
    # We check if it is a list, has exactly one element, and that element starts with @
    if (
        isinstance(args_or_spec, list)
        and len(args_or_spec) == 1
        and isinstance(args_or_spec[0], str)
        and args_or_spec[0].startswith("@")
    ):
        file_path = args_or_spec[0][1:]
        return _load_spec_from_file(file_path, model_class)

    # 3. CLI Strategy: Fallback to manual parser
    # Ensure it's a list before passing to parser
    if not isinstance(args_or_spec, list):
        raise TypeError(f"Expected list of strings or {model_class}, got {type(args_or_spec)}")

    safe_data = data if data is not None else {}

    # INSPECTION LOGIC:
    # Check if the parser accepts a second argument (data)
    import inspect

    sig = inspect.signature(parser_func)

    # Count how many parameters the function accepts
    if len(sig.parameters) >= 2:
        return parser_func(args_or_spec, safe_data)  # codeql[py/call/wrong-arguments]

    return parser_func(args_or_spec)  # codeql[py/call/wrong-arguments]


def _load_spec_from_file(path_str: str, model_class: type[T] | None = None) -> T:
    """
    Loads JSON (or YAML) from disk and converts it to model_class.
    """
    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"Argument file not found: {path}")

    with open(path, encoding="utf-8") as f:
        # Simple extension check
        if path.suffix.lower() in (".yaml", ".yml"):
            # Optional: Support YAML if PyYAML is installed
            try:
                import yaml

                data = yaml.safe_load(f)
            except ImportError as exc:
                raise ImportError(
                    "PyYAML is required to load .yaml files. Install it with: pip install pyyaml"
                ) from exc
        else:
            # Default to JSON
            import json

            data = json.load(f)

    # If no model class provided, return raw dict
    if not model_class:
        return data

    # Instantiate the class
    # If the class has a specific 'from_dict' factory (common in complex models), use it.
    if (factory := getattr(model_class, "from_dict", None)) is not None:
        if callable(factory):
            return cast(T, factory(data))  # pylint: disable=not-callable
        logger.warning(
            "Attribute 'from_dict' on %s is not callable. Falling back to constructor.",
            model_class.__name__,
        )

    # Otherwise, assume standard dataclass/constructor kwargs
    try:
        return model_class(**data)
    except TypeError as e:
        raise TypeError(f"Failed to instantiate {model_class.__name__}: {e}") from e


def expand_shorthand_args(args: list[str], is_selector_func=is_valid_page_spec) -> list[str]:
    """
    A universal framework-level shorthand expander for parenthesized syntax.
    """
    for arg in args:
        if not isinstance(arg, str):
            bad_type = type(arg).__name__
            hint = ""
            if bad_type == "InlineSubPipeline":
                hint = (
                    " Maybe you forgot to assign your inline pipeline to an input handle? "
                    "(e.g., B=JOB ... DONE)"
                )
            elif bad_type == "EachSubPipeline":
                hint = " Using EACH in that position does not seem to make sense."
            raise TypeError(
                f"Unexpected object of type '{bad_type}' found in operation arguments." + hint
            )
    # Guardrail: If the user already used parenthesized syntax, touch nothing.
    if not args or any("(" in arg for arg in args):
        logger.debug("Returning unchanged")
        return args

    # Step 1: Classify the first token using your core validation function
    first_token = args[0]
    if is_selector_func(first_token):
        logger.debug("%s is selector", first_token)
        selector = first_token
        options = args[1:]
    else:
        logger.debug("%s is NOT selector", first_token)
        selector = ""
        options = args

    # Step 2: Pack all sequential options cleanly into commas
    opts_str = ",".join(options)

    if selector:
        return [f"{selector}({opts_str})"]
    else:
        return [f"({opts_str})"]


_SIZE_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([A-Za-z]*)\s*$")

_SIZE_SUFFIXES = {
    "": 1,
    "B": 1,
    "K": 1024,
    "KB": 1024,
    "M": 1024**2,
    "MB": 1024**2,
    "G": 1024**3,
    "GB": 1024**3,
}


def parse_size_to_bytes(size_str: str, *, context: str = "value") -> int:
    """Parses a plain byte count or a `<number><suffix>` size string
    (suffix one of B/K/KB/M/MB/G/GB, case-insensitive) into an integer
    byte count. `context` names the option/argument for the error
    message, e.g. "min_bytes" or "'deduplicate_images'".

    This is pdftl's single canonical string-to-bytes size parser --
    if you need one, use this rather than writing a local variant.
    """
    match = _SIZE_RE.match(size_str)
    if not match:
        raise InvalidArgumentError(
            f"{context}: invalid size '{size_str}'. Expected a byte count or a size like '64KB'."
        )
    number_str, suffix = match.groups()
    suffix = suffix.upper()
    if suffix not in _SIZE_SUFFIXES:
        raise InvalidArgumentError(
            f"{context}: invalid size suffix '{suffix}' in '{size_str}'. "
            f"Expected one of: {', '.join(s for s in _SIZE_SUFFIXES if s)}."
        )
    return int(float(number_str) * _SIZE_SUFFIXES[suffix])
