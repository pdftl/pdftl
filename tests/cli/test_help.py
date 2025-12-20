import builtins
import importlib
import importlib.metadata
import io
import sys
import types
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import MagicMock, patch

import pytest

import pdftl.cli.help as helpmod


@pytest.fixture(autouse=True)
def patch_environment(monkeypatch, tmp_path):
    """Patch core globals so all help functions run cleanly."""
    monkeypatch.setattr(helpmod, "WHOAMI", "pdftl")
    monkeypatch.setattr(helpmod, "HOMEPAGE", "https://example.com")
    monkeypatch.setattr(helpmod, "PACKAGE", "pdftl")

    # Create fake operations and options
    fake_op = {
        "desc": "Combine PDFs",
        "usage": "combine a b out",
        "examples": [{"desc": "Example", "cmd": "combine"}],
        "long_desc": "Detailed description",
        "tags": ["merge"],
        "title": "combine",
    }
    fake_opt = {
        "desc": "Output file",
        "examples": [{"desc": "Save", "cmd": "output file.pdf"}],
        "long_desc": "More info",
    }
    fake_cli = {
        "options": {"output": fake_opt},
        "extra help topics": {
            "topic1": {
                "title": "topic1",
                "desc": "desc",
                "examples": [{"desc": "x", "cmd": "y"}],
            }
        },
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
    # monkeypatch.setattr(helpmod, "CLI_DATA", fake_cli)
    monkeypatch.setattr(helpmod, "SPECIAL_HELP_TOPICS_MAP", {("input", "in"): "help input"})
    monkeypatch.setattr(helpmod, "SYNOPSIS_TEMPLATE", "Usage: {whoami} [{special_help_topics}]")
    monkeypatch.setattr(
        helpmod,
        "VERSION_TEMPLATE",
        "{whoami} {package} {project_version}\n{dependencies}",
    )

    dummy_py = tmp_path / "dummy.py"
    dummy_py.write_text("")
    monkeypatch.setattr(helpmod, "__file__", str(dummy_py))


def test_get_synopsis():
    result = helpmod.get_synopsis()
    assert "pdftl" in result
    assert "i" in result  # from SPECIAL_HELP_TOPICS_MAP key tuple


def test_get_project_version_success(monkeypatch):
    monkeypatch.setattr(helpmod.importlib.metadata, "version", lambda pkg: "1.2.3")
    assert helpmod.get_project_version() == "1.2.3"


def test_get_project_version_fallback(monkeypatch):
    # Force metadata failure
    def fake_version(_):
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(importlib.metadata, "version", fake_version)

    # Insert a fake pdftl._version module into sys.modules
    fake_mod = types.SimpleNamespace(version="2.5.0-dev")
    monkeypatch.setitem(sys.modules, "pdftl._version", fake_mod)

    assert helpmod.get_project_version() == "2.5.0-dev"


def test_get_project_version_no_pyproject(monkeypatch):
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


def test_print_version_to_console(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "pikepdf",
        types.SimpleNamespace(__version__="10.0", __libqpdf_version__="11.0"),
    )
    monkeypatch.setattr(helpmod, "get_project_version", lambda: "1.0.0")
    buf_out, buf_err = io.StringIO(), io.StringIO()
    with patch("rich.console.Console") as MockConsole:
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            helpmod.print_version()
        MockConsole.return_value.print.assert_called_once()
    # Optionally check that the output buffer is empty (since print_version uses console)
    assert buf_out.getvalue() == ""
    assert buf_err.getvalue() == ""


def test_print_version_to_file(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "pikepdf",
        types.SimpleNamespace(__version__="10.0", __libqpdf_version__="11.0"),
    )
    monkeypatch.setattr(helpmod, "get_project_version", lambda: "1.0.0")
    buf = io.StringIO()
    helpmod.print_version(dest=buf)
    assert "1.0.0" in buf.getvalue()


@pytest.mark.parametrize("cmd", [None, "combine", "output_options", "examples", "all", "nonsense"])
def test_print_help_variants(monkeypatch, cmd):
    monkeypatch.setattr(helpmod, "get_project_version", lambda: "1.0.0")
    buf_out, buf_err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        helpmod.print_help(cmd, raw=True)
    output = buf_out.getvalue() + buf_err.getvalue()
    # Ensure output contains the version or some CLI content
    assert "pdftl" in output


def test_print_version_to_console(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "pikepdf",
        type("FakePikePDF", (), {"__version__": "10.0", "__libqpdf_version__": "11.0"})(),
    )
    monkeypatch.setattr(helpmod, "get_project_version", lambda: "1.0.0")

    with patch.object(helpmod, "get_console") as mock_get_console:

        # Run the command
        helpmod.print_version()

        # 3. Capture the mock console instance that get_console() returned
        mock_console_instance = mock_get_console.return_value

        # 4. Verify 'print' was called on that instance
        mock_console_instance.print.assert_called_once()

        # 5. Check the content
        #    We convert to str() in case rich passed a renderable object (like Text or Panel)
        args, _ = mock_console_instance.print.call_args
        printed_content = str(args[0])

        assert "pdftl 1.0.0" in printed_content
        assert "pikepdf 10.0" in printed_content


def test_print_version_to_file(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "pikepdf",
        type("FakePikePDF", (), {"__version__": "10.0", "__libqpdf_version__": "11.0"})(),
    )
    monkeypatch.setattr(helpmod, "get_project_version", lambda: "1.0.0")

    buf = io.StringIO()
    helpmod.print_version(dest=buf)
    output = buf.getvalue()

    assert "pdftl 1.0.0" in output
    assert "pikepdf 10.0" in output
    assert "libqpdf 11.0" in output


def test_find_special_topic_command():
    assert helpmod.find_special_topic_command("input") == "help input"
    assert helpmod.find_special_topic_command("unknown") is None


def test_find_operator_topic_command():
    assert helpmod.find_operator_topic_command(["combine", "merge"]) == "combine"


def test_find_option_topic_command():
    assert helpmod.find_option_topic_command(["output"]) == "output"
