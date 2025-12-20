import subprocess
import sys

import pytest


class TestCLILazyImports:
    def _run_isolated_cli_check(self, args, forbidden_modules):
        scanner_script = f"""
import sys
import importlib

culprits = set()
forbidden = {forbidden_modules}

# The Magic: A hook that runs every time a module is loaded
class TraceImports:
    def __init__(self):
        self.loading_stack = []

    def __call__(self, name, globals=None, locals=None, fromlist=None, level=0):
        # We check if this import is one of our forbidden fruits
        if any(name == f or name.startswith(f + ".") for f in forbidden):
            # Look at the stack to find the last 'pdftl.commands' module
            import inspect
            for frame in inspect.stack():
                module = inspect.getmodule(frame[0])
                if module and module.__name__.startswith("pdftl.commands"):
                    culprits.add(module.__name__)
                    break
        return original_import(name, globals, locals, fromlist, level)

original_import = __builtins__.__import__
__builtins__.__import__ = TraceImports()

# Run the CLI
sys.argv = {args}
try:
    from pdftl.cli.main import main
    main()
except SystemExit:
    pass

if culprits:
    print("--- SURGICAL EDIT LIST ---")
    for c in sorted(culprits):
        print(f"culprit: {{c}}")
    sys.exit(1)

print("SUCCESS")
"""
        result = subprocess.run(
            [sys.executable, "-c", scanner_script], capture_output=True, text=True
        )

        if result.returncode != 0:
            pytest.fail(f"Lazy import check failed!\\n\\n{result.stdout}")

    def test_cli_help_imports_rich_only(self):
        """
        Ensures 'pdftl --help' does NOT load heavy PDF libraries.
        """
        self._run_isolated_cli_check(
            args=["pdftl", "--help"],
            forbidden_modules=["pikepdf", "ocrmypdf", "pypdfium2", "pathlib"],
        )

    def test_cli_processing_imports_pikepdf_only(self, tmp_path, two_page_pdf):
        """
        Ensures processing command loads pikepdf but NOT UI libs like rich.
        """
        output_pdf = tmp_path / "out.pdf"

        # Note: We must pass file paths as strings to the subprocess script
        args = ["pdftl", str(two_page_pdf), "output", str(output_pdf)]

        self._run_isolated_cli_check(
            args=args, forbidden_modules=["rich", "ocrmypdf", "pypdfium2"]
        )
