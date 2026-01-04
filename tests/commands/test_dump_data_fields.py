from unittest.mock import MagicMock

from pdftl.commands.dump_data_fields import _extract_field_justification, dump_fields_cli_hook


def test_dump_fields_cli_hook_no_data(capsys):
    """
    Covers line 102: if not result.data: return
    Triggered when result.data is empty.
    """
    mock_result = MagicMock()
    mock_result.data = []  # Empty data

    mock_stage = MagicMock()

    # Should return immediately and print nothing
    dump_fields_cli_hook(mock_result, mock_stage)

    captured = capsys.readouterr()
    assert captured.out == ""


def test_extract_justification_exceptions():
    """
    Covers lines 243-244: except (IndexError, ValueError) -> return 'Left'
    Triggered when the 'Q' attribute (Quadding/Justification) is invalid.
    """
    # 1. Test IndexError (Q value out of range 0-2)
    field_index_err = MagicMock()
    field_index_err.obj.Q = 5  # Valid align is 0,1,2

    res = _extract_field_justification(field_index_err, "Tx")
    assert res == "Left"

    # 2. Test ValueError (Q value is not an integer)
    field_value_err = MagicMock()
    field_value_err.obj.Q = "invalid_int"

    res = _extract_field_justification(field_value_err, "Tx")
    assert res == "Left"
