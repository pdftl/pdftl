import logging
import os
import sys
from unittest.mock import patch

import pytest

from pdftl.registry_init import _discover_external_operations


@pytest.fixture
def clean_sys_modules():
    """
    Robust cleanup for tests that dynamically generate python modules.
    """
    # 1. Snapshot original state
    original_path = list(sys.path)
    original_modules_keys = set(sys.modules.keys())

    # 2. DEFINITELY REMOVE specific collision candidates immediately.
    #    This protects this test from previous tests on the same worker.
    #    (e.g., if 'my_plugin' was loaded by a previous run)
    known_collisions = {"my_plugin", "bad_import", "bad_syntax", "bad_code"}
    for mod in known_collisions:
        sys.modules.pop(mod, None)

    yield

    # 3. Restoration
    sys.path[:] = original_path

    # Remove any new modules loaded during the test
    current_modules_keys = set(sys.modules.keys())
    for m in current_modules_keys - original_modules_keys:
        del sys.modules[m]

    # Double check: ensure our dynamic modules are gone even if logic above missed them
    for mod in known_collisions:
        sys.modules.pop(mod, None)


def test_discover_external_windows_path(clean_sys_modules, tmp_path):
    """
    Covers line 29.
    Simulate running on Windows to verify APPDATA usage.
    We must mock pathlib.Path in the target module to prevent it from
    trying to instantiate a real WindowsPath on Linux.
    """
    fake_appdata = tmp_path / "AppData" / "Roaming"
    fake_pdftl_ops = fake_appdata / "pdftl" / "operations"
    fake_pdftl_ops.mkdir(parents=True)

    # 1. Patch os.name to trigger the Windows branch.
    # 2. Patch pathlib.Path within registry_init so it returns our compatible PosixPath
    #    instead of trying to create a WindowsPath.
    with (
        patch("os.name", "nt"),
        patch.dict(os.environ, {"APPDATA": str(fake_appdata)}),
        patch("pdftl.registry_init.pathlib.Path", return_value=fake_appdata),
        patch("sys.path", []),
    ):

        _discover_external_operations()

        # Verify the directory was added to sys.path
        assert str(fake_pdftl_ops) in sys.path


def test_discover_external_unix_path(clean_sys_modules, tmp_path):
    """
    Covers lines 31-36.
    Simulate running on Unix/Linux (default path).
    """
    fake_home = tmp_path / "home_user"
    fake_pdftl_ops = fake_home / ".config" / "pdftl" / "operations"
    fake_pdftl_ops.mkdir(parents=True)

    with patch("pathlib.Path.home", return_value=fake_home), patch("os.name", "posix"):

        _discover_external_operations()

        assert str(fake_pdftl_ops) in sys.path


def test_discover_external_import_success(clean_sys_modules, tmp_path, caplog):
    """
    Covers lines 40-52.
    Test successful loading of a valid python module.
    """
    fake_home = tmp_path / "home_valid"
    ops_dir = fake_home / ".config" / "pdftl" / "operations"
    ops_dir.mkdir(parents=True)

    # Create a valid module
    (ops_dir / "my_plugin.py").write_text("print('Plugin Loaded')", encoding="utf-8")

    # Create __init__.py which should be skipped (line 45-46)
    (ops_dir / "__init__.py").write_text("", encoding="utf-8")

    with (
        patch("pathlib.Path.home", return_value=fake_home),
        patch("os.name", "posix"),
        caplog.at_level(logging.DEBUG),
    ):

        _discover_external_operations()

        module_status = "NOT LOADED"
        if "my_plugin" in sys.modules:
            module_status = f"LOADED from {sys.modules['my_plugin'].__file__}"

        long_dump = "\n".join(
            [
                f"{name} -> {getattr(module, '__file__', '(built-in)')}"
                for name, module in list(sys.modules.items())
            ]
        )

        debug_info = (
            f"\n--- DEBUG DUMP ---\n"
            f"Module Status: {module_status}\n"
            f"Sys.Path top 3: {sys.path[:3]}\n"
            f"Caplog Records: {[r.message for r in caplog.records]}\n"
            f"Sys.modules dump: \n{long_dump}\n"
            f"------------------"
        )
        # Check success log
        assert "Loaded external operation: my_plugin" in caplog.text, debug_info
        # Check __init__ skip
        assert "Loaded external operation: __init__" not in caplog.text, debug_info


def test_discover_external_import_error(clean_sys_modules, tmp_path, caplog):
    """
    Covers lines 53-54.
    Test handling of ImportError within the plugin.
    """
    fake_home = tmp_path / "home_import_err"
    ops_dir = fake_home / ".config" / "pdftl" / "operations"
    ops_dir.mkdir(parents=True)

    # Create module that imports non-existent package
    (ops_dir / "bad_import.py").write_text("import this_does_not_exist_at_all", encoding="utf-8")

    with patch("pathlib.Path.home", return_value=fake_home), patch("os.name", "posix"):

        _discover_external_operations()

        assert "Could not import external operation 'bad_import'" in caplog.text


def test_discover_external_syntax_error(clean_sys_modules, tmp_path, caplog):
    """
    Covers lines 55-61.
    Test handling of SyntaxError in the plugin.
    """
    fake_home = tmp_path / "home_syntax_err"
    ops_dir = fake_home / ".config" / "pdftl" / "operations"
    ops_dir.mkdir(parents=True)

    # Create module with invalid syntax
    (ops_dir / "bad_syntax.py").write_text("def broken_function(:", encoding="utf-8")

    with patch("pathlib.Path.home", return_value=fake_home), patch("os.name", "posix"):

        _discover_external_operations()

        assert "Syntax error in external operation 'bad_syntax'" in caplog.text


def test_discover_external_general_exception(clean_sys_modules, tmp_path, caplog):
    """
    Covers lines 62-65.
    Test handling of generic Exception (e.g. ValueError) at module level.
    """
    fake_home = tmp_path / "home_general_err"
    ops_dir = fake_home / ".config" / "pdftl" / "operations"
    ops_dir.mkdir(parents=True)

    # Create module that raises an exception on load
    (ops_dir / "bad_code.py").write_text(
        "raise ValueError('Something went wrong')", encoding="utf-8"
    )

    with patch("pathlib.Path.home", return_value=fake_home), patch("os.name", "posix"):

        _discover_external_operations()

        assert "Unexpected error loading external operation 'bad_code'" in caplog.text
