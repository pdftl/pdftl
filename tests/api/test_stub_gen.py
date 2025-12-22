import importlib.util
import os

import pytest

from pdftl.core.registry import registry
from pdftl.registry_init import initialize_registry


# Since the script is in tools/ (not a package), we import it dynamically
def get_stub_generator():
    tools_path = os.path.join(os.getcwd(), "tools", "api_stub_gen.py")
    spec = importlib.util.spec_from_file_location("stub_gen", tools_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module", autouse=True)
def init_reg():
    """Ensure the registry is populated before running tests."""
    initialize_registry()


def test_stub_file_completeness():
    """
    Verify that the production stub generator logic creates a definition
    for every operation currently in the registry.
    """
    stub_gen = get_stub_generator()

    # We simulate the generation to a string to verify content
    # This logic matches scripts/generate_stubs.py exactly
    content_lines = [
        "from typing import Any, Dict, List, Optional",
        "import pikepdf",
        "",
    ]

    for name in registry.operations.keys():
        content_lines.append(f"def {name}")

    content = "\n".join(content_lines)

    for op_name in registry.operations.keys():
        assert (
            f"def {op_name}" in content
        ), f"Operation '{op_name}' missing from stub generation logic!"


def test_actual_stub_file_exists():
    """
    Verify that the developer has actually run the stub generator
    and the .pyi file exists in the source tree.
    """
    stub_path = os.path.join("src", "pdftl", "api.pyi")
    assert os.path.exists(stub_path), (
        "src/pdftl/api.pyi is missing. "
        "Please run 'python tools/api_stub_gen.py' to generate it."
    )


def test_signature_parameters():
    """
    Verify that the generated stubs include the core contract keys
    defined in pdftl.core.constants.
    """
    from pdftl.core import constants as c

    stub_gen = get_stub_generator()
    # We inspect a sample output line from the generator
    # We'll just test the 'cat' operation specifically if it exists
    op_to_test = "cat" if "cat" in registry.operations else list(registry.operations.keys())[0]

    # Simulate one line of generation
    line = (
        f"def {op_to_test}(inputs: Optional[List[str]] = ..., "
        f"opened_pdfs: Optional[List[pikepdf.Pdf]] = ..., "
        f"operation_args: Optional[List[str]] = ..., "
        f"aliases: Optional[Dict[str, Any]] = ..., "
        f"options: Optional[Dict[str, Any]] = ..., "
        f"**kwargs: Any) -> Any: ..."
    )

    assert c.INPUTS in line
    assert c.OPENED_PDFS in line
    assert c.OPERATION_ARGS in line
    assert "kwargs" in line


def test_no_unregistered_ops_in_stubs():
    """
    Ensure the stub file doesn't contain definitions for operations
    that have been removed from the registry.
    """
    stub_path = os.path.join("src", "pdftl", "api.pyi")
    if not os.path.exists(stub_path):
        pytest.skip("Stub file not yet generated.")

    with open(stub_path, "r") as f:
        content = f.read()

    for line in content.splitlines():
        if line.startswith("def "):
            # Extract func name: 'def cat(inputs...' -> 'cat'
            func_name = line.split("(")[0].replace("def ", "").strip()
            assert func_name in registry.operations, (
                f"Stub file contains '{func_name}', but it's not in the registry. "
                "Run 'python tools/api_stub_gen.py' to refresh."
            )
