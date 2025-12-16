import sys
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from pdftl.cli.main import main

@contextmanager
def strict_imports(forbidden_modules):
    """
    Context manager that 'poisons' specific modules in sys.modules.

    If the code attempts to import any module in `forbidden_modules` within
    this block, it will immediately raise an ImportError.
    """
    forbidden_modules = forbidden_modules or []
    stash = {}

    # 1. Snapshot and Poison
    for mod in forbidden_modules:
        # If it's currently loaded, stash it and remove it
        if mod in sys.modules:
            stash[mod] = sys.modules[mod]
            del sys.modules[mod]

        # Poison: Setting to None causes ImportError on import attempt
        sys.modules[mod] = None

    try:
        yield
    finally:
        # 2. Restore original state
        for mod in forbidden_modules:
            # Remove the poison
            if mod in sys.modules and sys.modules[mod] is None:
                del sys.modules[mod]

            # Restore stashed module if it existed
            if mod in stash:
                sys.modules[mod] = stash[mod]


def test_cli_help_imports_rich_only(capsys):
    """
    Scenario 1: 'pdftl help'
    Expected:
      - Imports: rich (for help text)
      - MUST NOT Import: pikepdf, ocrmypdf, pypdfium2 (heavy libs)
    """
    forbidden = ["pikepdf", "ocrmypdf", "pypdfium2"]
    test_argv = ["pdftl", "--help"]

    with strict_imports(forbidden):
        with patch.object(sys, "argv", test_argv):
            # We expect a SystemExit (0) when help is shown
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 0

    # check (and discard) help output
    captured = capsys.readouterr()
    assert "pdftl" in captured.out
    assert "cat" in captured.out
    assert "encrypt" in captured.out

    assert not captured.err


def test_cli_processing_imports_pikepdf_only(tmp_path, two_page_pdf, capsys):
    """
    Scenario 2: 'pdftl in.pdf output out.pdf'
    Expected:
      - Imports: pikepdf (to read PDF)
      - MUST NOT Import: rich, ocrmypdf, pypdfium2 (UI or extra features)
    """
    forbidden = ["rich", "ocrmypdf", "pypdfium2"]

    input_pdf = two_page_pdf
    output_pdf = tmp_path / "out.pdf"
    test_argv = ["pdftl", str(input_pdf), "output", str(output_pdf)]

    with strict_imports(forbidden):
        with patch.object(sys, "argv", test_argv):
            main()

    assert "pikepdf" in sys.modules, "pikepdf should have been imported for processing"

    # output should be empty, let's check while we're at it
    captured = capsys.readouterr()
    assert not captured.out
    assert not captured.err
