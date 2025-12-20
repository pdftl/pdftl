import logging
import types

import pytest

import pdftl.registry_init as reg_init


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
    fake_commands = make_fake_module("pdftl.commands", is_pkg=True, submodules=["mod_a", "mod_b"])
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
    result = reg_init._discover_modules([fake_commands, fake_core], "operation")

    expected_imports = {
        "pdftl.commands.mod_a",
        "pdftl.commands.mod_b",
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

    fake_pkg = make_fake_module("pdftl.commands", is_pkg=True)
    monkeypatch.setattr(
        reg_init.pkgutil,
        "iter_modules",
        lambda path: [(None, "alpha", False), (None, "beta", False)],
    )
    monkeypatch.setattr(reg_init.importlib, "import_module", lambda name: types.ModuleType(name))

    caplog.set_level(logging.DEBUG, logger="pdftl.registry_init")
    loaded = reg_init._discover_modules([fake_pkg], "operation")

    assert any("Loaded" in msg for msg in caplog.messages)
    assert any("pdftl.commands.alpha" in msg for msg in caplog.messages)
    assert isinstance(loaded, list)
    assert set(loaded) == {"pdftl.commands.alpha", "pdftl.commands.beta"}
