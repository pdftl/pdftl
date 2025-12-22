# tools/api_stub_gen.py
import pdftl.core.constants as c
from pdftl.core.registry import registry
from pdftl.registry_init import initialize_registry


def generate():
    initialize_registry()

    mapping = {
        c.INPUTS: ("inputs", "Optional[List[str]]"),
        c.OPENED_PDFS: ("opened_pdfs", "Optional[List[pikepdf.Pdf]]"),
        c.OPERATION_ARGS: ("operation_args", "Optional[List[str]]"),
        c.INPUT_PASSWORD: ("password", "Optional[str]"),
        c.OUTPUT: ("output", "Optional[str]"),
    }

    # --- Generate API Stubs ---
    api_stubs = [
        "# src/pdftl/api.pyi",
        "from typing import Any, Dict, List, Optional",
        "import pikepdf",
        "",
    ]

    # --- Generate Fluent Stubs ---
    # Using 'PdfPipeline' as string return type for 3.10 compatibility
    fluent_stubs = [
        "# src/pdftl/fluent.pyi",
        "from typing import Any, Dict, List, Optional",
        "import pikepdf",
        "",
        "class PdfPipeline:",
        "    def __init__(self, pdf: pikepdf.Pdf): ...",
        "    @classmethod",
        "    def open(cls, filename: str, password: Optional[str] = None) -> 'PdfPipeline': ...",
        "    def save(self, output_path: str) -> 'PdfPipeline': ...",
        "    def get(self) -> pikepdf.Pdf: ...",
        "    def __enter__(self) -> 'PdfPipeline': ...",
        "    def __exit__(self, t: Any, v: Any, tb: Any) -> None: ...",
    ]

    for name, op_data in registry.operations.items():
        args_meta = getattr(op_data, "args", ([], {}, {}))
        consumed_constants = set(args_meta[0]) | set(args_meta[1].values())

        # API signature (returns Pdf)
        api_params = []
        # Fluent signature (removes opened_pdfs, returns PdfPipeline)
        fluent_params = ["self"]

        for const in consumed_constants:
            if const in mapping:
                arg_name, type_hint = mapping[const]
                api_params.append(f"{arg_name}: {type_hint} = ...")
                if const != c.OPENED_PDFS:  # Fluent API hides the 'opened_pdfs' inject
                    fluent_params.append(f"{arg_name}: {type_hint} = ...")

        api_stubs.append(f"def {name}({', '.join(api_params)}) -> pikepdf.Pdf: ...")
        fluent_stubs.append(f"    def {name}({', '.join(fluent_params)}) -> 'PdfPipeline': ...")

    with open("src/pdftl/api.pyi", "w") as f:
        f.write("\n".join(api_stubs))

    with open("src/pdftl/fluent.pyi", "w") as f:
        f.write("\n".join(fluent_stubs))

    print("DONE: Generated API and Fluent stubs (Python 3.10 compatible).")


if __name__ == "__main__":
    generate()
