import io
from unittest.mock import patch

import pytest
from rich.console import Console

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
    # Create a console that writes to our specific output buffer
    mock_console = Console(file=output, force_terminal=True)

    mock_ops = {"op1": {"desc": "d1"}, "op2": {"desc": "d2"}}
    with patch("pdftl.core.registry.registry.operations", mock_ops):
        with (
            patch("pdftl.core.registry.registry.options", {}),
            patch("pdftl.core.registry.registry.help_topics", {}),
            # FIX: Patch Console to return our safe mock_console
            patch("rich.console.Console", return_value=mock_console),
        ):
            print_help("all", dest=output, raw=True)

    # (Optional) Verify something was written
    assert output.getvalue() != ""


from unittest.mock import patch

import pytest

from pdftl.cli.help import print_main_help


def test_print_main_help_no_console():
    """Hits help.py:115 by simulating a missing Rich console."""
    with patch("pdftl.cli.help.get_console", return_value=None):
        # We must set raw=False to enter the Rich path
        with pytest.raises(RuntimeError, match="Rich console is not available"):
            print_main_help(raw=False)
