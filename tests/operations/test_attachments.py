from pathlib import Path

import pikepdf
import pytest

from pdftl.core.core_types import OpResult
from pdftl.operations.attachments import dump_files_cli_hook, unpack_files, unpack_files_cli_hook
from unittest.mock import MagicMock
from pdftl.operations.attachments import dump_files


@pytest.fixture
def pdf_with_attachment(two_page_pdf):
    """Creates a PDF that has a dummy text file attached."""
    # We open the fixture PDF and add an attachment
    with pikepdf.open(two_page_pdf) as pdf:
        # Create a simple attachment
        # usage: pdf.attachments[filename] = data
        pdf.attachments["hello.txt"] = b"Hello World"

        # Save to a new temporary path
        output_path = Path(two_page_pdf).parent / "attached.pdf"
        pdf.save(output_path)
        return str(output_path)


def test_unpack_files(pdf_with_attachment, tmp_path):
    """Test extracting attachments to a directory."""
    with pikepdf.open(pdf_with_attachment) as pdf:
        # Prepare arguments

        # get_input is a callable, likely used if specific files need to be selected
        # We pass a dummy lambda that returns empty or None
        def mock_get_input():
            return None

        # Run the command
        result = unpack_files(
            pdf,
            get_input=mock_get_input,
            output_dir=str(tmp_path),
        )
        unpack_files_cli_hook(result, None, None)

        # Assertion: Check if file exists in output dir
        expected_file = tmp_path / "hello.txt"
        assert expected_file.exists()
        assert expected_file.read_text() == "Hello World"


def test_attachments_hooks_failures():
    """Test early returns and error raises in hooks."""

    # 1. Failure (success=False) -> Should return silently (Lines 76, 99)
    # We just ensure it doesn't raise exception
    fail_res = OpResult(success=False)
    dump_files_cli_hook(fail_res, None, None)
    unpack_files_cli_hook(fail_res, None, None)

    # 2. Missing Metadata -> Should raise AttributeError (Lines 79, 102)
    success_no_meta = OpResult(success=True, meta=None)

    with pytest.raises(AttributeError, match="Missing metadata"):
        dump_files_cli_hook(success_no_meta, None, None)

    with pytest.raises(AttributeError, match="Missing metadata"):
        unpack_files_cli_hook(success_no_meta, None, None)


def test_dump_files_invalid_attachment_data():
    """Ensure dump_files does not crash if an attachment's data is corrupted/None."""
    mock_pdf = MagicMock()
    mock_pdf.pages = []

    # 1. Setup Valid Attachment
    att_valid = MagicMock()
    att_valid.obj.objgen = 1

    mock_file_valid = MagicMock()
    mock_file_valid.size = 4
    att_valid.get_file.return_value = mock_file_valid

    # 2. Setup Invalid Attachment using a dummy class to reliably trigger AttributeError
    class CorruptedFileDump:
        @property
        def size(self):
            raise AttributeError("'NoneType' object has no attribute 'size'")

    att_invalid = MagicMock()
    att_invalid.obj.objgen = 2
    att_invalid.get_file.return_value = CorruptedFileDump()

    mock_pdf.attachments = {"good.txt": att_valid, "bad.txt": att_invalid}

    # Execute
    result = dump_files("test.pdf", mock_pdf, MagicMock())

    # Verify
    assert result.success is True
    sizes = {item["name"]: item["size"] for item in result.data}
    assert sizes["good.txt"] == 4
    assert sizes["bad.txt"] == "unknown"  # Matches the fallback value in implementation


def test_unpack_files_invalid_attachment_data():
    """Ensure unpack_files skips corrupted attachments without crashing."""
    mock_pdf = MagicMock()
    mock_pdf.pages = []

    # Setup Invalid Attachment using a dummy class to reliably trigger AttributeError
    class CorruptedFileUnpack:
        def read_bytes(self):
            raise AttributeError("'NoneType' object has no attribute 'read_bytes'")

    att_invalid = MagicMock()
    att_invalid.obj.objgen = 1
    att_invalid.get_file.return_value = CorruptedFileUnpack()

    mock_pdf.attachments = {"bad.txt": att_invalid}

    # Execute
    result = unpack_files(mock_pdf, MagicMock())
    generator = result.data

    # Generator should yield nothing and handle exception gracefully
    extracted = list(generator)
    assert len(extracted) == 0


def test_dump_files_with_page_pinned_attachments():
    """Ensure dump_files detects annotations and maps attachments to specific pages."""
    mock_pdf = MagicMock()

    # 1. Setup a mock annotation dictionary that mimics a page-pinned file attachment
    mock_fs = MagicMock()
    mock_fs.objgen = (5, 0)  # The unique PDF object identifier

    mock_annot = MagicMock()

    # Mock the internal .get() mapping for key lookups inside the loop
    def mock_get(key):
        if key == "/Subtype":
            return "/FileAttachment"
        if key == "/FS":
            return mock_fs
        return None

    mock_annot.get.side_effect = mock_get

    # 2. Assign the annotation to Page 1
    mock_page = MagicMock()
    mock_page.__contains__.return_value = True  # Handles: if "/Annots" in page
    mock_page.Annots = [mock_annot]
    mock_pdf.pages = [mock_page]

    # 3. Setup the corresponding document attachment matching that same objgen
    mock_attachment = MagicMock()
    mock_attachment.obj.objgen = (5, 0)
    mock_attachment.get_file.return_value.size = 1024

    mock_pdf.attachments = {"pinned_document.pdf": mock_attachment}

    # Execute
    result = dump_files("input.pdf", mock_pdf, MagicMock())

    # Verify both the mapping loop and the string formatting logic ran successfully
    assert result.success is True
    assert len(result.data) == 1
    assert result.data[0]["name"] == "pinned_document.pdf"
    assert result.data[0]["location"] == "Pages:1"  # Confirms lines 183-184 are covered
    assert result.data[0]["size"] == 1024
