# src/pdftl/utils/arg_helpers.py
import json
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

# Optional: Support YAML if PyYAML is installed
try:
    import yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False

T = TypeVar("T")


def resolve_operation_spec(
    args_or_spec: list[str] | T,
    parser_func: Callable[[list[str]], T],
    model_class: type[T] | None = None,
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

    return parser_func(args_or_spec)


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
            if not HAS_YAML:
                raise ImportError(
                    "PyYAML is required to load .yaml files. Install it with: pip install pyyaml"
                )
            data = yaml.safe_load(f)
        else:
            # Default to JSON
            data = json.load(f)

    # If no model class provided, return raw dict
    if not model_class:
        return data

    # Instantiate the class
    # If the class has a specific 'from_dict' factory (common in complex models), use it.
    if factory := getattr(model_class, "from_dict", None):
        return factory(data)

    # Otherwise, assume standard dataclass/constructor kwargs
    return model_class(**data)
