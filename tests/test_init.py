# tests/test_init.py
import importlib
import sys
from unittest.mock import patch

import pdftl

from tests.conftest import allow_pdftl_reload


def test_init_dir():
    """
    Test that dir(pdftl) includes API commands (covering line 49).
    This confirms the convenience imports like pdftl.cat work for tab completion.
    """
    attributes = dir(pdftl)

    # Check for standard exports
    assert "PdfPipeline" in attributes

    # Check for a dynamic API attribute included via __dir__ logic
    # 'cat' is a core function in pdftl.api
    assert "cat" in attributes
    assert "shuffle" in attributes


def test_version_import_error():
    """
    Test fallback version when _version cannot be imported (covering lines 31-32).

    Unlike other reload sites in this codebase, reloading the top-level pdftl
    package itself is safe: pdftl/__init__.py's body only *rebinds* names it
    imports from already-cached submodules (pdftl.api, pdftl.fluent, etc.) -
    it doesn't redefine any classes, and importlib.reload() is non-recursive
    (it doesn't reload the submodules pdftl.api/pdftl.fluent themselves).
    So `from pdftl.fluent import PdfPipeline` re-fetches the identical cached
    class object on reload; class identity is preserved regardless of what
    other tests already did `from pdftl import PdfPipeline`. The only thing
    that actually changes here is __version__, which is exactly what this
    test needs to exercise.

    (A subprocess-isolated version of this test was tried instead, but
    pytest-cov doesn't see coverage inside a subprocess without extra
    COVERAGE_PROCESS_START plumbing, which silently dropped these lines to
    0% coverage - not worth it when the reload itself is provably safe.)
    """
    # By setting the module to None in sys.modules, imports of it will raise ModuleNotFoundError
    with patch.dict(sys.modules, {"pdftl._version": None}):
        with allow_pdftl_reload():
            importlib.reload(pdftl)
        assert pdftl.__version__ == "0.0.0+unknown"

    # Restore the original state so subsequent tests aren't affected
    with allow_pdftl_reload():
        importlib.reload(pdftl)
    assert pdftl.__version__ != "0.0.0+unknown"
