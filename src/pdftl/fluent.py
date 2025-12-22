from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional, Union

if TYPE_CHECKING:
    from pathlib import Path
    from pikepdf import Pdf

import pdftl.api as api
import pdftl.core.constants as c
from pdftl.core.executor import registry
from pdftl.registry_init import initialize_registry

initialize_registry()


class PdfPipeline:
    def __init__(self, pdf: pikepdf.Pdf):
        self._pdf = pdf

    @classmethod
    def open(cls, filename: Union[str, "Path"], password: Optional[str] = None) -> PdfPipeline:
        import pikepdf

        pdf = pikepdf.open(filename, password=password) if password else pikepdf.open(filename)
        return cls(pdf)

    def save(self, filename: Union[str, "Path"], **kwargs: Any) -> None:
        self._pdf.save(filename, **kwargs)

    def __dir__(self):
        """Allow tab completion for dynamic registry operations."""
        default_attrs = list(super().__dir__())
        dynamic_attrs = list(registry.operations.keys())
        return default_attrs + dynamic_attrs

    def __getattr__(self, name: str) -> Any:
        import pikepdf

        if name in registry.operations:

            def fluent_method(*args, **kwargs):
                # 1. Inspect the registry to see what this command expects
                op_data = registry.operations[name]

                # Retrieve the positional args configuration
                args_conf = op_data.args if hasattr(op_data, "args") else ([], {})
                reg_pos_args = list(args_conf[0]) if args_conf else []

                # 2. Prepare the primary input (the pipeline's current PDF)
                current_inputs = kwargs.get(c.INPUTS, [])
                if not isinstance(current_inputs, list):
                    current_inputs = [current_inputs]

                # Prepend our pipeline PDF
                full_inputs = [self._pdf] + current_inputs

                # 3. Intelligent Argument Mapping
                mapped_op_args = kwargs.get(c.OPERATION_ARGS, [])

                # If the command takes a primary input, our self._pdf satisfies it.
                if reg_pos_args and reg_pos_args[0] in (c.INPUT_PDF, c.INPUT_FILENAME):
                    reg_pos_args.pop(0)

                # Map user's *args to remaining targets
                user_args = list(args)

                if user_args:
                    if c.INPUTS in reg_pos_args:
                        full_inputs.extend(user_args)
                    elif c.OPERATION_ARGS in reg_pos_args:
                        mapped_op_args.extend(user_args)
                    else:
                        mapped_op_args.extend(user_args)

                # 4. Construct the clean call
                kwargs[c.INPUTS] = full_inputs
                kwargs[c.OPERATION_ARGS] = mapped_op_args

                # Execute
                result = api.call(name, **kwargs)

                # Update state if a PDF was returned
                if isinstance(result, pikepdf.Pdf):
                    self._pdf = result
                    return self

                return result

            fluent_method.__name__ = name

            # --- METADATA FIX ---
            # We look up the corresponding function in the API layer.
            # pdftl.api generates nice signatures and docstrings dynamically.
            # We simply copy them to our fluent wrapper so help() works.
            try:
                api_func = getattr(api, name)
                fluent_method.__doc__ = api_func.__doc__
                if hasattr(api_func, "__signature__"):
                    fluent_method.__signature__ = api_func.__signature__
            except Exception:
                # Fallback if introspection fails (e.g. partial initialization)
                pass

            return fluent_method

        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

    @property
    def native(self) -> pikepdf.Pdf:
        return self._pdf

    def get(self) -> pikepdf.Pdf:
        return self._pdf


def pipeline(
    pdf_or_path: Union["Pdf", str, "Path"], password: Optional[str] = None
) -> PdfPipeline:
    """
    Entry point for the fluent API.
    Accepts a pikepdf.Pdf object or a filename/path.
    """
    from pathlib import Path

    if isinstance(pdf_or_path, (str, Path)):
        return PdfPipeline.open(pdf_or_path, password=password)

    return PdfPipeline(pdf_or_path)
