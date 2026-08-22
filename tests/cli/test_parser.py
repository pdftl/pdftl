from unittest.mock import MagicMock, patch

import pytest

# --- Import module and functions to test ---
from pdftl.cli import parser
from pdftl.cli import parser as parser_module
from pdftl.cli.constants import SUB_EACH, SUB_END, SUB_START
from pdftl.cli.parser import (
    _assign_passwords,
    _find_operation_and_split,
    _handle_pipeline_input,
    _parse_allow_permissions,
    _parse_file_handles,
    _parse_flag_keyword,
    _parse_multiple_arguments,
    _parse_passwords,
    _parse_pre_operation_args,
    _parse_value_keyword,
    _raise_unknown_arg_error,
    _recursive_group_pipelines,
    _separate_file_and_pw_args,
    parse_cli_stage,
    parse_options_and_specs,
    split_args_by_separator,
)
from pdftl.cli.pipeline import CliStage
from pdftl.core import constants as constants_module

# --- Import Exceptions ---
from pdftl.exceptions import DuplicateArgumentError, InvalidArgumentError, MissingArgumentError

# --- Mocks for Dependencies ---


@pytest.fixture
def mock_registry(mocker):
    """Mocks the global operations registry."""
    mock_reg = MagicMock()
    mock_reg.operations = {"cat", "burst", "dump_data"}
    mocker.patch.dict(parser_module.registry.__dict__, {"operations": mock_reg.operations})
    return mock_reg


@pytest.fixture
def mock_constants(mocker):
    """Mocks the keyword constants."""
    mock_flags = {"encrypt_128bit", "uncompress", "linearize"}
    mock_values = {"output", "owner_pw", "user_pw"}
    mock_allow = {"Printing", "Copying", "Assembly"}
    mock_allow_l = {
        "printing": "Printing",
        "copying": "Copying",
        "assembly": "Assembly",
    }

    mocker.patch.object(parser_module, "_get_flag_keywords", return_value=mock_flags)
    mocker.patch.object(parser_module, "_get_value_keywords", return_value=mock_values)
    mocker.patch.object(constants_module, "ALLOW_PERMISSIONS", mock_allow)
    mocker.patch.object(constants_module, "ALLOW_PERMISSIONS_L", mock_allow_l)


@pytest.fixture(autouse=True)
def patch_logging(mocker):
    """Patches logging for all tests."""
    mocker.patch("pdftl.cli.parser.logging")


# ==================================================================
# === Tests for Public Functions
# ==================================================================


