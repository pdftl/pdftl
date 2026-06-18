import pytest
from pdftl.exceptions import UserCommandLineError
from pdftl.operations.parsers.modify_images_parser import (
    parse_modify_images_args,
    _parse_operations,
    _parse_modify_params,
)


def test_parse_modify_images_args():
    args = [
        " 1-3(contrast=1.5; blur=2) ",
        "",  # Should be skipped
        "  ",  # Should be skipped
        "(autocontrast)",
    ]

    commands = parse_modify_images_args(args)

    assert len(commands) == 2
    assert commands[0].page_spec == "1-3"
    assert len(commands[0].operations) == 2
    assert commands[0].operations[0].name == "contrast"

    assert commands[1].page_spec == "1-end"
    assert len(commands[1].operations) == 1
    assert commands[1].operations[0].name == "autocontrast"


def test_parse_operations():
    # Standard key-value
    ops = _parse_operations("Contrast=1.5; Blur=true")
    assert len(ops) == 2
    assert ops[0].name == "contrast"  # verifies lowercasing
    assert ops[0].params["value"] == "1.5"
    assert ops[1].name == "blur"

    # Standalone flag fallback to 'true'
    ops = _parse_operations("Autocontrast")
    assert len(ops) == 1
    assert ops[0].name == "autocontrast"
    assert ops[0].params["value"] == "true"

    # Catching empty/malformed keys from weird edge-case inputs
    # (e.g. if an `=` is provided with nothing before it)
    with pytest.raises(UserCommandLineError, match="Invalid empty filter element"):
        _parse_operations("=1.5")


def test_parse_modify_params():
    # Single string value
    params = _parse_modify_params("contrast", "1.5")
    assert params == {"value": "1.5"}

    # Comma-separated list value
    params = _parse_modify_params("levels", "10, 200, 1.2")
    assert params["value"] == "10, 200, 1.2"
    assert params["values"] == ["10", "200", "1.2"]
