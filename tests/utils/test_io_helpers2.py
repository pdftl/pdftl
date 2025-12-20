import sys
from unittest.mock import patch

from pdftl.utils.io_helpers import can_read_file, smart_open_output

# --- Tests for smart_open_output ---


def test_smart_open_stdout_text():
    """Tests line 23: Opening stdout in text mode."""
    with smart_open_output(None, mode="w") as f:
        assert f is sys.stdout


def test_smart_open_stdout_binary():
    """Tests line 22: Opening stdout in binary mode."""
    with smart_open_output(None, mode="wb") as f:
        # sys.stdout.buffer is where binary data goes in Python 3
        assert f is sys.stdout.buffer


def test_smart_open_file_writing(tmp_path):
    """Tests lines 25-29: Opening a real file."""
    test_file = tmp_path / "test.txt"
    # Text mode
    with smart_open_output(str(test_file), mode="w") as f:
        f.write("hello")
    assert test_file.read_text() == "hello"

    # Binary mode
    bin_file = tmp_path / "test.bin"
    with smart_open_output(str(bin_file), mode="wb") as f:
        f.write(b"\x00\x01")
    assert bin_file.read_bytes() == b"\x00\x01"


# --- Tests for can_read_file ---


def test_can_read_file_success(tmp_path):
    """Tests line 42: Successful file check."""
    f = tmp_path / "readable.pdf"
    f.write_text("content")
    assert can_read_file(str(f)) is True


def test_can_read_file_not_found():
    """Tests line 38: File does not exist."""
    assert can_read_file("non_existent_file_999.pdf") is False


def test_can_read_file_permission_denied(tmp_path):
    """Tests lines 44-45: OSError handling."""
    f = tmp_path / "locked.pdf"
    f.write_text("secret")

    # We simulate an OSError (like PermissionError) on the open() call
    with patch("pathlib.Path.open", side_effect=OSError("Permission denied")):
        assert can_read_file(str(f)) is False


def test_can_read_file_is_directory(tmp_path):
    """Tests line 38: Path is a directory, not a file."""
    assert can_read_file(str(tmp_path)) is False
