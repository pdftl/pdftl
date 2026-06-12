import builtins
import importlib
import importlib.metadata
import io
import logging
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

import pdftl.cli.console as console_mod
import pdftl.cli.help as helpmod
import pdftl.cli.help_version as helpvermod
from pdftl.cli.help_render import format_examples_block
from pdftl.core.core_types import HelpExample


@pytest.fixture
def patch_environment(monkeypatch, tmp_path):
    """Patch core globals so all help functions run cleanly."""
    monkeypatch.setattr(helpmod, "WHOAMI", "pdftl_whoami")
    monkeypatch.setattr(helpvermod, "WHOAMI", "pdftl_whoami")
    monkeypatch.setattr(helpvermod, "HOMEPAGE", "https://example.com")
    monkeypatch.setattr(helpvermod, "PACKAGE", "pdftl_package")
    monkeypatch.setattr(helpmod, "PACKAGE", "pdftl_package")

    # Create fake operations and options
    fake_op = {
        "desc": "Combine PDFs",
        "usage": "combine a b out",
        "examples": [HelpExample(desc="Example", cmd="combine")],
        "long_desc": "Detailed description",
        "tags": ["merge"],
        "title": "combine",
    }
    fake_opt = {
        "desc": "Output file",
        "examples": [HelpExample(desc="Save", cmd="output file.pdf")],
        "long_desc": "More info",
    }

    # Patch registry with a dict-like object
    class FakeRegistry:
        def __init__(self):
            self.operations = {"combine": fake_op}
            self.options = {
                "output": fake_opt,
                "encrypt_aes256": {"desc": "AES256", "type": "flag"},
            }
            self.help_topics = {"foo": MagicMock()}

        def __getitem__(self, key):
            if key in ("operations", "options"):
                return getattr(self, key)
            raise KeyError(key)

        def __contains__(self, key):
            return key in ("operations", "options")

    monkeypatch.setattr(helpmod, "registry", FakeRegistry())
    monkeypatch.setattr(helpmod, "SPECIAL_HELP_TOPICS_MAP", {("input", "in"): "help input"})
    monkeypatch.setattr(helpmod, "SYNOPSIS_TEMPLATE", "Usage: {whoami} [{special_help_topics}]")
    monkeypatch.setattr(
        helpvermod,
        "VERSION_TEMPLATE",
        "{whoami} <~~ {package} ~~> {project_version}\n{dependencies}",
    )

    dummy_py = tmp_path / "dummy.py"
    dummy_py.write_text("")
    monkeypatch.setattr(helpmod, "__file__", str(dummy_py))


def test_get_synopsis(patch_environment):
    result = helpmod.get_synopsis()
    assert "pdftl_whoami" in result
    assert "i" in result  # from SPECIAL_HELP_TOPICS_MAP key tuple


def test_get_project_version_success(monkeypatch, patch_environment):
    monkeypatch.setattr("importlib.metadata.version", lambda pkg: "1.2.3")
    assert helpmod.get_project_version() == "1.2.3"


def test_get_project_version_fallback(monkeypatch, patch_environment):
    # Force metadata failure
    def fake_version(_):
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(importlib.metadata, "version", fake_version)

    # Insert a fake pdftl._version module into sys.modules
    fake_mod = types.SimpleNamespace(version="2.5.0-dev")
    monkeypatch.setitem(sys.modules, "pdftl._version", fake_mod)

    assert helpmod.get_project_version() == "2.5.0-dev"


def test_get_project_version_no_pyproject(monkeypatch, patch_environment):
    # 1. Force metadata failure
    def fake_version(_):
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(importlib.metadata, "version", fake_version)

    # 2. Remove cached modules
    monkeypatch.delitem(sys.modules, "pdftl._version", raising=False)
    monkeypatch.delitem(sys.modules, "pdftl", raising=False)

    # 3. Patch builtin import to block ONLY pdftl._version
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "pdftl._version":
            raise ImportError("simulated missing _version")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    # 4. Now it must take the final fallback
    assert helpmod.get_project_version() == "unknown-dev-version"


