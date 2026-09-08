import ast
import pathlib
import pytest


def test_sign_pdf_requires_run_sync():
    """Statically verify that `sign_pdf` is never called directly
    without `run_sync`, across both source code and tests.
    """
    # Scan both src and tests
    directories = [pathlib.Path("src/pdftl"), pathlib.Path("tests")]
    violations = []

    for base_dir in directories:
        if not base_dir.exists():
            continue
        for path in base_dir.glob("**/*.py"):
            if "async_helpers.py" in str(path):
                continue

            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func_name = ""
                    if isinstance(node.func, ast.Name):
                        func_name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        func_name = node.func.attr

                    if func_name in {"sign_pdf", "validate_pdf_signature"}:
                        violations.append(f"{path}:{node.lineno}")

    if violations:
        violation_list = "\n  - ".join(violations)
        pytest.fail(
            f"❌ Direct call(s) to async-backed PyHanko function(s) detected without `run_sync`:\n  - {violation_list}\n\n"
            f"Please wrap these calls using `run_sync` from `pdftl.utils.async_helpers`."
        )
