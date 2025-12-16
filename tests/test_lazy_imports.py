import subprocess
import sys

import pytest


class TestCLILazyImports:

    def _run_isolated_cli_check(self, args, forbidden_modules):
        """
        Runs the CLI in a subprocess and checks sys.modules *inside* that process.
        """
        # We construct a mini-script that runs your CLI and then inspects memory.
        # This script runs INSIDE the subprocess.
        scanner_script = f"""
import sys
import pytest # Mocking argv usually requires ensuring sys is clean, simpler to set it here
sys.argv = {args}

# Wrap execution to catch the expected SystemExit from CLI tools
try:
    from pdftl.cli.main import main
    main()
except SystemExit:
    pass
except Exception as e:
    print(f"CRASH: {{e}}")
    sys.exit(1)

# --- INSPECTION TIME ---
import sys
loaded_modules = set(sys.modules.keys())
forbidden = {forbidden_modules}

# Check if any forbidden module is in the loaded list
violations = [m for m in forbidden if any(m == k or k.startswith(m + ".") for k in loaded_modules)]

if violations:
    print(f"VIOLATION: Found forbidden modules loaded: {{violations}}")
    sys.exit(1)

print("SUCCESS")
"""

        # Run the script in a fresh python process
        result = subprocess.run(
            [sys.executable, "-c", scanner_script], capture_output=True, text=True
        )

        # 1. Did the script crash or find violations?
        if result.returncode != 0:
            # Print stdout/stderr so you can debug "Why" it failed
            pytest.fail(f"Lazy import check failed:\n{result.stdout}\n{result.stderr}")

    def test_cli_help_imports_rich_only(self):
        """
        Ensures 'pdftl --help' does NOT load heavy PDF libraries.
        """
        self._run_isolated_cli_check(
            args=["pdftl", "--help"],
            forbidden_modules=["pikepdf", "ocrmypdf", "pypdfium2"],
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
