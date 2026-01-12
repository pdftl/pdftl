# tests/compat_pdftk/conftest.py
import pytest
import sys
import shutil
import subprocess
from pathlib import Path

# Scoped to this folder
HERE = Path(__file__).parent
TEST_FILES = HERE / "files"

@pytest.fixture
def get_test_file():
    """Returns absolute path to a file inside tests/compat_pdftk/files/"""
    def _get(filename):
        # The Java tests often reference "test/files/name.pdf"
        # We strip the directory prefix to find it in our local 'files' folder
        name = Path(filename).name
        return str(TEST_FILES / name)
    return _get

@pytest.fixture
def run_pdftl(tmp_path):
    """
    Runs the pdftl CLI. 
    This fixture is ONLY available to tests in this directory.
    """
    def _run(args, input_data=None, expect_exit_code=0):
        # Point to your main package
        cmd = [sys.executable, "-m", "pdftl"] + args
        
        stdin = None
        if input_data:
            stdin = input_data.encode() if isinstance(input_data, str) else input_data

        result = subprocess.run(
            cmd,
            input=stdin,
            capture_output=True,
            cwd=tmp_path
        )
        
        if expect_exit_code is not None:
             # Helpful error message including stderr if it fails
            assert result.returncode == expect_exit_code, \
                f"Cmd failed. Code: {result.returncode}\nStderr: {result.stderr.decode()}"

        return result
    return _run

# @pytest.fixture
# def compare_pdfs_as_svg():
#     """Visual comparison fixture"""
#     def _compare(pdf_bytes_a, pdf_bytes_b):
#         def to_svg(pdf_data):
#             if not shutil.which("pdftocairo"):
#                 pytest.skip("pdftocairo missing")
#             proc = subprocess.run(
#                 ["pdftocairo", "-svg", "-", "-"],
#                 input=pdf_data,
#                 capture_output=True,
#                 check=True
#             )
#             return proc.stdout

#         assert to_svg(pdf_bytes_a) == to_svg(pdf_bytes_b)
#     return _compare

@pytest.fixture
def compare_pdfs_as_svg(tmp_path):
    """Visual comparison fixture with debugging for broken PDFs"""
    def _compare(pdf_bytes_a, pdf_bytes_b):
        # 1. Sanity Check: Is data empty?
        if not pdf_bytes_a:
            raise ValueError("Expected PDF data is empty! Check the test setup.")
        if not pdf_bytes_b:
            raise ValueError("Actual PDF data is empty! 'pdftl' produced no output.")

        # 2. Sanity Check: Does it look like a PDF?
        # PDF files must start with %PDF-
        if not pdf_bytes_b.startswith(b"%PDF-"):
            # It's likely an error message printed to stdout
            snippet = pdf_bytes_b[:100].decode(errors="replace")
            raise ValueError(f"Actual data is not a PDF header. Started with: {snippet!r}")

        def to_svg(pdf_data, label):
            if not shutil.which("pdftocairo"):
                pytest.skip("pdftocairo missing")
            
            proc = subprocess.run(
                ["pdftocairo", "-svg", "-", "-"],
                input=pdf_data,
                capture_output=True
            )
            
            if proc.returncode != 0:
                # DEBUG: Save the bad file so you can open it
                debug_file = tmp_path / f"debug_{label}.pdf"
                debug_file.write_bytes(pdf_data)
                
                error_msg = proc.stderr.decode(errors="replace")
                raise RuntimeError(
                    f"pdftocairo failed on '{label}'. \n"
                    f"Saved bad PDF to: {debug_file}\n"
                    f"Stderr: {error_msg}"
                )
            return proc.stdout

        # Compare
        assert to_svg(pdf_bytes_a, "expected") == to_svg(pdf_bytes_b, "actual")
    return _compare

def pytest_collection_modifyitems(items):
    """Automatically mark all tests in this directory as pdftk_compat"""
    for item in items:
        item.add_marker("pdftk_compat")