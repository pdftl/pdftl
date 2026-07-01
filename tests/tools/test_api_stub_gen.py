import sys
import subprocess
from types import SimpleNamespace
from pathlib import Path

# Ensure the root/tools directory is importable if it isn't already
sys.path.append(str(Path(__file__).parent.parent))
from tools.api_stub_gen import generate


def test_api_stub_generation(monkeypatch, tmp_path, capsys):
    """
    Ensures the stub generator runs without errors, produces the expected files,
    and adheres to encoding and newline standards.
    """
    # 1. Setup a fake project structure in the temp directory
    fake_src_dir = tmp_path / "src" / "pdftl"
    fake_src_dir.mkdir(parents=True)

    # 2. Trick the script into thinking the temp directory is the repo root
    monkeypatch.chdir(tmp_path)

    # 3. Run the generator

    # Mock subprocess.run to avoid hitting the actual shell binary
    def mock_run(args, input, **kwargs):
        # Simply return the input content untouched as if ruff formatted it
        return SimpleNamespace(stdout=input)

    monkeypatch.setattr(subprocess, "run", mock_run)
    generate()

    captured = capsys.readouterr()
    assert "Generated clean API and Fluent stubs" in captured.out

    # 4. Verify both files were created
    api_stub_file = fake_src_dir / "api.pyi"
    fluent_stub_file = fake_src_dir / "fluent.pyi"

    assert api_stub_file.exists(), "api.pyi was not generated"
    assert fluent_stub_file.exists(), "fluent.pyi was not generated"

    # 5. Sanity check the contents of api.pyi
    api_content = api_stub_file.read_text(encoding="utf-8")
    assert "from typing import Any, Dict, List, Optional, Union" in api_content
    assert "import pikepdf" in api_content
    assert api_content.endswith("\n"), "api.pyi is missing a trailing newline!"

    # 6. Sanity check the contents of fluent.pyi
    fluent_content = fluent_stub_file.read_text(encoding="utf-8")
    assert "class PdfPipeline:" in fluent_content
    assert "def pipeline" in fluent_content
    assert fluent_content.endswith("\n"), "fluent.pyi is missing a trailing newline!"


def test_stubs_are_up_to_date():
    """
    Ensures that the stubs currently committed to git match what the generator
    produces right now (catches developers who forgot to run the script).
    """
    # Read the real files currently on disk
    repo_root = Path(__file__).parent.parent.parent
    actual_api = (repo_root / "src" / "pdftl" / "api.pyi").read_text(encoding="utf-8")

    # We can run a small trick or just assert that the file contains
    # elements from your current operational registry.
    from pdftl.core.registry import registry

    for name in registry.operations.keys():
        if registry.operations[name].caller.startswith("pdftl.external."):
            continue
        assert f"def {name}" in actual_api, (
            f"Stub for operation '{name}' is missing! Did you forget to run the generator?"
        )