class TestPublicFunctions:
    def test_split_args_by_separator(self):
        argv = ["a", "b", "---", "c", "d", "e", "---", "f"]
        assert split_args_by_separator(argv) == [
            ["a", "b"],
            ["c", "d", "e"],
            ["f"],
        ]

    def test_split_args_no_separator(self):
        argv = ["a", "b", "c"]
        assert split_args_by_separator(argv) == [["a", "b", "c"]]

    def test_split_args_empty(self):
        assert split_args_by_separator([]) == [[]]

    @patch("pdftl.cli.parser._parse_pre_operation_args")
    @patch("pdftl.cli.parser.parse_options_and_specs")
    def test_parse_cli_stage_explicit_op(self, mock_parse_options, mock_parse_pre, mock_registry):
        """Tests parsing a stage with an explicit operation 'cat'."""
        args = ["in.pdf", "A=in2.pdf", "cat", "1-end", "output", "out.pdf"]

        mock_parse_pre.return_value = (["in.pdf", "in2.pdf"], {"A": 1}, ["pw1", None])
        mock_parse_options.return_value = (["1-end"], {"output": "out.pdf"})

        stage = parse_cli_stage(args, is_first_stage=True)

        # Check that pre-op args were parsed
        pre_op_args = ["in.pdf", "A=in2.pdf"]
        mock_parse_pre.assert_called_once_with(pre_op_args, True)

        # Check that post-op args were parsed
        post_op_args = ["1-end", "output", "out.pdf"]
        mock_parse_options.assert_called_once_with(post_op_args)

        # Check the final CliStage object
        assert isinstance(stage, CliStage)
        assert stage.operation == "cat"
        assert stage.inputs == ["in.pdf", "in2.pdf"]
        assert stage.handles == {"A": 1}
        assert stage.input_passwords == ["pw1", None]
        assert stage.operation_args == ["1-end"]
        assert stage.options == {"output": "out.pdf"}

    @patch("pdftl.cli.parser._parse_pre_operation_args")
    @patch("pdftl.cli.parser.parse_options_and_specs")
    def test_parse_cli_stage_implicit_filter(
        self, mock_parse_options, mock_parse_pre, mock_registry
    ):
        """Tests parsing a stage with no operation, implying 'filter'."""
        args = ["in.pdf", "output", "out.pdf"]

        mock_parse_pre.return_value = (["in.pdf"], {}, [None])
        mock_parse_options.return_value = ([], {"output": "out.pdf"})

        stage = parse_cli_stage(args, is_first_stage=True)

        # No operation found, so pre_op gets all args
        mock_parse_pre.assert_called_once_with(args, True)
        # Post-op is empty
        mock_parse_options.assert_called_once_with([])

        assert stage.operation == "filter"
        assert stage.inputs == ["in.pdf"]
        assert stage.options == {"output": "out.pdf"}

    @patch("pdftl.cli.parser._parse_pre_operation_args")
    @patch("pdftl.cli.parser.parse_options_and_specs")
    def test_parse_cli_stage_empty_filter(self, mock_parse_options, mock_parse_pre, mock_registry):
        """Tests that empty args defaults to a filter operation."""
        mock_parse_pre.return_value = ([], {}, [])
        mock_parse_options.return_value = ([], {})

        stage = parse_cli_stage([], is_first_stage=True)

        mock_parse_pre.assert_called_once_with([], True)
        mock_parse_options.assert_called_once_with([])
        assert stage.operation == "filter"


# ==================================================================
# === Tests for Private Helper Functions
# ==================================================================


