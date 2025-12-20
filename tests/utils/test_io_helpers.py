from unittest.mock import patch

from pdftl.utils.io_helpers import can_read_file


@patch("pdftl.utils.io_helpers.Path")
def test_can_read_file_exists(mock_Path):
    mock_p_instance = mock_Path.return_value
    mock_p_instance.is_file.return_value = True
    mock_p_instance.open.return_value.__enter__.return_value = None
    assert can_read_file("good.txt") is True


@patch("pdftl.utils.io_helpers.Path")
def test_can_read_file_not_a_file(mock_Path):
    mock_p_instance = mock_Path.return_value
    mock_p_instance.is_file.return_value = False
    assert can_read_file("dir/") is False
