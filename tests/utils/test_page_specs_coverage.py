import pytest

from pdftl.exceptions import UserCommandLineError
from pdftl.utils.page_specs import _expand_square_brackets, _flatten_spec_list


def test_expand_square_brackets_logic():
    """
    Tests the internal group expansion logic to cover lines 130-149.
    """
    # 1. Test standard group expansion: [1, 3]r90 -> 1r90, 3r90
    specs = ["[1, 3]r90"]
    result = _expand_square_brackets(specs)
    assert result == ["1r90", "3r90"]

    # 2. Test mixed input (regular specs + groups)
    specs = ["5", "[1,2]x2"]
    result = _expand_square_brackets(specs)
    assert result == ["5", "1x2", "2x2"]


def test_expand_square_brackets_ambiguity_guardrail():
    """
    Tests that a comma in the suffix raises an error.
    Covers lines 136-142.
    """
    # This spec is ambiguous: does it mean ([1,2]x2), 3 OR [1,2](x2,3)?
    # The code forbids it to prevent user error.
    specs = ["[1, 2]x2, 3"]

    with pytest.raises(UserCommandLineError) as excinfo:
        _expand_square_brackets(specs)

    assert "Found a comma after the closing bracket" in str(excinfo.value)


def test_flatten_spec_list_ignores_none():
    """
    Tests that None entries are skipped.
    Covers line 240.
    """
    specs = ["1", None, "2,3"]
    result = _flatten_spec_list(specs)
    assert result == ["1", "2", "3"]