class TestPrivateHelpers:
    def test_find_operation_and_split(self, mock_registry):
        args = ["in.pdf", "A=in2.pdf", "cat", "1-end", "output", "out.pdf"]
        op, pre, post = _find_operation_and_split(args)
        assert op == "cat"
        assert pre == ["in.pdf", "A=in2.pdf"]
        assert post == ["1-end", "output", "out.pdf"]

    def test_find_operation_none(self, mock_registry):
        args = ["in.pdf", "output", "out.pdf"]
        op, pre, post = _find_operation_and_split(args)
        assert op is None
        assert pre == args
        assert post == []

    def test_parse_flag_keyword(self):
        options = {}
        consumed = _parse_flag_keyword("uncompress", options)
        assert consumed == 1
        assert options == {"uncompress": True}

    def test_parse_value_keyword(self):
        options = {}
        args = ["output", "out.pdf"]
        consumed = _parse_value_keyword("output", args, 0, options)
        assert consumed == 2
        assert options == {"output": "out.pdf"}

    def test_parse_value_keyword_error(self):
        with pytest.raises(MissingArgumentError):
            _parse_value_keyword("output", ["output"], 0, {})

    def test_parse_multiple_arguments(self):
        args = ["allow", "Printing", "Copying", "output"]

        def q(x):
            return x.lower() in ["printing", "copying"]

        consumed, end_pos = _parse_multiple_arguments("allow", args, 0, q)
        assert consumed == 3  # "allow", "Printing", "Copying"
        assert end_pos == 3

    def test_parse_multiple_arguments_error(self):
        args = ["allow", "output"]

        def q(x):
            return x.lower() in ["printing", "copying"]

        with pytest.raises(InvalidArgumentError, match="Invalid argument 'output'"):
            _parse_multiple_arguments("allow", args, 0, q, hint="test hint")

    def test_raise_unknown_arg_error(self, mock_constants):
        with pytest.raises(InvalidArgumentError, match="Unknown argument"):
            _raise_unknown_arg_error("bad_arg", False)

        with pytest.raises(InvalidArgumentError, match="Maybe you wanted.*allow"):
            _raise_unknown_arg_error("bad_arg", True)

    @patch("pdftl.cli.parser._parse_multiple_arguments", return_value=(3, 3))
    def test_parse_allow_permissions(self, mock_parse_multi, mock_constants):
        options = {}
        args = ["allow", "Printing", "copying"]

        consumed, end_pos = _parse_allow_permissions(args, 0, options)

        assert consumed == 3
        assert end_pos == 3
        assert options == {"allow": {"Printing", "Copying"}}  # Values are capitalized
        mock_parse_multi.assert_called_once()

    def testparse_options_and_specs_attach_error(self, mock_constants):
        """Integration test for the main options parser."""
        args = [
            "1-end",
            "A=2",
            "allow",
            "Printing",
            "uncompress",
            "output",
            "out.pdf",
            "attach_files",
            "f1.txt",
        ]

        with pytest.raises(InvalidArgumentError, match="Unknown argument.*attach_files"):
            _specs, _options = parse_options_and_specs(args)

    def testparse_options_and_specs(self, mock_constants):
        """Integration test for the main options parser."""
        args = [
            "1-end",
            "A=2",
            "allow",
            "Printing",
            "uncompress",
            "output",
            "out.pdf",
        ]

        specs, options = parse_options_and_specs(args)

        assert specs == ["1-end", "A=2"]  # Specs stop at first keyword
        assert options == {
            "allow": {"Printing"},
            "uncompress": True,
            "output": "out.pdf",
        }

    def testparse_options_and_specs_unknown_arg(self, mock_constants):
        """Tests that an unknown arg after options have started raises error."""
        args = ["1-end", "output", "out.pdf", "bad_arg"]
        with pytest.raises(InvalidArgumentError, match="Unknown argument.*bad_arg"):
            parse_options_and_specs(args)

    def test_separate_file_and_pw_args(self):
        args = ["in1.pdf", "A=in2.pdf", "input_pw", "pw1", "A=pw2"]
        files, pws = _separate_file_and_pw_args(args)
        assert files == ["in1.pdf", "A=in2.pdf"]
        assert pws == ["pw1", "A=pw2"]

        # Test no password
        args_no_pw = ["in1.pdf", "A=in2.pdf"]
        files, pws = _separate_file_and_pw_args(args_no_pw)
        assert files == args_no_pw
        assert pws == []

    def test_parse_file_handles(self):
        args = ["in1.pdf", "A=in2.pdf", "B=in3.pdf"]
        inputs, handles = _parse_file_handles(args)
        assert inputs == ["in1.pdf", "in2.pdf", "in3.pdf"]
        assert handles == {"A": 1, "B": 2}

    def test_handle_pipeline_input(self):
        # First stage, no change
        inputs, handles = _handle_pipeline_input(["in.pdf"], {}, True)
        assert inputs == ["in.pdf"]

        # Second stage, no pipe char -> injects '_'
        inputs, handles = _handle_pipeline_input(["in.pdf"], {}, False)
        assert inputs == ["_", "in.pdf"]
        assert handles == {"_": 0}

        # Second stage, pipe char exists -> no change
        inputs, handles = _handle_pipeline_input(["_", "in.pdf"], {}, False)
        assert inputs == ["_", "in.pdf"]

    def test_parse_passwords(self):
        args = ["pw1", "A=pw2", "pw3"]
        by_handle, by_order = _parse_passwords(args)
        assert by_handle == {"A": "pw2"}
        assert by_order == ["pw1", "pw3"]

    def test_assign_passwords(self):
        # 3 inputs, 2 handles
        num_inputs = 3
        handles = {"A": 1, "B": 2}

        # Handle priority
        by_handle = {"A": "pw_A"}
        by_order = ["pw_1"]
        passwords = _assign_passwords(num_inputs, handles, by_handle, by_order)
        # [pw_1 (order), pw_A (handle), None]
        assert passwords == ["pw_1", "pw_A", None]

        # Order fills Nones
        by_handle = {"A": "pw_A"}
        by_order = ["pw_1", "pw_3"]
        passwords = _assign_passwords(num_inputs, handles, by_handle, by_order)
        # [pw_1 (order), pw_A (handle), pw_3 (order)]
        assert passwords == ["pw_1", "pw_A", "pw_3"]

    @patch("pdftl.cli.parser._assign_passwords")
    @patch("pdftl.cli.parser._parse_passwords")
    @patch("pdftl.cli.parser._handle_pipeline_input")
    @patch("pdftl.cli.parser._parse_file_handles")
    @patch("pdftl.cli.parser._separate_file_and_pw_args")
    def test_parse_pre_operation_args_orchestration(
        self,
        mock_separate,
        mock_parse_files,
        mock_handle_pipe,
        mock_parse_pw,
        mock_assign_pw,
    ):
        """Tests the pre-op orchestrator by mocking its helpers."""
        args = ["in.pdf", "input_pw", "pw1"]
        mock_separate.return_value = (["in.pdf"], ["pw1"])
        mock_parse_files.return_value = (
            ["in.pdf"],
            {"A": 0},
        )  # returns (inputs, handles)
        mock_handle_pipe.return_value = (
            ["in.pdf"],
            {"A": 0},
        )  # returns (inputs, handles)
        mock_parse_pw.return_value = ({}, ["pw1"])  # returns (by_handle, by_order)
        # _assign_passwords returns a single list, no change needed
        mock_assign_pw.return_value = ["pw1"]

        _parse_pre_operation_args(args, is_first_stage=True)

        mock_separate.assert_called_once_with(args)
        mock_parse_files.assert_called_once()
        mock_handle_pipe.assert_called_once()
        mock_parse_pw.assert_called_once()
        mock_assign_pw.assert_called_once()


