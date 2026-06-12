import pytest
from unittest.mock import MagicMock, patch

from pdftl.utils.pager import ThresholdPagerStream


def test_pager_under_threshold():
    """Test that text under the threshold prints normally to stdout on close."""
    stream = ThresholdPagerStream(threshold=5)
    assert stream.isatty() is True

    with patch("sys.stdout.write") as mock_stdout_write:
        stream.write("line 1\nline 2\n")

        # Pager shouldn't start yet
        assert stream.pager_proc is None
        assert stream.pager_failed is False

        stream.close()
        mock_stdout_write.assert_called_once_with("line 1\nline 2\n")


@patch("platform.system", return_value="Linux")
@patch("subprocess.Popen")
def test_pager_over_threshold_unix(mock_popen, mock_platform):
    """Test that exceeding the threshold successfully launches the pager."""
    mock_proc = MagicMock()
    mock_popen.return_value = mock_proc

    stream = ThresholdPagerStream(threshold=2)
    stream.write("line 1\nline 2\nline 3\n")

    # Pager should have started
    mock_popen.assert_called_once()
    assert stream.pager_proc is not None

    # Buffer should have been flushed to the pager and cleared
    mock_proc.stdin.write.assert_called_with("line 1\nline 2\nline 3\n")
    assert len(stream.buffer) == 0


@patch("platform.system", return_value="Windows")
def test_pager_windows_fallback(mock_platform):
    """Test that Windows skips the pager and streams direct to stdout."""
    stream = ThresholdPagerStream(threshold=2)

    with patch("sys.stdout.write") as mock_stdout_write:
        stream.write("line 1\nline 2\nline 3\n")

        assert stream.pager_proc is None
        assert stream.pager_failed is True
        mock_stdout_write.assert_called_once_with("line 1\nline 2\nline 3\n")


@patch("platform.system", return_value="Linux")
@patch("subprocess.Popen")
def test_pager_broken_pipe(mock_popen, mock_platform):
    """Test that a closed pager raises a BrokenPipeError to stop Rich."""
    mock_proc = MagicMock()
    mock_popen.return_value = mock_proc

    stream = ThresholdPagerStream(threshold=2)

    # 1. This hits threshold, launches Pager, and safely flushes the buffer
    stream.write("line 1\nline 2\n")

    # 2. NOW simulate less being closed by the user
    mock_proc.stdin.write.side_effect = OSError("Pipe closed")

    # 3. The next write directly into the pipe will fail and raise
    with pytest.raises(BrokenPipeError):
        stream.write("line 3\n")


@patch("platform.system", return_value="Linux")
@patch("subprocess.Popen", side_effect=OSError("No less found"))
def test_pager_start_failure(mock_popen, mock_platform):
    """Test fallback behavior if the subprocess completely fails to launch."""
    stream = ThresholdPagerStream(threshold=2)

    with patch("sys.stdout.write") as mock_stdout_write:
        stream.write("1\n2\n")

        # It tried to start, failed, and fell back
        assert stream.pager_failed is True
        mock_stdout_write.assert_called_once_with("1\n2\n")


def test_pager_flush_states():
    """Test that flush behaves correctly depending on the active state."""
    stream = ThresholdPagerStream(threshold=5)

    # Standard state (does nothing)
    stream.flush()

    # Fallback state
    stream.pager_failed = True
    with patch("sys.stdout.flush") as mock_stdout_flush:
        stream.flush()
        mock_stdout_flush.assert_called_once()


import sys
from unittest.mock import patch


def test_write_fallback_oserror(monkeypatch):
    """Covers lines 32-36: fallback to stdout fails."""
    stream = ThresholdPagerStream(threshold=2)
    stream.pager_failed = True

    # Mock sys.stdout.write to throw an OSError
    mock_stdout = MagicMock()
    mock_stdout.write.side_effect = OSError("Disk full or pipe closed")
    monkeypatch.setattr(sys, "stdout", mock_stdout)

    with pytest.raises(BrokenPipeError):
        stream.write("text")


def test_write_pager_proc_success():
    """Covers line 46: successful write directly to an active pager."""
    stream = ThresholdPagerStream(threshold=2)
    stream.pager_proc = MagicMock()

    # Should execute cleanly and hit the return statement on line 46
    stream.write("text")
    stream.pager_proc.stdin.write.assert_called_with("text")


@patch("platform.system", return_value="Linux")
@patch("subprocess.Popen")
def test_start_pager_injects_R_flag(mock_popen, mock_platform, monkeypatch):
    """Covers line 66: Injecting -R into LESS if it is missing."""
    # Force the environment to have LESS without the 'R' or 'r' flag
    monkeypatch.setenv("LESS", "FX")

    stream = ThresholdPagerStream(threshold=1)
    # Writing two lines hits the threshold and triggers _start_pager
    stream.write("line 1\nline 2\n")

    # Verify Popen was called with the modified LESS variable
    called_env = mock_popen.call_args.kwargs["env"]
    assert called_env["LESS"] == "FX -R"


def test_flush_pager_proc():
    """Covers lines 89-92: flushing the pager process with and without errors."""
    stream = ThresholdPagerStream(threshold=2)
    stream.pager_proc = MagicMock()

    # Normal flush
    stream.flush()
    stream.pager_proc.stdin.flush.assert_called_once()

    # Flush throwing OSError (swallowed by the pass statement)
    stream.pager_proc.stdin.flush.side_effect = OSError("Pipe broken")
    stream.flush()  # Should not raise an exception


def test_close_pager_proc():
    """Covers lines 96-100: closing the pager process and waiting."""
    stream = ThresholdPagerStream(threshold=2)
    stream.pager_proc = MagicMock()

    # Normal close
    stream.close()
    stream.pager_proc.stdin.close.assert_called_once()
    stream.pager_proc.wait.assert_called_once()

    # Close throwing OSError (swallowed by the pass statement)
    stream.pager_proc.stdin.close.side_effect = OSError("Pipe broken")
    stream.close()  # Should not raise an exception
    # Verify wait() is still called even if close() throws
    assert stream.pager_proc.wait.call_count == 2


def test_write_fallback_success(monkeypatch):
    """Covers line 36: fallback to stdout succeeds and naturally returns."""
    stream = ThresholdPagerStream(threshold=2)
    stream.pager_failed = True

    # Mock stdout so we can verify the write happened without actually printing
    mock_stdout = MagicMock()
    monkeypatch.setattr(sys, "stdout", mock_stdout)

    # Writing should hit line 33, skip the except block, and hit the return on line 36
    stream.write("fallback text")

    # Verify the write actually happened
    mock_stdout.write.assert_called_once_with("fallback text")
