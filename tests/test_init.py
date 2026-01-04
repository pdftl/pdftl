import importlib
import sys
from unittest.mock import patch


import pdftl


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
    We force a reload of the module while simulating that _version is missing.
    """
    # By setting the module to None in sys.modules, imports of it will raise ModuleNotFoundError
    with patch.dict(sys.modules, {"pdftl._version": None}):
        importlib.reload(pdftl)
        assert pdftl.__version__ == "0.0.0+unknown"

    # Restore the original state so subsequent tests aren't affected
    importlib.reload(pdftl)
    assert pdftl.__version__ != "0.0.0+unknown"
