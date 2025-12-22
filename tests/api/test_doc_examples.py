import re
from pathlib import Path

import pikepdf
import pytest


def create_dummy_pdf(path, pages=5):
    """Helper to create valid PDFs for the examples to chew on."""
    with pikepdf.new() as pdf:
        for _ in range(pages):
            pdf.add_blank_page(page_size=(500, 500))
        pdf.save(path)


def test_api_tutorial_examples(tmp_path, monkeypatch):
    """
    Parses docs/api_tutorial.md and executes every ```python block
    to ensure the examples actually work.
    """
    # 1. Robustly locate project root by searching for pyproject.toml
    # This works whether the test is in tests/, tests/api/, or elsewhere.
    current_path = Path(__file__).resolve()
    project_root = None

    for parent in [current_path.parent] + list(current_path.parents):
        if (parent / "pyproject.toml").exists():
            project_root = parent
            break

    if project_root is None:
        # Fallback: assume we are in tests/api/ -> up 3 levels
        project_root = current_path.parent.parent.parent

    doc_path = project_root / "docs" / "api_tutorial.md"

    if not doc_path.exists():
        pytest.skip(f"{doc_path} not found, skipping doc tests.")

    content = doc_path.read_text(encoding="utf-8")

    # 2. Setup a clean environment (chdir to tmp_path)
    monkeypatch.chdir(tmp_path)

    # 3. Create dummy assets referenced in the tutorial
    for x in [
        "input.pdf",
        "report.pdf",
        "form.pdf",
        "cover.pdf",
        "chapter1.pdf",
        "chapter2.pdf",
        "chapter3.pdf",
        "chapter3.pdf",
        "watermark.pdf",
    ]:

        create_dummy_pdf(x)

    # 4. Extract Python blocks
    # Regex finds content between ```python and ```
    # Note: We rely on the tutorial using explicit ```python fences
    code_blocks = re.findall(r"```python\n(.*?)\n```", content, re.DOTALL)

    if not code_blocks:
        # Fallback for ``` only (if syntax highlighting hints are missing)
        code_blocks = re.findall(r"```\n(.*?)\n```", content, re.DOTALL)

    assert len(code_blocks) > 0, "No code blocks found in tutorial!"

    # 5. Execute them
    # We share a locals dictionary so imports from one block (e.g. 'import pdftl')
    # persist to the next block, simulating a sequential read.
    shared_locals = {}

    for i, block in enumerate(code_blocks):
        try:
            # We compile first to get better error messages
            code = compile(block, filename=f"example_{i}", mode="exec")
            exec(code, shared_locals)
        except Exception as e:
            pytest.fail(f"Doc example #{i+1} failed:\n\n{block}\n\nError: {e}")
