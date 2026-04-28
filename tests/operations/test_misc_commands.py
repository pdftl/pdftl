import pikepdf
import pytest

from pdftl.operations.update_info import update_info


# --- UPDATE_INFO TESTS ---


@pytest.fixture
def metadata_file(tmp_path):
    """Creates a dummy dump_data formatted text file."""
    content = "InfoKey: Title\nInfoValue: New Title\nInfoKey: Author\nInfoValue: Test Author\n"
    # update_info opens in 'rb' mode, so we write bytes
    f = tmp_path / "meta.txt"
    f.write_bytes(content.encode("utf-8"))
    return str(f)


def test_update_info_basic(two_page_pdf, metadata_file):
    """Test updating PDF metadata from file."""
    with pikepdf.open(two_page_pdf) as pdf:
        # op_args expects [filename]
        op_args = [metadata_file]

        def mock_input(msg, **kwargs):
            return None

        update_info(pdf, op_args, mock_input)

        # Verify changes
        assert pdf.docinfo["/Title"] == "New Title"
        assert pdf.docinfo["/Author"] == "Test Author"


def test_update_info_missing_arg(two_page_pdf):
    """Test error when no metadata filename is provided."""
    from pdftl.exceptions import MissingArgumentError

    with pikepdf.open(two_page_pdf) as pdf:
        with pytest.raises(MissingArgumentError):
            update_info(pdf, [], lambda x: x)
