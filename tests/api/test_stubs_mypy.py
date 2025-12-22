# tests/test_stubs_mypy.py
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


def test_mypy_validation_isolated(tmp_path):
    """
    Verifies that Mypy enforces the API contract using a simulated
    site-packages structure to prevent environment resolution issues.

    Checks for:
    1. [arg-type]: Passing wrong types to defined arguments.
    2. [call-arg]: Passing arguments not defined in the .pyi.
    3. [assignment]: Assigning the return value to an incompatible type.
    """
    # 1. Resolve project root relative to this test file for CI stability
    # Current file: pdftl/tests/test_stubs_mypy.py
    # Parent: pdftl/tests
    # Grandparent: pdftl (project root)
    test_dir = Path(__file__).parent

    # If the test is in tests/api/ or tests/core/, we might need another .parent
    # Safest is to find the 'src' directory relative to where we are.
    if (test_dir.parent / "src").exists():
        project_root = test_dir.parent
    elif (test_dir.parent.parent / "src").exists():
        project_root = test_dir.parent.parent
    else:
        # Fallback for when running from root
        project_root = Path(os.getcwd())

    src_path = project_root / "src" / "pdftl"

    # 2. Create a valid package structure in the temp directory
    pkg_dir = tmp_path / "pdftl"
    pkg_dir.mkdir()

    src_api_py = src_path / "api.py"
    src_api_pyi = src_path / "api.pyi"

    if not src_api_pyi.exists():
        # Helpful error message with the path we tried
        pytest.skip(f"Stub file not found at {src_api_pyi}. Run stub generator first.")

    # Copy project source and stubs to the isolated environment
    shutil.copy(src_api_py, pkg_dir / "api.py")
    shutil.copy(src_api_pyi, pkg_dir / "api.pyi")
    (pkg_dir / "__init__.py").write_text("from pdftl.api import *")
    (pkg_dir / "py.typed").write_text("")

    # 3. Write the smoke test file to be analyzed
    smoke_test = tmp_path / "smoke.py"
    smoke_test.write_text(
        textwrap.dedent(
            """
        from pdftl import api
        import pikepdf
        
        def check_errors() -> None:
            # Error 1: [arg-type]
            api.cat(inputs=123)
            
            # Error 2: [call-arg]
            api.cat(inputs=["f.pdf"], unknown_kwarg=True)
            
            # Error 3: [assignment]
            # api.cat returns pikepdf.Pdf; assigning to int must fail.
            val: int = api.cat(inputs=["f.pdf"])
    """
        )
    )

    # 4. Configure environment
    env = os.environ.copy()
    # We add tmp_path to PYTHONPATH so Mypy finds our 'pdftl' package
    env["PYTHONPATH"] = str(tmp_path) + os.pathsep + env.get("PYTHONPATH", "")

    # 5. Run Mypy
    # In CI, we use --python-executable to ensure mypy uses the same env as pytest
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            str(smoke_test),
            "--show-error-codes",
            "--no-incremental",
            "--disallow-untyped-calls",
            "--ignore-missing-imports",  # Important for CI if pikepdf stubs aren't installed
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    output = result.stdout + result.stderr

    # 6. Assertions
    assert "[arg-type]" in output, f"Mypy failed to flag invalid argument type. Output: {output}"
    assert (
        "[call-arg]" in output
    ), f"Mypy failed to flag unknown keyword argument. Output: {output}"
    assert (
        "[assignment]" in output
    ), f"Mypy failed to flag incompatible return assignment. Output: {output}"
