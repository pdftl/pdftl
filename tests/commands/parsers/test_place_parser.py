# tests/commands/parsers/test_place_parser.py

import pytest

from pdftl.commands.parsers.place_parser import parse_place_args
from pdftl.exceptions import UserCommandLineError


def test_parse_simple_shift():
    args = ["1-5(shift=10,20)"]
    cmds = parse_place_args(args)
    assert len(cmds) == 1
    assert cmds[0].page_spec == "1-5"
    assert len(cmds[0].operations) == 1
    op = cmds[0].operations[0]
    assert op.name == "shift"
    assert op.params["dx"] == ["10"]
    assert op.params["dy"] == ["20"]


def test_parse_compound_math():
    """Test that math like '50%+1in' is tokenized into a list."""
    args = ["odd(shift=50%+1in, -20pt)"]
    cmd = parse_place_args(args)[0]
    op = cmd.operations[0]
    # Expect the sign to be preserved
    assert op.params["dx"] == ["50%", "+1in"]
    assert op.params["dy"] == ["-20pt"]


def test_parse_scale_with_named_anchor():
    args = ["1(scale=0.5:top-left)"]
    cmd = parse_place_args(args)[0]
    op = cmd.operations[0]
    assert op.name == "scale"
    assert op.params["value"] == "0.5"
    assert op.params["anchor_type"] == "named"
    assert op.params["anchor_name"] == "top-left"


def test_parse_spin_with_coord_anchor():
    """Test spin=45:50%,50%"""
    args = ["1(spin=45:50%,50%)"]
    cmd = parse_place_args(args)[0]
    op = cmd.operations[0]
    assert op.name == "spin"
    assert op.params["value"] == "45"
    assert op.params["anchor_type"] == "coord"
    assert op.params["anchor_x"] == ["50%"]
    assert op.params["anchor_y"] == ["50%"]


def test_parse_multiple_ops():
    """Test chaining: shift then scale via semicolon"""
    args = ["all(shift=10,10; scale=2)"]
    cmd = parse_place_args(args)[0]
    ops = cmd.operations
    assert len(ops) == 2
    assert ops[0].name == "shift"
    assert ops[1].name == "scale"


def test_parser_errors():
    """Check invalid syntax handling."""
    with pytest.raises(UserCommandLineError, match="Invalid syntax"):
        parse_place_args(["1 shift=10,10"])

    with pytest.raises(UserCommandLineError, match="Unknown operation"):
        parse_place_args(["1(explode=50)"])

    # Shift missing y (comma)
    with pytest.raises(UserCommandLineError, match="Shift requires x,y"):
        parse_place_args(["1(shift=100)"])




def test_place_parser_implicit_pages():
    """
    Covers line 30: arg = "1-end" + arg
    Triggered when the argument starts with '('.
    """
    args = ["(shift=10,10)"]
    commands = parse_place_args(args)

    assert len(commands) == 1
    assert commands[0].page_spec == "1-end"
    assert commands[0].operations[0].name == "shift"


def test_place_parser_invalid_op_format():
    """
    Covers line 57: raise UserCommandLineError... if "=" not in token
    Triggered when an operation lacks an equals sign.
    """
    args = ["1(shift=10,10;invalid_op)"]

    with pytest.raises(UserCommandLineError) as exc:
        parse_place_args(args)

    assert "Invalid operation format" in str(exc.value)
    assert "invalid_op" in str(exc.value)
