import io
from unittest.mock import patch

import pytest

from pdftl.cli.help import print_help
from pdftl.core.registry import registry


@pytest.fixture
def mock_registry_with_tags():
    """Temporary add an option with full metadata to the registry."""

    class MockInfo(dict):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            meta = {
                "tags": ["test-tag", "security"],
                "long_desc": "Detailed long description.",
                "examples": [{"desc": "Test Example", "cmd": "test-cmd"}],
            }
            self.update(meta)
            # Set attributes for hasattr() checks in _print_output_options_help
            for k, v in meta.items():
                setattr(self, k, v)

    original_options = registry.options
    registry.options = {"mock_opt": MockInfo(desc="Mock Description")}
    yield
    registry.options = original_options


def test_print_help_tag_search(mock_registry_with_tags):
    """Tests lines 357-368: Searching help by tag."""
    output = io.StringIO()
    print_help("tag:test-tag", dest=output, raw=True)
    content = output.getvalue()
    assert "mock_opt" in content
    assert "test-tag" in content


def test_print_output_options_details(mock_registry_with_tags):
    """Tests lines 211-219: Details, examples, and tags in output options."""
    output = io.StringIO()
    print_help("output_options", dest=output, raw=True)
    content = output.getvalue()
    assert "Detailed long description" in content
    assert "test-tag" in content


def test_print_multiple_topics_separator():
    """Tests lines 393-397: Separator logic."""
    output = io.StringIO()
    mock_ops = {"op1": {"desc": "d1"}, "op2": {"desc": "d2"}}
    with patch("pdftl.core.registry.registry.operations", mock_ops):
        with (
            patch("pdftl.core.registry.registry.options", {}),
            patch("pdftl.core.registry.registry.help_topics", {}),
        ):
            print_help("all", dest=output, raw=True)
            assert "---" in output.getvalue()
