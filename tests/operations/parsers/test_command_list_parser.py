import pytest
from pdftl.exceptions import UserCommandLineError
from pdftl.operations.parsers.command_list_parser import (
    split_spec_and_ops,
    split_semicolon_tokens,
    split_key_value,
)


def test_split_spec_and_ops():
    # Standard format
    assert split_spec_and_ops("1-5(op1=a; op2=b)") == ("1-5", "op1=a; op2=b")

    # Bare parentheses fallback to '1-end'
    assert split_spec_and_ops("(autocontrast)") == ("1-end", "autocontrast")

    # Extra whitespace
    assert split_spec_and_ops("  odd ( resize=true )  ") == ("odd", "resize=true")

    # Invalid syntaxes
    with pytest.raises(UserCommandLineError, match="Invalid command syntax"):
        split_spec_and_ops("1-5 no_parens")

    with pytest.raises(UserCommandLineError, match="Invalid command syntax"):
        split_spec_and_ops("1-5(unclosed")


def test_split_semicolon_tokens():
    # Normal splits
    assert split_semicolon_tokens("op1=1; op2=2") == ["op1=1", "op2=2"]

    # Handling trailing, leading, and repeated semicolons (ignoring empty parts)
    assert split_semicolon_tokens("; op1=1 ;; op2=2 ; ") == ["op1=1", "op2=2"]

    # Completely empty or blank string
    assert split_semicolon_tokens("   ;;;  ") == []


def test_split_key_value():
    # Standard usage
    assert split_key_value("contrast = 1.5") == ("contrast", "1.5")
    assert split_key_value("sharpen=true") == ("sharpen", "true")

    # Preserves additional '=' signs in the value
    assert split_key_value("name=my=weird=value") == ("name", "my=weird=value")

    # Error: Missing '='
    with pytest.raises(UserCommandLineError, match="Invalid operation format near 'just_a_key'"):
        split_key_value("just_a_key")

    # Error with context specified
    with pytest.raises(UserCommandLineError, match="in 'Context Name'"):
        split_key_value("bad_token", context="Context Name")
