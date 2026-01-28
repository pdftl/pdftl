import io
import sys
from unittest.mock import patch

from pdftl.cli.completion_setup import completion_setup


def test_completion_setup_output(monkeypatch):
    # We check if it generates the bash script with the correct paths
    with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        status = completion_setup("bash")
        output = mock_stdout.getvalue()

        assert status == 0
        assert "_pdftl_completions()" in output
        assert "complete -F _pdftl_completions pdftl" in output
        # Ensure it baked in the python path
        assert sys.executable in output


def test_completion_setup_unsupported_shell():
    import pytest

    with pytest.raises(NotImplementedError):
        completion_setup("madashell")


def test_cover_help_setup():
    from pdftl.cli.completion_setup import shell_completion_help_topic

    shell_completion_help_topic()
