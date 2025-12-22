from unittest.mock import patch

import pytest

from pdftl.cli import parser
from pdftl.core import constants as c
from pdftl.exceptions import InvalidArgumentError, MissingArgumentError


def test_missing_multiple_arguments_error():
    """Hits line 62: Missing value for multiple-value option."""
    with pytest.raises(MissingArgumentError, match="Missing value for option 'attach_files'"):
        parser._parse_multiple_arguments("attach_files", ["attach_files"], 0, lambda x: True)


def test_missing_output_value_error():
    """Hits line 149: Missing value for 'output' keyword."""
    with pytest.raises(MissingArgumentError, match="Missing value for keyword: output"):
        parser.parse_options_and_specs(["output"])


def test_parse_cli_stages_complete_flow():
    """Hits lines 319-364: Full pipeline parsing and global option aggregation."""
    # To ensure tokens like 'output' go to 'options', the operation must come BEFORE them
    # in the stage. Pre-operation = inputs. Post-operation = options/specs.
    args = [
        "file1.pdf",
        "file2.pdf",
        "merge",
        "---",
        "filter",
        "encrypt_aes256",
        "user_pw",
        "secret",
        "allow",
        "Printing",
        "CopyContents",
        "output",
        "final.pdf",
    ]

    # Mock registry for operations and options
    with patch("pdftl.core.registry.registry.operations", {"merge": {}, "filter": {}}):
        with patch(
            "pdftl.core.registry.registry.options",
            {
                "encrypt_aes256": {"type": "flag"},
                "user_pw": {"type": "mandatory argument"},
                "output": {"type": "mandatory argument"},
            },
        ):
            # Re-initialize the keywords based on mocked registry
            parser.FLAG_KEYWORDS = parser._get_registry_data_entries(
                "options", "type", lambda x: x == "flag"
            )
            parser.VALUE_KEYWORDS = parser._get_registry_data_entries(
                "options", "type", lambda x: "mandatory argument" in x, lambda x: x.split(" ")[0]
            )

            stages, globals_dict = parser.parse_cli_stages(args)

            # Verify aggregation (Lines 335-364)
            assert len(stages) == 2
            assert globals_dict[c.OUTPUT] == "final.pdf"
            assert globals_dict["encrypt_aes256"] is True
            assert globals_dict["user_pw"] == "secret"
            # Permission values are mapped to constants/integers in ALLOW_PERMISSIONS_L
            assert "allow" in globals_dict
            assert len(globals_dict["allow"]) == 2


def test_unknown_arg_error_with_allow_hint():
    """Hits line 79-83: Unknown argument error with the 'allow' hint."""
    # To hit line 79, we need _parse_allow_permissions to SUCCEED,
    # setting just_slurped_allow_index, and then have the NEXT arg be unknown.
    # Note: 'Printing' is valid, 'UnknownThing' is not.
    # We use parse_options_and_specs directly.

    with patch("pdftl.core.registry.registry.options", {}):
        # Ensure FLAG_KEYWORDS/VALUE_KEYWORDS are empty so everything is 'unknown'
        parser.FLAG_KEYWORDS = set()
        parser.VALUE_KEYWORDS = set()

        args = ["allow", "Printing", "UnknownThing"]

        with pytest.raises(InvalidArgumentError) as excinfo:
            parser.parse_options_and_specs(args)

        assert "Maybe you wanted to give an additional 'allow' permission?" in str(excinfo.value)


def test_handle_pipeline_input_injection():
    """Hits lines 196-198: Injecting '_' for non-first stages."""
    inputs = ["file.pdf"]
    handles = {"A": 0}
    new_inputs, new_handles = parser._handle_pipeline_input(inputs, handles, is_first_stage=False)

    assert new_inputs[0] == "_"
    assert new_handles["A"] == 1
    assert new_handles["_"] == 0


def test_assign_passwords_stop_iteration():
    """Hits line 231: Break when passwords_by_order is exhausted."""
    passwords = parser._assign_passwords(
        num_inputs=3, handles={}, passwords_by_handle={}, passwords_by_order=["pass1"]
    )
    assert passwords == ["pass1", None, None]