##################################################


class TestParserIntegration:
    def test_parse_cli_stage_specs_and_options(self, mock_registry, mock_constants):
        """
        Tests parsing a full command with specs, flags, and value keywords.
        """
        # Note: No mocks for private helpers
        args = [
            "in.pdf",
            "cat",
            "1-5",
            "end",  # Specs
            "uncompress",  # Flag keyword
            "output",
            "out.pdf",  # Value keyword
            "allow",
            "Printing",
            "Copying",  # Multi-value keyword
        ]

        stage = parse_cli_stage(args, is_first_stage=True)

        assert stage.operation == "cat"
        assert stage.inputs == ["in.pdf"]
        assert stage.operation_args == ["1-5", "end"]
        assert stage.options == {
            "uncompress": True,
            "output": "out.pdf",
            "allow": {"Printing", "Copying"},
        }

    def test_parse_cli_stage_full_pre_op_parsing(self, mock_registry, mock_constants):
        """
        Tests parsing handles, pipeline input injection, and password assignment.
        """
        args = [
            "A=in1.pdf",
            "in2.pdf",
            "input_pw",
            "A=pw1",
            "pw2",
            "cat",
            "output",
            "out.pdf",
        ]

        # Test as a *second* stage to trigger pipeline injection
        stage = parse_cli_stage(args, is_first_stage=False)

        # 1. Check pipeline injection
        assert stage.inputs == ["_", "in1.pdf", "in2.pdf"]

        # 2. Check handle re-indexing
        assert stage.handles == {"_": 0, "A": 1}

        # 3. Check password assignment (Handle "A" gets pw1, "_" gets pw2)
        assert stage.input_passwords == ["pw2", "pw1", None]

        assert stage.operation == "cat"
        assert stage.options == {"output": "out.pdf"}

    def test_parse_cli_stage_invalid_allow_perm(self, mock_registry, mock_constants):
        args = ["in.pdf", "cat", "allow", "Printing", "BadPermission"]
        with pytest.raises(InvalidArgumentError, match="argument.*BadPermission"):
            parse_cli_stage(args, is_first_stage=True)

    def test_parse_cli_stage_allow_followed_by_keyword(self, mock_registry, mock_constants):
        args = ["in.pdf", "cat", "allow", "output", "out.pdf"]
        p = parse_cli_stage(args, is_first_stage=True)
        assert p.operation == "cat"
        assert p.options["allow"] == set()

    def test_parse_cli_stage_attach_files_not_option(self, mock_registry, mock_constants):
        args = [
            "in.pdf",
            "cat",
            "attach_files",
            "file1.txt",
            "file2.pdf",  # These are for attach_files
            "output",
            "out.pdf",
            "uncompress",  # This is a new keyword
        ]

        stage = parse_cli_stage(args, is_first_stage=True)

        # Check that attach_files not parsed as an output option
        assert "attach_files" not in stage.options
        # Check that the other keywords were also parsed
        assert stage.options["uncompress"] is True
        assert stage.options["output"] == "out.pdf"

    def test_parse_cli_stage_unknown_arg_after_allow_hint(self, mock_registry, mock_constants):
        args = [
            "in.pdf",
            "cat",
            "allow",
            "Printing",
            "bad_arg",  # This is the "unknown argument"
        ]

        with pytest.raises(
            InvalidArgumentError,
            match="Maybe you wanted to give an additional 'allow' permission",
        ):
            parse_cli_stage(args, is_first_stage=True)


