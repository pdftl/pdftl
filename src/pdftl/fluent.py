from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path
    from pikepdf import Pdf

import pdftl.api as api
import pdftl.core.constants as c
from pdftl.core.executor import registry
from pdftl.registry_init import initialize_registry

initialize_registry()


class PdfPipeline:
    def __init__(self, pdf: Pdf):
        self._pdf = pdf

    @classmethod
    def open(cls, filename: str | Path, password: str | None = None) -> PdfPipeline:
        import pikepdf

        pdf = pikepdf.open(filename, password=password) if password else pikepdf.open(filename)
        return cls(pdf)

    def save(
        self,
        filename: str | Path,
        input_context: Any = None,
        set_pdf_id: bytes | None = None,
        **kwargs: Any,
    ) -> PdfPipeline:
        """
        Save the current PDF to a file, applying pdftl options.

        Args:
            filename: Output path.
            input_context: Optional context for handling passwords/prompts.
            set_pdf_id: Optional byte string to force the PDF ID.
            **kwargs: Options passed to save_pdf (e.g. flatten=True, encrypt_128bit=True).
        Returns:
          self: To allow onward pipeline chaining
        """
        from pdftl.output.save import save_pdf

        # We explicitly separate the args that save_pdf defines in its signature
        # from the general 'options' dictionary.
        save_pdf(
            self._pdf,
            str(filename),
            input_context=input_context,
            options=kwargs,  # All other kwargs (flatten, etc) go here
            set_pdf_id=set_pdf_id,
        )
        return self

    def __dir__(self):
        """Allow tab completion for dynamic registry operations."""
        default_attrs = list(super().__dir__())
        dynamic_attrs = list(registry.operations.keys())
        return default_attrs + dynamic_attrs

    def __getattr__(self, name: str) -> Any:
        if name not in registry.operations:
            raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

        def fluent_method(*args, **kwargs):
            # 1. Prepare arguments based on registry metadata
            op_data = registry.operations[name]
            final_kwargs = self._map_fluent_args(op_data, args, kwargs)

            # 2. Execute via the API
            result = api.call(name, **final_kwargs)

            # 3. Handle state (Chain or Return)
            from pikepdf import Pdf

            if isinstance(result, Pdf):
                self._pdf = result
                return self
            return result

        # 4. Attach metadata (Docstrings/Signatures) for better DX
        return self._apply_metadata(fluent_method, name)

    def _map_fluent_args(self, op_data: Any, user_args: tuple, kwargs: dict) -> dict:
        """Translates fluent call args into the standard API kwargs format."""
        # Initialize structures
        args_conf = op_data.args if hasattr(op_data, "args") else ([], {})
        reg_pos_args = list(args_conf[0]) if args_conf else []

        current_inputs = kwargs.get(c.INPUTS, [])
        if not isinstance(current_inputs, list):
            current_inputs = [current_inputs]

        # Logic: Pipeline's current PDF is always the primary input
        full_inputs = [self._pdf] + current_inputs
        mapped_op_args = kwargs.get(c.OPERATION_ARGS, [])

        # If the command expects a PDF first, self._pdf already satisfied it
        if reg_pos_args and reg_pos_args[0] in (c.INPUT_PDF, c.INPUT_FILENAME):
            reg_pos_args.pop(0)

        # Map remaining user *args
        remaining_args = list(user_args)
        if remaining_args:
            if c.INPUTS in reg_pos_args:
                full_inputs.extend(remaining_args)
            else:
                mapped_op_args.extend(remaining_args)

        # Re-package into kwargs for api.call
        kwargs[c.INPUTS] = full_inputs
        kwargs[c.OPERATION_ARGS] = mapped_op_args
        return kwargs

    def _apply_metadata(self, func: Callable, name: str) -> Callable:
        """Copies docstrings and signatures from the API function to the wrapper."""
        func.__name__ = name
        try:
            api_func = getattr(api, name)
            func.__doc__ = api_func.__doc__
            if hasattr(api_func, "__signature__"):
                func.__signature__ = api_func.__signature__
        except AttributeError:
            # Silence issues with dynamic API generation or partial init
            pass
        return func

    @property
    def native(self) -> Pdf:
        return self._pdf

    def get(self) -> Pdf:
        return self._pdf


def pipeline(pdf_or_path: Pdf | str | Path, password: str | None = None) -> PdfPipeline:
    """
    Entry point for the fluent API.
    Accepts a pikepdf.Pdf object or a filename/path.
    """
    from pathlib import Path

    if isinstance(pdf_or_path, (str, Path)):
        return PdfPipeline.open(pdf_or_path, password=password)

    return PdfPipeline(pdf_or_path)
