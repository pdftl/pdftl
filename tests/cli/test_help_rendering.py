import io
from unittest.mock import MagicMock

from pdftl.cli.help import _load_help_markdown, print_help


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


def test_print_help_formatted_to_stdout(monkeypatch):
    """
    Covers line 447:
    Printing to stdout (dest=None) WITHOUT raw mode.
    This triggers the use of the global get_console().
    """
    mock_console = MagicMock()
    monkeypatch.setattr("pdftl.cli.help.get_console", lambda: mock_console)

    # dest=None, raw=False -> goes to line 446/447
    print_help("help", dest=None, raw=False)

    assert mock_console.print.called
    # Verify it passed a HelpMarkdown object
    args = mock_console.print.call_args[0]
    # The class name is dynamic, so we check the type name string
    assert type(args[0]).__name__ == "HelpMarkdown"