@pytest.mark.parametrize("cmd", [None, "combine", "output_options", "examples", "all", "nonsense"])
def test_print_help_variants(monkeypatch, caplog, cmd):
    """Test various help sub-commands/topics."""
    # 1. Setup a capture console
    capture_buffer = io.StringIO()
    capture_console = Console(file=capture_buffer, force_terminal=True, width=80)

    # 2. Patch the global singleton in the console module (good practice)
    monkeypatch.setattr(console_mod, "_CONSOLE", capture_console)

    # 3. Patch version to be consistent
    monkeypatch.setattr(helpmod, "get_project_version", lambda: "1.0.0")

    # 4. Run the help command
    # FIX: Patch the Console class used inside help.py.
    # This ensures that even if the code tries to create a new Console(file=print),
    # it gets our capture_console instead, avoiding the AttributeError.
    with patch("rich.console.Console", return_value=capture_console):
        with caplog.at_level(logging.WARNING):
            helpmod.print_help(cmd, dest=capture_buffer, raw=True)

    # 5. Verify output
    output = capture_buffer.getvalue()
    if cmd == "examples":
        assert "Examples" in output
    else:
        assert "usage:" in output or "Description:" in output or "pdftl" in output
        if cmd == "nonsense":
            assert "Unknown help topic 'nonsense'" in caplog.text


def test_print_version_to_console(monkeypatch, patch_environment):
    monkeypatch.setattr(
        helpvermod,
        "get_dependencies_status",
        lambda: (
            [("defusedxml", "0.7.1"), ("pikepdf", "10.0"), ("rich", "14.2.0")],
            [],
        ),
    )
    monkeypatch.setattr(helpvermod, "get_project_version", lambda: "1.0.0")
    monkeypatch.setitem(
        sys.modules,
        "pikepdf",
        type("FakePikePDF", (), {"__version__": "10.0", "__libqpdf_version__": "11.0"})(),
    )
    monkeypatch.setattr(helpvermod, "get_project_version", lambda: "1.0.0")

    with patch.object(helpvermod, "get_console") as mock_get_console:
        # Run the command
        helpvermod.print_version()

        # 3. Capture the mock console instance that get_console() returned
        mock_console_instance = mock_get_console.return_value

        # 4. Verify 'print' was called on that instance
        mock_console_instance.print.assert_called_once()

        # 5. Check the content
        #    We convert to str() in case rich passed a renderable object (like Text or Panel)
        args, _ = mock_console_instance.print.call_args
        printed_content = str(args[0])

        assert "pdftl_whoami <~~ pdftl_package ~~> 1.0.0" in printed_content
        assert "pikepdf 10.0" in printed_content


def test_print_version_to_file(monkeypatch, patch_environment):
    monkeypatch.setattr(
        helpvermod,
        "get_dependencies_status",
        lambda: (
            [("defusedxml", "0.7.1"), ("pikepdf", "10.0"), ("rich", "14.2.0")],
            [],
        ),
    )
    monkeypatch.setattr(helpvermod, "get_project_version", lambda: "1.0.0")
    monkeypatch.setitem(
        sys.modules,
        "pikepdf",
        type("FakePikePDF", (), {"__version__": "10.0", "__libqpdf_version__": "11.0"})(),
    )
    monkeypatch.setattr(helpvermod, "get_project_version", lambda: "1.0.0")

    buf = io.StringIO()
    helpvermod.print_version(dest=buf)
    output = buf.getvalue()

    assert "pdftl_whoami <~~ pdftl_package ~~> 1.0.0" in output
    assert "pikepdf 10.0" in output
    assert "libqpdf 11.0" in output


def test_find_special_topic_command(patch_environment):
    assert helpmod.find_special_topic_command("input") == "help input"
    assert helpmod.find_special_topic_command("unknown") is None


