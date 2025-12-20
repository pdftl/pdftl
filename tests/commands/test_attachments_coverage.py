import logging
from unittest.mock import patch

import pikepdf
import pytest

from pdftl.commands.attachments import unpack_files


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


def test_list_files_operation(pdf_with_attachment, capsys):
    """Test the 'list_files' operation (Lines 184-186)."""
    with pikepdf.open(pdf_with_attachment) as pdf:
        unpack_files("fname", pdf, lambda x: x, output_dir=None, operation="list_files")

    out = capsys.readouterr().out
    assert "test.txt" in out
    assert "7" in out


def test_unpack_prompt(pdf_with_attachment, tmp_path):
    """Test 'PROMPT' for output directory (Line 122)."""
    with pikepdf.open(pdf_with_attachment) as pdf:
        mock_input = lambda msg, **kwargs: str(tmp_path)
        unpack_files("fname", pdf, mock_input, output_dir="PROMPT", operation="unpack_files")

    assert (tmp_path / "test.txt").exists()


def test_unpack_invalid_dir(pdf_with_attachment, tmp_path):
    """Test error when output is not a directory (Line 129)."""
    file_path = tmp_path / "im_a_file"
    file_path.touch()

    with pikepdf.open(pdf_with_attachment) as pdf:
        # Should catch ValueError and log error, returning None
        unpack_files("fname", pdf, None, output_dir=str(file_path), operation="unpack_files")


def test_no_attachments_unpack(pdf_no_attachment, caplog):
    """Test handling of PDF with no attachments (Line 107, 157)."""
    with caplog.at_level(logging.DEBUG, logger="pdftl"):
        with pikepdf.open(pdf_no_attachment) as pdf:
            unpack_files("fname", pdf, None, operation="unpack_files")

    assert "No attachments found" in caplog.text


def test_no_attachments_list(pdf_no_attachment, capsys):
    """Test list_files on PDF with no attachments (Line 155)."""
    with pikepdf.open(pdf_no_attachment) as pdf:
        unpack_files("fname", pdf, None, operation="list_files")

    out = capsys.readouterr().out
    assert "No attachments found" in out


def test_write_error(pdf_with_attachment, tmp_path, caplog):
    """Test OSError handling during write (Lines 178-179)."""
    caplog.set_level(logging.WARNING)

    with pikepdf.open(pdf_with_attachment) as pdf:
        # Simple patch: make open() raise OSError immediately
        with patch("builtins.open") as mock_file:
            mock_file.side_effect = OSError("Disk full")
            unpack_files("fname", pdf, None, output_dir=str(tmp_path), operation="unpack_files")

    assert "Could not write file" in caplog.text
