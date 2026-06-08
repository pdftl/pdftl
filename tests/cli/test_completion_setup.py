import io
import os
import sys
from unittest.mock import MagicMock, mock_open, patch

import pytest

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


def test_cover_help_setup():
    from pdftl.cli.completion_setup import shell_completion_help_topic

    shell_completion_help_topic()


# Adjust this import to match your actual structure if needed
from pdftl.cli.completion_setup import _infer_active_shell

# --- Tests for _infer_active_shell ---


def test_infer_shell_from_env():
    """It should prioritize the SHELL environment variable."""
    with patch.dict(os.environ, {"SHELL": "/usr/bin/zsh"}):
        assert _infer_active_shell() == "zsh"


# --- Tests for completion_setup ---


@patch("pdftl.cli.completion_setup.ensure_dependencies")
@patch("pdftl.cli.completion_setup._get_completion_scripts")
def test_completion_setup_explicit_success(mock_get_scripts, mock_ensure, capsys):
    """It should output the script for the requested shell and return 0."""
    mock_get_scripts.return_value = {"bash": "echo 'bash completion loaded'"}

    result = completion_setup(shell="bash")

    assert result == 0
    mock_ensure.assert_called_once()
    captured = capsys.readouterr()
    assert "echo 'bash completion loaded'" in captured.out


@patch("pdftl.cli.completion_setup.ensure_dependencies")
@patch("pdftl.cli.completion_setup._get_completion_scripts")
def test_completion_setup_unsupported_shell(mock_get_scripts, mock_ensure):
    """It should raise NotImplementedError if the shell isn't supported."""
    mock_get_scripts.return_value = {"bash": "..."}

    with pytest.raises(NotImplementedError, match="Shell completion for 'fish' is not available"):
        completion_setup(shell="fish")


@patch("pdftl.cli.completion_setup.ensure_dependencies")
@patch("pdftl.cli.completion_setup._infer_active_shell", return_value=None)
def test_completion_setup_inference_fails(mock_infer, mock_ensure, capsys):
    """It should print an error to stderr and return 1 if shell cannot be determined."""
    result = completion_setup(shell=None)

    assert result == 1
    captured = capsys.readouterr()
    assert "Could not automatically detect your shell" in captured.err


def test_infer_shell_proc_comm_bash():
    with (
        patch.dict(os.environ, clear=True),
        patch("os.path.exists", return_value=True),
        patch("builtins.open", mock_open(read_data="bash\n")),
    ):
        assert _infer_active_shell() == "bash"


def test_infer_shell_proc_comm_zsh():
    with (
        patch.dict(os.environ, clear=True),
        patch("os.path.exists", return_value=True),
        patch("builtins.open", mock_open(read_data="zsh\n")),
    ):
        assert _infer_active_shell() == "zsh"


def test_infer_shell_no_proc_linux_returns_none():
    with (
        patch.dict(os.environ, clear=True),
        patch("os.path.exists", return_value=False),
        patch("sys.platform", "linux"),
    ):
        assert _infer_active_shell() is None


def test_infer_shell_windows_fallback():
    with (
        patch.dict(os.environ, clear=True),
        patch("os.path.exists", return_value=False),
        patch("sys.platform", "win32"),
    ):
        assert _infer_active_shell() == "powershell"


def test_infer_shell_macos_libproc_found():
    mock_ctypes = MagicMock()
    mock_ctypes.util.find_library.return_value = "libproc.dylib"
    buf = mock_ctypes.create_string_buffer.return_value
    buf.value = b"zsh"

    with (
        patch.dict(os.environ, clear=True),
        patch("os.path.exists", return_value=False),
        patch("sys.platform", "darwin"),
        patch.dict("sys.modules", {"ctypes": mock_ctypes, "ctypes.util": mock_ctypes.util}),
    ):
        assert _infer_active_shell() == "zsh"


def test_infer_shell_macos_libproc_not_found():
    mock_ctypes = MagicMock()
    mock_ctypes.util.find_library.return_value = None

    with (
        patch.dict(os.environ, clear=True),
        patch("os.path.exists", return_value=False),
        patch("sys.platform", "darwin"),
        patch.dict("sys.modules", {"ctypes": mock_ctypes, "ctypes.util": mock_ctypes.util}),
    ):
        assert _infer_active_shell() is None


def test_infer_shell_oserror_falls_through_to_none():
    with (
        patch.dict(os.environ, clear=True),
        patch("os.getppid", side_effect=OSError("no parent")),
        patch("sys.platform", "linux"),
    ):
        assert _infer_active_shell() is None


def test_infer_shell_oserror_on_open():
    with (
        patch.dict(os.environ, clear=True),
        patch("os.path.exists", return_value=True),
        patch("builtins.open", side_effect=OSError("permission denied")),
        patch("sys.platform", "linux"),
    ):
        assert _infer_active_shell() is None