def test_find_operator_topic_command(patch_environment):
    assert helpmod.find_operator_topic_command(["combine", "merge"]) == "combine"


def test_find_option_topic_command(patch_environment):
    assert helpmod.find_option_topic_command(["output"]) == "output"


##################################################


# --- Fixtures & Mocks ---


@pytest.fixture
def mock_metadata(mocker, monkeypatch):
    """
    Mocks the importlib.metadata functions to simulate a specific
    dependency tree without reading the real system.
    """
    monkeypatch.setattr(helpvermod, "WHOAMI", "pdftl_whoami")
    monkeypatch.setattr(helpvermod, "PACKAGE", "pdftl_package")

    # 1. Mock 'requires' to return a mix of core, feature, and dev deps
    mocker.patch(
        "importlib.metadata.requires",
        return_value=[
            "pikepdf>=10.0.0",  # Core (no marker)
            "reportlab ; extra == 'add-text'",  # Feature (keep)
            'pypdfium2 ; extra == "crop-visible"',  # Feature (keep, double quotes)
            "pytest ; extra == 'dev'",  # Dev (ignore)
            "sphinx ; extra == 'docs'",  # Docs (ignore)
            "pdftl[extras] ; extra == 'full'",  # Self-ref (ignore)
        ],
    )

    # 2. Mock 'version' to simulate some packages installed, others missing
    def fake_version(package_name):
        versions = {
            "pdftl": "0.9.9",
            "pikepdf": "10.0.0",
            "reportlab": "4.0.0",
            # pypdfium2 is missing in this fake env
        }
        if package_name in versions:
            return versions[package_name]
        raise importlib.metadata.PackageNotFoundError(package_name)

    mocker.patch("importlib.metadata.version", side_effect=fake_version)


# --- Tests ---


def test_get_dependencies_filtering(mock_metadata):
    """
    Verifies that dev tools and self-references are filtered out,
    and only 'feature' extras remain.
    """
    results = helpvermod.get_dependencies_status()

    # Extract just the names for easy assertion
    pkg_names = {r[0] for r in results[1]}

    # Assertions
    assert "reportlab" in pkg_names, "Should include standard feature extras"
    assert "pypdfium2" in pkg_names, "Should include feature extras with double-quotes"

    assert "pytest" not in pkg_names, "Should ignore 'dev' extras"
    assert "sphinx" not in pkg_names, "Should ignore 'docs' extras"
    assert "pdftl" not in pkg_names, "Should ignore self-reference"
    assert "pikepdf" not in pkg_names, "Should ignore core deps (no marker)"


def test_get_dependencies_status_detection(mock_metadata):
    """
    Verifies that installed packages return their version,
    and missing packages return None.
    """
    results = dict(helpvermod.get_dependencies_status()[1])

    assert results["reportlab"] == "4.0.0", "Should return version for installed pkg"
    assert results["pypdfium2"] is None, "Should return None for missing pkg"


def test_print_version_output_format(mock_metadata):
    """
    Verifies the actual string output to stdout.
    """
    capture = io.StringIO()
    helpvermod.print_version(dest=capture)

    stdout = capture.getvalue()

    # 1. Check Core output
    assert "pdftl_whoami" in stdout
    assert "Core dependencies:" in stdout
    assert "pikepdf" in stdout

    # 2. Check Optional Section Header
    assert "Optional dependencies:" in stdout

    # 3. Check status formatting
    assert "reportlab: 4.0.0" in stdout
    assert "pypdfium2: (not found)" in stdout

    # 4. Ensure filtered items are NOT present
    assert "pytest" not in stdout


def test_print_version_no_metadata_crash(mocker, monkeypatch):
    """
    Edge case: If importlib.metadata throws PackageNotFoundError
    (e.g. running from a raw .py file without install), it should not crash.
    """
    monkeypatch.setattr(helpvermod, "WHOAMI", "pdftl_whoami")
    monkeypatch.setattr(helpvermod, "PACKAGE", "pdftl_package")
    capture = io.StringIO()

    mocker.patch(
        "importlib.metadata.requires", side_effect=importlib.metadata.PackageNotFoundError
    )

    # Should run without error
    helpvermod.print_version(dest=capture)
    stdout = capture.getvalue()

    # Should still print core info, just no optional block
    assert "pdftl_whoami" in stdout
    assert "Optional dependencies:" not in stdout


