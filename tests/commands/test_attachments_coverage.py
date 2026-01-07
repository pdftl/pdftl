import logging
from unittest.mock import patch

import pikepdf
import pytest

from pdftl.operations.attachments import (
    dump_files,
    dump_files_cli_hook,
    unpack_files,
    unpack_files_cli_hook,
)


@pytest.fixture
def pdf_with_attachment(tmp_path):
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.attachments["test.txt"] = b"content"
    path = tmp_path / "attached.pdf"
    pdf.save(path)
    return str(path)


@pytest.fixture
def pdf_no_attachment(tmp_path):
    pdf = pikepdf.new()
    pdf.add_blank_page()
    path = tmp_path / "clean.pdf"
    pdf.save(path)
    return str(path)


def test_dump_files_operation(pdf_with_attachment, capsys):
    """Test the 'dump_files' operation (Lines 184-186)."""
    with pikepdf.open(pdf_with_attachment) as pdf:
        result = dump_files("fname", pdf, lambda x: x, output_dir=None)
        dump_files_cli_hook(result, None)

    out = capsys.readouterr().out
    assert "test.txt" in out
    assert "7" in out


def test_unpack_prompt(pdf_with_attachment, tmp_path):
    """Test 'PROMPT' for output directory (Line 122)."""
    with pikepdf.open(pdf_with_attachment) as pdf:
        mock_input = lambda msg, **kwargs: str(tmp_path)
        result = unpack_files(pdf, mock_input, output_dir="PROMPT")
        unpack_files_cli_hook(result, None)

    assert (tmp_path / "test.txt").exists()


def test_unpack_invalid_dir(pdf_with_attachment, tmp_path):
    """Test error when output is not a directory (Line 129)."""
    file_path = tmp_path / "im_a_file"
    file_path.touch()

    with pikepdf.open(pdf_with_attachment) as pdf:
        # Should catch ValueError and log error, returning None
        result = unpack_files(pdf, None, output_dir=str(file_path))
        unpack_files_cli_hook(result, None)


def test_no_attachments_unpack(pdf_no_attachment, caplog):
    """Test handling of PDF with no attachments (Line 107, 157)."""
    with caplog.at_level(logging.DEBUG, logger="pdftl"):
        with pikepdf.open(pdf_no_attachment) as pdf:
            result = unpack_files(pdf, None)
            unpack_files_cli_hook(result, None)

    assert "No attachments found" in caplog.text


def test_no_attachments_dump_files(pdf_no_attachment, capsys):
    """Test dump_files on PDF with no attachments (Line 155)."""
    with pikepdf.open(pdf_no_attachment) as pdf:
        result = dump_files("fname", pdf, None)
        dump_files_cli_hook(result, None)

    out = capsys.readouterr().out
    assert "No attachments found" in out


def test_write_error(pdf_with_attachment, tmp_path, caplog):
    """Test OSError handling during write (Lines 178-179)."""
    caplog.set_level(logging.WARNING)

    with pikepdf.open(pdf_with_attachment) as pdf:
        # Simple patch: make open() raise OSError immediately
        with patch("builtins.open") as mock_file:
            mock_file.side_effect = OSError("Disk full")
            result = unpack_files(pdf, None, output_dir=str(tmp_path))
            unpack_files_cli_hook(result, None)

    assert "Could not write file" in caplog.text
