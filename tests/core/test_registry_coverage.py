import pytest

from pdftl.core.registry import Registry, registry
from pdftl.core.types import Compatibility, FeatureType, HelpExample, Status


@pytest.fixture(autouse=True)
def clean_global_registry():
    """Clear the global registry between tests to prevent pollution."""
    registry.operations.clear()
    registry.options.clear()
    registry.help_topics.clear()
    yield


def test_registry_filter_attribute_access():
    """Tests lines 55-56: Registry.filter using object attribute access via LegacyDictAccess."""

    # Register a real operation which uses LegacyDictAccess
    @registry.register_operation("attr-op", tags=["search-me"])
    def mock_func():
        pass

    # This triggers the hasattr(item, "tags") check in Registry.filter
    result = registry.filter("operations", "tags", lambda x: "search-me" in x)
    assert "attr-op" in result


def test_register_operation_compatibility_dict():
    """Tests line 117: Automatic conversion of dict to Compatibility object."""
    # Now using the actual mandatory fields defined in types.py
    compat_data = {
        "type": FeatureType.PDFTL_EXTENSION,
        "status": Status.STABLE,
        "notes": "Verified fields",
    }

    @registry.register_operation("compat-op", compatibility=compat_data)
    def mock_func():
        pass

    op = registry.operations["compat-op"]
    assert isinstance(op.compatibility, Compatibility)
    assert op.compatibility.status == Status.STABLE


def test_register_help_topic_examples():
    """Tests lines 157 & 161: Help topic example validation."""

    @registry.register_help_topic(
        "topic1", "Title", "Desc", examples=[{"cmd": "pdftl", "desc": "d"}]
    )
    def func1():
        pass

    assert isinstance(registry.help_topics["topic1"].examples[0], HelpExample)

    with pytest.raises(ValueError, match="Invalid example format in topic 'topic2'"):

        @registry.register_help_topic("topic2", "Title", "Desc", examples=[123])
        def func2():
            pass


def test_to_help_example_helper_branches():
    """Tests lines 185-188: _to_help_example_helper logic."""
    existing_ex = HelpExample(cmd="cmd", desc="desc")

    @registry.register_operation("op-existing", examples=[existing_ex])
    def func3():
        pass

    assert registry.operations["op-existing"].examples[0] is existing_ex

    with pytest.raises(ValueError, match="Invalid example format in operation 'op-bad'"):

        @registry.register_operation("op-bad", examples=[None])
        def func4():
            pass


def test_registry_getitem_error():
    """Tests line 34: Unknown registry key error."""
    reg = Registry()
    with pytest.raises(KeyError, match="Unknown registry key"):
        _ = reg["invalid_key"]
