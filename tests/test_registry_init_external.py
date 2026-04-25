import os
import sys
from unittest.mock import MagicMock, patch

import pytest

import pdftl.registry_init as registry_init

# --- 1. The Safety Net: Anti-Pollution Fixture ---


@pytest.fixture(autouse=True)
def clean_system_environment():
    """
    Prevents cross-test pollution by restoring sys.path, sys.modules,
    and the 'initialized' flag after every test.
    """
    initial_path = list(sys.path)
    initial_modules = list(sys.modules.keys())

    # Ensure flag is gone before starting
    if hasattr(registry_init.initialize_registry, "initialized"):
        delattr(registry_init.initialize_registry, "initialized")

    yield

    # Restore sys.path
    sys.path[:] = initial_path

    # Purge any fake modules injected during the test
    current_modules = list(sys.modules.keys())
    for mod in current_modules:
        if mod not in initial_modules and ("pdftl.external" in mod or "fake" in mod):
            del sys.modules[mod]

    # Reset flag again for the next test
    if hasattr(registry_init.initialize_registry, "initialized"):
        delattr(registry_init.initialize_registry, "initialized")


# --- 2. Tests for registry_init._discover_external_operations ---


def test_external_ops_platform_branching():
    """Covers Lines 28-31: Ensuring cross-platform path resolution logic."""
    # We patch Path directly in the registry_init namespace
    with patch("pdftl.registry_init.pathlib.Path") as mock_path:
        mock_path.return_value.exists.return_value = False

        # Test Windows Branch (Line 29)
        with patch("os.name", "nt"), patch.dict(os.environ, {"APPDATA": "C:\\MockApp"}):
            registry_init._discover_external_operations()
            # Verify the Windows-specific path was constructed
            assert "C:\\MockApp" in mock_path.call_args[0][0]

        # Test Linux Branch (Line 31)
        with (
            patch("os.name", "posix"),
            patch.dict(os.environ, {"XDG_CONFIG_HOME": "/mock/config"}),
        ):
            registry_init._discover_external_operations()
            # Verify the Linux-specific path was constructed
            assert "/mock/config" in mock_path.call_args[0][0]


def test_external_ops_execution_flow():
    """Covers Lines 40-71: sys.path, __init__ skip, None specs, and all Exceptions."""
    mock_files = [
        MagicMock(stem="__init__"),  # Hits Line 46
        MagicMock(stem="ok_op"),  # Hits Line 58
        MagicMock(stem="none_spec"),  # Hits Line 53
        MagicMock(stem="imp_err"),  # Hits Line 59
        MagicMock(stem="syn_err"),  # Hits Lines 61-67
        MagicMock(stem="exc_err"),  # Hits Lines 68-71
    ]
    mock_files[1].__str__.return_value = "/fake/ok_op.py"

    mock_op_dir = MagicMock()
    mock_op_dir.exists.return_value = True
    mock_op_dir.__str__.return_value = "/fake/ops"
    mock_op_dir.glob.return_value = mock_files

    with (
        patch("pdftl.registry_init.pathlib.Path", return_value=mock_op_dir),
        patch("importlib.util.spec_from_file_location") as mock_spec_func,
        patch("importlib.util.module_from_spec"),
        patch.dict("sys.modules", {}, clear=False),
    ):
        # Setup specific spec behaviors
        spec_ok = MagicMock()

        spec_imp = MagicMock()
        spec_imp.loader.exec_module.side_effect = ImportError("mock")

        spec_syn = MagicMock()
        syn_ex = SyntaxError("fail")
        syn_ex.lineno, syn_ex.msg = 1, "error"
        spec_syn.loader.exec_module.side_effect = syn_ex

        spec_exc = MagicMock()
        spec_exc.loader.exec_module.side_effect = Exception("boom")

        # Map returns to glob order (excluding __init__)
        mock_spec_func.side_effect = [spec_ok, None, spec_imp, spec_syn, spec_exc]

        registry_init._discover_external_operations()

        assert "/fake/ops" in sys.path
        assert mock_spec_func.call_count == 5


# --- 3. Tests for registry_init._discover_modules ---


def test_internal_discovery_edge_cases():
    """Covers Lines 89-102: Missing path, invalid identifiers, security violation."""

    # 1. Line 89: Package with no __path__
    mock_no_path = MagicMock(__name__="no_path_pkg")
    del mock_no_path.__path__
    registry_init._discover_modules([mock_no_path], "test")

    # 2. Line 94/99: Security and Invalid names
    mock_valid_pkg = MagicMock(__name__="pdftl.core")
    mock_valid_pkg.__path__ = ["/fake"]

    mock_bad_pkg = MagicMock(__name__="external_lib")  # Security violation
    mock_bad_pkg.__path__ = ["/fake"]

    mock_iter_data = [(None, "123_invalid", False), (None, "valid_mod", False)]

    with (
        patch("pkgutil.iter_modules", return_value=mock_iter_data),
        patch("importlib.import_module"),
    ):
        registry_init._discover_modules([mock_valid_pkg], "test")
        registry_init._discover_modules([mock_bad_pkg], "test")


# --- 4. Tests for registry_init.initialize_registry (The CI-Safe version) ---


def test_initialize_registry_idempotency():
    """Covers Lines 128-145: Ensures discovery runs exactly once."""
    # Using patch.object on the imported module for maximum CI reliability
    with (
        patch("importlib.import_module"),
        patch.object(registry_init, "_discover_modules"),
        patch.object(registry_init, "_discover_external_operations") as mock_ext,
    ):
        # Call 1: Runs everything
        registry_init.initialize_registry()
        assert registry_init.initialize_registry.initialized is True
        assert mock_ext.call_count == 1

        # Call 2: Should return early (Line 129)
        registry_init.initialize_registry()
        assert mock_ext.call_count == 1
