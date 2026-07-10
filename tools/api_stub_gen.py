import inspect
import subprocess

import pdftl.core.constants as c
from pdftl.core.registry import registry
from pdftl.fluent import pipeline
from pdftl.registry_init import initialize_registry


def ruff_then_write(name, lines):
    filename = f"src/pdftl/{name}.pyi"
    content = "\n".join(lines) + "\n"
    result = subprocess.run(
        ["ruff", "format", "-", "--stdin-filename", filename],
        input=content,
        text=True,
        capture_output=True,
        check=True,
    )
    formatted = result.stdout
    with open(filename, "w", encoding="utf-8") as f:
        f.write(formatted)


def generate():
    initialize_registry()

    mapping = {
        c.INPUTS: ("inputs", "Optional[List[str]]"),
        c.OPENED_PDFS: ("opened_pdfs", "Optional[List[pikepdf.Pdf]]"),
        c.OPERATION_ARGS: ("operation_args", "Optional[List[str]]"),
        c.INPUT_PASSWORD: ("password", "Optional[str]"),
        c.OUTPUT: ("output", "Optional[str]"),
    }

    # Standard arguments available to almost all API calls
    global_api_params = [
        ("run_cli_hook", "bool = False"),
        ("full_result", "bool = False"),
        ("password", "Optional[str] = None"),
    ]

    # --- Generate API Stubs Header ---
    # Added Union to imports because we use Union[pikepdf.Pdf, str]
    api_stubs = [
        "# src/pdftl/api.pyi",
        "from typing import Any, Dict, List, Optional, Union",
        "import pikepdf",
        "",
    ]

    # --- Generate Fluent Stubs Header ---
    sig = inspect.signature(pipeline)
    fluent_stubs = [
        "# src/pdftl/fluent.pyi",
        "from typing import Any, Dict, List, Optional, Union",
        "import pikepdf",
        "from pikepdf import Pdf",
        "from pathlib import Path",
        "",
        f"def pipeline{sig}: ...",
        "",
        "class PdfPipeline:",
        "    def __init__(self, pdf: pikepdf.Pdf): ...",
        "    @classmethod",
        "    def open(cls, filename: str, password: Optional[str] = None) -> 'PdfPipeline': ...",
        "    def save(self, filename: Union[str, Path], input_context: Any = None, "
        "             set_pdf_id: Optional[bytes] = None, **kwargs: Any) -> 'PdfPipeline': ...",
        "    def get(self) -> pikepdf.Pdf: ...",
        "    def __enter__(self) -> 'PdfPipeline': ...",
        "    def __exit__(self, t: Any, v: Any, tb: Any) -> None: ...",
    ]

    for name, op_data in sorted(registry.operations.items()):
        if getattr(op_data, "caller", "").startswith("pdftl.external."):
            continue
        args_meta = getattr(op_data, "args", ([], {}, {}))

        # Track names for this specific function to prevent duplicates
        api_names = set()
        api_params = []
        fluent_params = ["self"]
        fluent_names = {"self"}

        # 1. API: Add the positional 'pdf' argument
        api_params.append("pdf: Optional[Union[pikepdf.Pdf, str]] = None")
        api_names.add("pdf")

        # 2. Add dynamic params from registry
        # Combine positional and keyword-mapped constants
        registry_constants = list(args_meta[0]) + list(args_meta[1].values())

        for const in registry_constants:
            if const in mapping:
                arg_name, type_hint = mapping[const]
                # API Side
                if arg_name not in api_names:
                    api_params.append(f"{arg_name}: {type_hint} = ...")
                    api_names.add(arg_name)
                # Fluent Side
                if const != c.OPENED_PDFS and arg_name not in fluent_names:
                    fluent_params.append(f"{arg_name}: {type_hint} = ...")
                    fluent_names.add(arg_name)

        # 3. Add Global Flags to API (if not already added via registry)
        for arg_name, type_def in global_api_params:
            if arg_name not in api_names:
                api_params.append(f"{arg_name}: {type_def}")
                api_names.add(arg_name)

        api_stubs.append(f"def {name}({', '.join(api_params)}) -> pikepdf.Pdf: ...")
        fluent_stubs.append(f"    def {name}({', '.join(fluent_params)}) -> 'PdfPipeline': ...")

    ruff_then_write("api", api_stubs)
    ruff_then_write("fluent", fluent_stubs)

    print("DONE: Generated clean API and Fluent stubs.")


if __name__ == "__main__":
    generate()
