import pytest
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.parsers.modify_layers_parser import parse_modify_layers_rules


def test_parse_default_all():
    args = ["strip"]
    r_id, r_name, default = parse_modify_layers_rules(args)
    assert default == {"strip"}
    assert not r_id
    assert not r_name

    args = ["merge", "all"]
    r_id, r_name, default = parse_modify_layers_rules(args)
    assert default == {"merge"}


def test_parse_explicit_targets():
    args = ["strip", "id=42", "merge", "name=Background"]
    r_id, r_name, default = parse_modify_layers_rules(args)
    assert r_id == {42: {"strip"}}
    assert r_name == {"Background": {"merge"}}
    assert default == {"keep"}


def test_parse_sloppy_names():
    args = ["strip", "English", "keep", "German"]
    r_id, r_name, default = parse_modify_layers_rules(args)
    assert r_name == {"English": {"strip"}, "German": {"keep"}}
    assert not r_id
    assert default == {"keep"}


def test_parse_mixed_actions():
    args = ["strip", "id=10", "merge", "Header", "keep", "all"]
    r_id, r_name, default = parse_modify_layers_rules(args)
    assert r_id == {10: {"strip"}}
    assert r_name == {"Header": {"merge"}}
    assert default == {"keep"}


def test_invalid_action():
    with pytest.raises(InvalidArgumentError, match="unknown action 'delete'"):
        parse_modify_layers_rules(["delete", "id=5"])


def test_invalid_id_type():
    with pytest.raises(InvalidArgumentError, match="id must be an integer"):
        parse_modify_layers_rules(["strip", "id=abc"])