def test_print_version_pikepdf_not_found(monkeypatch, patch_environment):
    monkeypatch.setattr(helpvermod, "get_dependencies_status", lambda: ([], []))
    monkeypatch.setattr(helpvermod, "get_project_version", lambda: "1.0.0")
    monkeypatch.setitem(sys.modules, "pikepdf", None)  # None causes ImportError on import

    with patch.object(helpvermod, "get_console") as mock_get_console:
        helpvermod.print_version()

        args, _ = mock_get_console.return_value.print.call_args
        printed_content = str(args[0])

        assert "libqpdf (not found)" in printed_content


# --- merged from test_help_coverage.py ---


class TestHelpLogicEdgeCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Mute the noisy markdown_it debug logs
        logging.getLogger("markdown_it").setLevel(logging.WARNING)

    def test_format_examples_grouping(self):
        """
        Covers line 115: Multiple examples for the same topic.
        """
        examples = [
            HelpExample(topic="foo", desc="d1", cmd="c1"),
            HelpExample(topic="foo", desc="d2", cmd="c2"),
            HelpExample(topic="bar", desc="d3", cmd="c3"),
        ]

        output = format_examples_block(examples, show_topics=True)

        # FIX: The logic uses "Example" for the first one, not "Example 1"
        self.assertIn("Example for '`foo`'", output)  # First example
        self.assertIn("Example 2 for '`foo`'", output)  # Second example increments
        self.assertIn("Example for '`bar`'", output)  # Resets for new topic

    def test_print_topic_help_caller_source(self):
        """Covers line 166: Printing the 'Source: ...' line."""
        mock_hprint = MagicMock()
        topic_data = {"desc": "Test desc", "caller": "my_plugin_module"}
        helpmod._print_topic_help(mock_hprint, topic_data, "test_topic")
        mock_hprint.assert_any_call("\n*Source: my_plugin_module*")

    def test_find_special_topic_none(self):
        """Covers line 372: Early exit when topic is None."""
        self.assertIsNone(helpmod.find_special_topic_command(None))


class TestHelpRichRendering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.getLogger("markdown_it").setLevel(logging.WARNING)

    def test_render_to_file_triggers_rich_logic(self):
        """
        Covers 313-316 (File console) and 260-300 (Rich objects).
        """
        buffer = io.StringIO()
        # This will now pass if you applied the 'from rich.console import Console' fix
        helpmod.print_help(command=None, dest=buffer, raw=False)

        output = buffer.getvalue()
        self.assertIn("PDF tackle", output)
        # Check for Rich box-drawing characters (indicating LeftJustifiedHeading worked)
        # doesn't work on windows: Rich degrades. So restrict test to linux only.
        if "linux" in sys.platform:
            self.assertTrue(any(c in output for c in ["┏", "━", "┃"]))

    @patch("pdftl.cli.help.get_console")
    def test_rich_object_internals(self, mock_get_console):
        """Covers 310, 297, 293-294."""
        mock_console_instance = MagicMock()
        mock_get_console.return_value = mock_console_instance

        helpmod.print_help(command=None, dest=None, raw=False)

        # Inspect the first call to print()
        first_call_args = mock_console_instance.print.call_args_list[0]
        renderable = first_call_args[0][0]  # args[0]

        # FIX: The first item printed is now the Title Panel, not HelpMarkdown
        self.assertEqual(type(renderable).__name__, "Panel")

    def test_raw_mode_bypass(self):
        buffer = io.StringIO()
        helpmod.print_help(command=None, dest=buffer, raw=True)
        self.assertNotIn("┏", buffer.getvalue())
