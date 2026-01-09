import pytest

from pdftl.exceptions import UserCommandLineError
from pdftl.operations.parsers.move_parser import parse_move_args


def test_parse_move_args_empty():
    """
    Covers line 19: raise UserCommandLineError if args is empty.
    """
    with pytest.raises(UserCommandLineError, match="Move command requires arguments"):
        parse_move_args([])
