import subprocess
import sys

import pytest

ALWAYS_FORBIDDEN = [
    "ocrmypdf",
    "pypdfium2",
    "yaml",
    "reportlab",
    "pyhanko",
    "fonttools",
    "numpy",
    "numba",
]


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
            count = 0
            for frame_info in inspect.stack():
               frame = frame_info.frame
               module = inspect.getmodule(frame)
               if module and module.__name__.startswith("pdftl"):
                   code = None
                   if frame_info.code_context:
                       code = frame_info.code_context[0].rstrip()
                   culprits.add((
                       count,
                       module.__name__,
                       # frame_info.filename,
                       frame_info.lineno,
                       code,
                       name,
                   ))
                   count += 1
                   # break
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
    grouped_culprits={{}}
    for c in culprits:
        count, mod, line, code, imported = c
        if count > 0:
            continue
        key = (count, mod, line, code)
        if key not in grouped_culprits:
            grouped_culprits[key] = []
        grouped_culprits[key].append(imported)
    for key in grouped_culprits:
        count, mod, line, code = key
        imported_list = grouped_culprits[key]
        imported_list.sort()
        imported_str = '\\n    '.join(imported_list)
        print(f"[{{count}}]{{mod}}:{{line}}:{{code}}\\n  Imported: {{imported_str}}")
    sys.exit(1)

print("SUCCESS")
"""
        result = subprocess.run(
            [sys.executable, "-c", scanner_script], capture_output=True, text=True
        )

        if result.returncode != 0:
            pytest.fail(
                f"Lazy import check failed!\\n\\nstdout:\n{result.stdout}\n\nstderr:{result.stderr}"
            )

    def test_cli_default_help_imports_rich_without_markdown(self):
        """
        Ensures 'pdftl --help' does NOT load heavy PDF (or other un-needed) libraries.
        """
        self._run_isolated_cli_check(
            args=["pdftl", "--help"],
            forbidden_modules=ALWAYS_FORBIDDEN + ["pikepdf", "rich.markdown"],
        )

    def test_cli_rich_help_imports_rich_only(self):
        """
        Ensures 'pdftl --help help' does NOT load heavy PDF (or other un-needed) libraries.
        """
        self._run_isolated_cli_check(
            args=["pdftl", "--help", "help"],
            forbidden_modules=ALWAYS_FORBIDDEN
            + [
                "pikepdf",
            ],
        )

    def test_cli_processing_imports_pikepdf_only(self, tmp_path, two_page_pdf):
        """
        Ensures processing command loads pikepdf but NOT UI libs like rich, or yaml.
        """
        output_pdf = tmp_path / "out.pdf"

        # Note: We must pass file paths as strings to the subprocess script
        args = ["pdftl", str(two_page_pdf), "output", str(output_pdf)]

        self._run_isolated_cli_check(
            args=args,
            forbidden_modules=ALWAYS_FORBIDDEN
            + [
                "rich",
            ],
        )