# --- merged from test_parser_coverage_2.py ---


def test_missing_multiple_arguments_error():
    """Hits line 62: Missing value for multiple-value option."""
    with pytest.raises(MissingArgumentError, match="Missing value for option 'attach_files'"):
        parser._parse_multiple_arguments("attach_files", ["attach_files"], 0, lambda x: True)


def test_missing_output_value_error():
    """Hits line 149: Missing value for 'output' keyword."""
    with pytest.raises(MissingArgumentError, match="Missing value for keyword: output"):
        parser.parse_options_and_specs(["output"])


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


def test_parse_multiple_args_allow_no_args():
    """Hits cli/parser.py line 67 by ending the command with 'allow'."""
    # Line 149 in parser.py calls _parse_allow_permissions
    # which calls _parse_multiple_arguments with allow_no_args=True.
    # By putting 'allow' at the end of the list, i + 1 >= len(args) becomes True.

    args = ["input.pdf", "allow"]
    specs, options = parse_options_and_specs(args)

    assert specs == ["input.pdf"]
    assert "allow" in options
    assert options["allow"] == set()  # Should be an empty set


def test_duplicate_argument_raises_error():
    """
    Test that providing the same keyword argument twice throws a DuplicateArgumentError.

    We use 'owner_pw' (Owner Password) here because it is a value-taking option.
    Flags (like 'compress') do not currently trigger this error if repeated.
    """
    args = [
        "output",
        "out.pdf",
        "owner_pw",
        "secret123",
        "allow",
        "owner_pw",
        "overwrite_attempt",
    ]

    with pytest.raises(DuplicateArgumentError) as excinfo:
        _specs, _options = parse_options_and_specs(args)

    # Verify the error message mentions the correct keyword
    assert "Duplicate keyword: owner_pw" in str(excinfo.value)


def test_duplicate_flags_are_accepted_and_deduplicated():
    """
    Verify that flags (which take no value) can be repeated without error,
    or mixed with standard options.
    """
    # 'compress' and 'flatten' are flags. We pass 'flatten' twice.
    # We also use 'owner_pw' to ensure mixing flags and value-options works.
    args = [
        "output",
        "out.pdf",
        "compress",
        "flatten",
        "owner_pw",
        "my_password",
        "flatten",  # Duplicate flag should be harmless/ignored
    ]

    _, options = parse_options_and_specs(args)
    # Check flags are set
    assert options["compress"] is True
    assert options["flatten"] is True

    # Check value option is captured
    assert options["owner_pw"] == "my_password"


