from pathlib import Path

import pikepdf
import pytest

from pdftl.commands.attachments import unpack_files


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
        # input_filename is mainly for logging/reference
        input_filename = "dummy_input.pdf"

        # get_input is a callable, likely used if specific files need to be selected
        # We pass a dummy lambda that returns empty or None
        mock_get_input = lambda: None

        # Run the command
        unpack_files(
            input_filename,
            pdf,
            get_input=mock_get_input,
            output_dir=str(tmp_path),
            operation="unpack_files",  # Explicitly setting operation if needed
        )

        # Assertion: Check if file exists in output dir
        expected_file = tmp_path / "hello.txt"
        assert expected_file.exists()
        assert expected_file.read_text() == "Hello World"
