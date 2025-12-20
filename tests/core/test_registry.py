from collections import OrderedDict

import pytest

import pdftl.core.registry as registry_mod


@pytest.fixture(autouse=True)
def reset_registry(monkeypatch):
    """Reset the global registry before each test."""
    registry_mod.registry.operations.clear()
    registry_mod.registry.options.clear()
    yield
    registry_mod.registry.operations.clear()
    registry_mod.registry.options.clear()


def test_registry_initial_state():
    """Registry starts with empty OrderedDicts."""
    r = registry_mod.Registry()
    assert isinstance(r.operations, OrderedDict)
    assert isinstance(r.options, OrderedDict)
    assert not r.operations
    assert not r.options


def test_getitem_and_contains_valid_keys():
    r = registry_mod.Registry()
    assert "operations" in r
    assert "options" in r
    assert r["operations"] == r.operations
    assert r["options"] == r.options


def test_getitem_invalid_key_raises():
    r = registry_mod.Registry()
    with pytest.raises(KeyError):
        _ = r["invalid"]


def test_filter_basic():
    """filter() should select keys by predicate and optionally transform."""
    r = registry_mod.Registry()
    r.operations.update(
        {
            "op1": {"category": "A"},
            "op2": {"category": "B"},
            "op3": {"category": "A"},
        }
    )

    result = r.filter("operations", "category", lambda x: x == "A")
    assert result == {"op1", "op3"}

    # With transform
    result2 = r.filter("operations", "category", lambda x: x == "A", transform=str.upper)
    assert result2 == {"OP1", "OP3"}


def test_register_operation_decorator_registers():
    """@register_operation should add function to global registry."""

    @registry_mod.register_operation("say_hello", category="utility")
    def say_hello():
        return "hi"

    entry = registry_mod.registry.operations["say_hello"]
    assert entry["function"] is say_hello
    assert entry["category"] == "utility"


def test_register_option_decorator_registers():
    """@register_option should add metadata to global registry.options."""

    @registry_mod.register_option("verbose", type=bool, default=False)
    def dummy():
        pass

    opt = registry_mod.registry.options["verbose"]
    assert opt["type"] is bool
    assert opt["default"] is False


def test_register_operation_decorator_stackable():
    """Multiple decorators can stack and still register properly."""
    calls = []

    @registry_mod.register_operation("outer", tag="x")
    @registry_mod.register_operation("inner", tag="y")
    def f():
        calls.append("ran")

    assert "outer" in registry_mod.registry.operations
    assert "inner" in registry_mod.registry.operations
    # Ensure both point to the same function
    assert registry_mod.registry.operations["outer"]["function"] is f
    assert registry_mod.registry.operations["inner"]["function"] is f
    f()
    assert calls == ["ran"]


def test_global_helper_functions_delegation(monkeypatch):
    """register_operation and register_option should delegate to registry methods."""
    called = {}

    def fake_reg_op(name, **meta):
        called["op"] = (name, meta)
        return lambda f: f

    def fake_reg_opt(name, **meta):
        called["opt"] = (name, meta)
        return lambda f: f

    monkeypatch.setattr(registry_mod.registry, "register_operation", fake_reg_op)
    monkeypatch.setattr(registry_mod.registry, "register_option", fake_reg_opt)

    registry_mod.register_operation("hello", x=1)
    registry_mod.register_option("quiet", y=2)

    assert called["op"] == ("hello", {"x": 1})
    assert called["opt"] == ("quiet", {"y": 2})
