import pytest

from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.dump import get_json_flag


def test_get_json_flag_returns_true():
    assert get_json_flag(["json"], "op") is True


def test_get_json_flag_returns_false():
    assert get_json_flag([], "op") is False


def test_get_json_flag_too_many_args():
    with pytest.raises(InvalidArgumentError, match="Too many arguments"):
        get_json_flag(["json", "extra"], "op")


def test_get_json_flag_invalid_arg():
    with pytest.raises(InvalidArgumentError, match="Invalid"):
        get_json_flag(["xml"], "op")
