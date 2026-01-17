import logging
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

import pdftl.registry_init as reg_init
from pdftl.registry_init import _discover_external_operations, _discover_modules


@pytest.fixture(autouse=True)
def reset_init_flag():
    """Ensure initialize_registry.initialized flag is reset before each test."""
    if hasattr(reg_init.initialize_registry, "initialized"):
        delattr(reg_init.initialize_registry, "initialized")
    yield
    if hasattr(reg_init.initialize_registry, "initialized"):
        delattr(reg_init.initialize_registry, "initialized")


def make_fake_module(name, is_pkg=False, submodules=None):
    """Helper to create a fake module with optional __path__ and submodules."""
    mod = types.ModuleType(name)
    if is_pkg:
        mod.__path__ = [f"/fake/{name.replace('.', '/')}"]
        # Allow pkgutil.iter_modules to see submodules
        mod._submodules = submodules or []
    return mod


def test_discover_modules_imports_all(monkeypatch):
    """_discover_modules() should import all submodules under fake packages."""

    # Create fake packages
    fake_operations = make_fake_module(
        "pdftl.operations", is_pkg=True, submodules=["mod_a", "mod_b"]
    )
    fake_core = make_fake_module("pdftl.core", is_pkg=True, submodules=["mod_a", "mod_b"])

    # Patch iter_modules to yield submodules
    def fake_iter_modules(path):
        for sub in ["mod_a", "mod_b"]:
            yield (None, sub, False)  # False -> not a nested package

    monkeypatch.setattr(reg_init.pkgutil, "iter_modules", fake_iter_modules)

    imported = []

    def fake_import_module(name):
        imported.append(name)
        return types.ModuleType(name)

    monkeypatch.setattr(reg_init.importlib, "import_module", fake_import_module)

    # Call _discover_modules with our fake modules
    result = reg_init._discover_modules([fake_operations, fake_core], "operation")

    expected_imports = {
        "pdftl.operations.mod_a",
        "pdftl.operations.mod_b",
        "pdftl.core.mod_a",
        "pdftl.core.mod_b",
    }
    assert set(imported) == expected_imports
    assert set(result) == expected_imports


def test_initialize_registry_calls_expected(monkeypatch):
    """initialize_registry should call _discover_modules once for each package."""
    called = {"discover": 0}

    def fake_discover(modules, label):
        called["discover"] += 1
        return ["dummy"]

    monkeypatch.setattr(reg_init, "_discover_modules", fake_discover)

    reg_init.initialize_registry()

    # It should be called twice: once for operations, once for options
    assert called["discover"] == 2
    assert getattr(reg_init.initialize_registry, "initialized") is True


def test_initialize_registry_idempotent(monkeypatch):
    """If already initialized, initialize_registry should not call _discover_modules again."""
    call_count = {"discover": 0}

    def fake_discover(modules, label):
        call_count["discover"] += 1
        return []

    monkeypatch.setattr(reg_init, "_discover_modules", fake_discover)

    reg_init.initialize_registry()
    reg_init.initialize_registry()  # second call should be skipped

    assert (
        call_count["discover"] == 2
    )  # once per type (operation/option) on first call, none on second
    assert getattr(reg_init.initialize_registry, "initialized") is True


def test_discover_modules_logs_debug(monkeypatch, caplog):
    """Ensure _discover_modules emits debug logs listing loaded modules."""

    fake_pkg = make_fake_module("pdftl.operations", is_pkg=True)
    monkeypatch.setattr(
        reg_init.pkgutil,
        "iter_modules",
        lambda path: [(None, "alpha", False), (None, "beta", False)],
    )
    monkeypatch.setattr(reg_init.importlib, "import_module", lambda name: types.ModuleType(name))

    caplog.set_level(logging.DEBUG, logger="pdftl.registry_init")
    loaded = reg_init._discover_modules([fake_pkg], "operation")

    assert any("Loaded" in msg for msg in caplog.messages)
    assert any("pdftl.operations.alpha" in msg for msg in caplog.messages)
    assert isinstance(loaded, list)
    assert set(loaded) == {"pdftl.operations.alpha", "pdftl.operations.beta"}


def test_discover_modules_skips_no_path(caplog):
    """
    Covers lines 33-34:
    logger.warning("Skipping discovery for %s (no __path__)", pkg.__name__)
    continue
    """
    # 1. Create a dummy module object (standard Python module type)
    # This behaves exactly like a real module: it has a __name__ but no __path__ (unless we add it).
    mock_module = types.ModuleType("mock_file_module")

    with caplog.at_level(logging.WARNING):
        loaded = _discover_modules([mock_module], "test_label")

    # 2. Assertions
    assert len(loaded) == 0
    # Now this will match exactly because types.ModuleType respects the name we gave it.
    assert "Skipping discovery for mock_file_module" in caplog.text


def test_registry_syntax_error():
    """
    Covers registry_init.py line 56:
    Checks that a SyntaxError during external module import is caught and logged,
    rather than crashing the application.
    """
    # We mock the Path object to simulate finding a file
    mock_path_obj = MagicMock()
    mock_plugin = MagicMock()
    mock_plugin.stem = "broken_plugin"

    # Setup the mock to look like a directory containing one python file
    mock_path_obj.exists.return_value = True
    mock_path_obj.glob.return_value = [mock_plugin]

    # Make division (the / operator) always return self.
    # This allows chains of any length: Path.home() / ".config" / "pdftl" / ...
    # to all resolve to this same configured mock_path_obj.
    mock_path_obj.__truediv__.return_value = mock_path_obj

    # Patch pathlib to return our mock path
    with patch("pdftl.registry_init.pathlib.Path") as mock_path_cls:
        # Handle both Windows (env var) and Linux (home dir) logic in the source
        mock_path_cls.return_value = mock_path_obj
        mock_path_cls.home.return_value.__truediv__.return_value.__truediv__.return_value = (
            mock_path_obj
        )

        # Patch import_module to raise SyntaxError when called
        with patch("pdftl.registry_init.importlib.import_module") as mock_import:
            mock_import.side_effect = SyntaxError(
                "Invalid Syntax", ("broken_plugin.py", 1, 1, "bad code")
            )

            # Patch sys.path so we don't actually mess up the test runner's path
            with patch.object(sys, "path", []):
                # Execute
                _discover_external_operations()

            # If we reached here without a crash, the try/except block worked.
            # We can verify import was attempted
            mock_import.assert_called()


def test_registry_no_config_dir():
    """
    Covers registry_init.py line 36:
    Checks that the function returns early if the operations directory does not exist.
    """
    mock_path_obj = MagicMock()
    # CRITICAL: This forces the early return at line 36
    mock_path_obj.exists.return_value = False

    # Ensure path chaining works so we reach the .exists() check without crashing
    mock_path_obj.__truediv__.return_value = mock_path_obj

    with patch("pdftl.registry_init.pathlib.Path") as mock_path_cls:
        mock_path_cls.return_value = mock_path_obj
        mock_path_cls.home.return_value = mock_path_obj

        # We verify the early return by checking that sys.path was NOT modified
        with patch("pdftl.registry_init.sys.path", []) as mock_sys_path:
            _discover_external_operations()

            assert len(mock_sys_path) == 0
