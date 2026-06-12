import io
import logging
from unittest.mock import MagicMock, patch

import pytest

from pdftl.cli.help import print_help
from pdftl.cli.help_render import _load_help_markdown, format_examples_block, load_hprint


def test_help_markdown_internals():
    """
    Covers HelpMarkdown internals:
    - __init__ and __str__ (lines 432-436)
    - LeftJustifiedHeading rendering (lines 410-423)
    """
    # Load the inner class
    HelpMarkdown = _load_help_markdown()
    source_md = "# Title\n\n## Subtitle\nText"
    md = HelpMarkdown(source_md)

    # Cover __str__
    assert str(md) == source_md

    # Cover LeftJustifiedHeading.__rich_console__
    # We must render it via a Console to trigger the __rich_console__ method
    from rich.console import Console

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, width=80)

    console.print(md)
    output = buf.getvalue()

    # Verify H1 logic (it puts it in a Panel, so look for borders or the text)
    assert "Title" in output
    # Verify H2 logic (it adds a newline prefix)
    assert "Subtitle" in output


def test_print_help_formatted_to_file():
    """
    Covers lines 449-455:
    Printing to a file object (not stdout) WITHOUT raw mode.
    This triggers the creation of a specific file_console.
    """
    buf = io.StringIO()
    # dest is a file, raw is False -> goes to line 449
    print_help("help", dest=buf, raw=False)

    output = buf.getvalue()
    # Should contain the help text
    assert "pdftl" in output
    # Should NOT be raw markdown (checking for rendered characteristic is hard reliably without exact matching,
    # but ensuring it ran without error and produced output covers the execution path).


def test_print_help_formatted_to_stdout_piped(monkeypatch, mock_notty):
    """
    Covers the piped/redirected path.
    Because mock_notty is injected, this ALWAYS evaluates isatty() as False
    and skips the page_captured_output block entirely.
    """
    mock_console = MagicMock()
    # Height is not strictly required here since paging is bypassed,
    # but mocking the console is still needed for the raw rendering.
    monkeypatch.setattr("pdftl.cli.help_render.get_console", lambda: mock_console)

    # dest=None, raw=False -> piped rendering
    print_help("help", dest=None, raw=False)
    assert mock_console.print.called
    # Verify it passed a HelpMarkdown object
    args = mock_console.print.call_args[0]
    # The class name is dynamic, so we check the type name string
    assert type(args[0]).__name__ == "HelpMarkdown"


def test_print_help_formatted_to_stdout_interactive(monkeypatch, mock_tty):
    """
    Covers line 447:
    Printing to stdout (dest=None) WITHOUT raw mode.
    This triggers the use of the global get_console().
    """
    mock_console = MagicMock()
    mock_console.height = 24
    monkeypatch.setattr("pdftl.cli.help_render.get_console", lambda: mock_console)

    # dest=None, raw=False -> goes to line 446/447
    print_help("help", dest=None, raw=False)
    assert mock_console.print.called
    # Verify it passed a HelpMarkdown object
    args = mock_console.print.call_args[0]
    # The class name is dynamic, so we check the type name string
    assert type(args[0]).__name__ == "HelpMarkdown"


def test_skip_invalid_example(caplog):
    caplog.set_level(logging.WARNING)
    format_examples_block([{}])
    assert "Skipping incomplete example" in caplog.text


def test_hprint_no_console():
    import sys

    import pytest

    from pdftl.cli.help_render import load_hprint

    # Hit line 66: Standard hprint failure
    hprint_std = load_hprint(None, raw=False)
    with patch("pdftl.cli.help_render.get_console", return_value=None):
        with pytest.raises(RuntimeError, match="Rich console is not available"):
            hprint_std("some markdown")

    # Hit line 74: Redirection (file) hprint failure
    hprint_file = load_hprint(sys.stderr, raw=False)
    with patch("pdftl.cli.help_render.get_console", return_value=None):
        with pytest.raises(RuntimeError, match="Rich console is not available"):
            hprint_file("some markdown")


def test_load_hprint_file_no_console():
    """Hits help_render.py:74 by rendering to a file without a console."""
    fake_file = io.StringIO()
    # To hit line 74, we need:
    # 1. raw = False
    # 2. dest is NOT sys.stdout/stderr (so we use a StringIO)
    # 3. get_console returns None
    hprint = load_hprint(dest=fake_file, raw=False)

    with patch("pdftl.cli.help_render.get_console", return_value=None):
        with pytest.raises(RuntimeError, match="Rich console is not available"):
            hprint("some markdown")


from pdftl.cli.help_render import page_captured_output


def test_page_captured_output_success(monkeypatch):
    """Tests that the stream is successfully hijacked and restored."""
    mock_console = MagicMock()
    mock_console.height = 24
    original_file = MagicMock()
    mock_console.file = original_file
    monkeypatch.setattr("pdftl.cli.help_render.get_console", lambda: mock_console)

    with page_captured_output() as console:
        assert console is mock_console
        # Verify the file target was temporarily swapped to our PagerStream
        assert type(console.file).__name__ == "ThresholdPagerStream"

    # Verify the original file target was safely restored after yielding
    assert console.file is original_file


def test_page_captured_output_broken_pipe(monkeypatch):
    """Tests that BrokenPipeError is silently swallowed to abort rendering."""
    mock_console = MagicMock()
    mock_console.height = 24
    monkeypatch.setattr("pdftl.cli.help_render.get_console", lambda: mock_console)

    with page_captured_output():
        raise BrokenPipeError("User quit the pager")
    # If the test passes without raising, the error was successfully swallowed


def test_page_captured_output_no_console(monkeypatch):
    """Tests safety check when rich console is entirely unavailable."""
    monkeypatch.setattr("pdftl.cli.help_render.get_console", lambda: None)

    with pytest.raises(RuntimeError, match="Rich console is not available"):
        with page_captured_output():
            pass
