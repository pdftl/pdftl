from unittest.mock import patch

from pdftl.utils.io_helpers import can_read_file


@patch("pathlib.Path")
def test_can_read_file_exists(mock_Path):
    mock_p_instance = mock_Path.return_value
    mock_p_instance.is_file.return_value = True
    mock_p_instance.open.return_value.__enter__.return_value = None
    assert can_read_file("good.txt") is True


@patch("pathlib.Path")
def test_can_read_file_not_a_file(mock_Path):
    mock_p_instance = mock_Path.return_value
    mock_p_instance.is_file.return_value = False
    assert can_read_file("dir/") is False


def test_smart_pikepdf_open_with_password(tmp_path):
    import pikepdf

    from pdftl.utils.io_helpers import smart_pikepdf_open

    pdf_path = tmp_path / "enc.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.save(str(pdf_path), encryption=pikepdf.Encryption(user="test", owner="test"))
    result = smart_pikepdf_open(str(pdf_path), password="test")
    assert len(result.pages) == 1


def test_smart_pikepdf_open_from_stdin(tmp_path):
    import io
    from unittest.mock import patch

    import pikepdf

    from pdftl.utils.io_helpers import smart_pikepdf_open

    pdf = pikepdf.new()
    pdf.add_blank_page()
    buf = io.BytesIO()
    pdf.save(buf)
    pdf_bytes = buf.getvalue()

    with patch("sys.stdin") as mock_stdin:
        mock_stdin.buffer.read.return_value = pdf_bytes
        result = smart_pikepdf_open(None)
        assert len(result.pages) == 1