def test_parser_coverage_gaps():
    # Line 321: Unclosed sub-pipeline
    # Reaches 'StopIteration' while depth > 0
    with pytest.raises(InvalidArgumentError, match="Unclosed sub-pipeline"):
        list(_recursive_group_pipelines(iter(["JOB", "filter"])))

    # Line 354: Unexpected SUB_END (DONE)
    # Finds DONE when depth is 0
    with pytest.raises(InvalidArgumentError, match="Unexpected 'DONE' found"):
        split_args_by_separator(["filter", "DONE"])


def test_parser_implicit_cat_coverage():
    from pdftl.cli.parser import parse_cli_stage

    # Line 286-287: Provide > 1 input with no operation keyword
    # is_first_stage=True prevents it from trying to prepending '_'
    stage = parse_cli_stage(["file1.pdf", "file2.pdf"], is_first_stage=True)

    assert stage.operation == "cat"


# --- Coverage for extracted / previously-untested branches ---


def test_find_operation_and_split_skips_non_str_token(mock_registry):
    """Line 46: a non-str token (e.g. an already-parsed sub-pipeline
    object) is skipped while searching for the operation keyword."""
    non_str = object()
    args = [non_str, "cat", "output"]
    op, pre, post = _find_operation_and_split(args)
    assert op == "cat"
    assert pre == [non_str]
    assert post == ["output"]


def test_parse_options_and_specs_non_str_arg(mock_constants):
    """Lines 136-138: a non-str arg (e.g. an inline sub-pipeline object)
    is appended directly to specs."""
    non_str = object()
    specs, options = parse_options_and_specs([non_str, "1-end"])
    assert specs == [non_str, "1-end"]
    assert options == {}


def test_parse_file_handles_non_str_with_handle_name():
    """Lines 188-190: a non-str arg carrying a handle_name attribute is
    registered as a handle."""

    class FakeSubPipeline:
        handle_name = "B"

    fake = FakeSubPipeline()
    inputs, handles = _parse_file_handles(["in1.pdf", fake])
    assert inputs == ["in1.pdf", fake]
    assert handles == {"B": 1}


def test_parse_file_handles_non_str_without_handle_name():
    """Lines 188-190: a non-str arg with no handle_name is appended
    without registering a handle."""

    class FakeSubPipeline:
        handle_name = None

    fake = FakeSubPipeline()
    inputs, handles = _parse_file_handles([fake])
    assert inputs == [fake]
    assert handles == {}


def test_handle_pipeline_input_underscore_already_present():
    """Line 200->203: when '_' is already a registered handle, the
    suppress(ValueError) block is skipped entirely."""
    inputs, handles = _handle_pipeline_input(["_", "in.pdf"], {"_": 0}, False)
    assert inputs == ["_", "in.pdf"]
    assert handles == {"_": 0}


def test_recursive_group_pipelines_inline_sub_pipeline(mock_registry):
    """Lines 310-323, 359, 367->exit: a real JOB...DONE inline sub-pipeline
    is parsed into an InlineSubPipeline, and closing it hits the normal
    (non-raising) return path of _validate_final_depth."""
    from pdftl.cli.pipeline import InlineSubPipeline

    args = [SUB_START, "in.pdf", "cat", SUB_END, "output", "out.pdf"]
    grouped = _recursive_group_pipelines(iter(args))
    assert isinstance(grouped[0], InlineSubPipeline)
    assert grouped[1:] == ["output", "out.pdf"]


def test_recursive_group_pipelines_named_inline_sub_pipeline(mock_registry):
    """Lines 310-323: a named 'B=JOB...DONE' sub-pipeline sets handle_name."""
    token = "B=" + SUB_START
    args = [token, "in.pdf", "cat", SUB_END]
    grouped = _recursive_group_pipelines(iter(args))
    assert grouped[0].handle_name == "B"


def test_recursive_group_pipelines_each_sub_pipeline(mock_registry):
    """Lines 329-334, 356: a real EACH...DONE sub-pipeline is parsed into
    an EachSubPipeline."""
    from pdftl.cli.pipeline import EachSubPipeline

    args = [SUB_EACH, "in.pdf", "cat", SUB_END]
    grouped = _recursive_group_pipelines(iter(args))
    assert isinstance(grouped[0], EachSubPipeline)
