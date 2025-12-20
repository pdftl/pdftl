from unittest.mock import MagicMock, patch

import pytest

# --- Import module and functions to test ---
from pdftl.cli import parser as parser_module
from pdftl.cli.parser import (
    _assign_passwords,
    _find_operation_and_split,
    _handle_pipeline_input,
    _parse_allow_permissions,
    _parse_attach_files,
    _parse_file_handles,
    _parse_flag_keyword,
    _parse_multiple_arguments,
    _parse_passwords,
    _parse_pre_operation_args,
    _parse_value_keyword,
    _raise_unknown_arg_error,
    _separate_file_and_pw_args,
    parse_cli_stage,
    parse_options_and_specs,
    split_args_by_separator,
)
from pdftl.cli.pipeline import CliStage

# --- Import Exceptions ---
from pdftl.exceptions import InvalidArgumentError, MissingArgumentError

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

    mocker.patch.object(parser_module, "FLAG_KEYWORDS", mock_flags)
    mocker.patch.object(parser_module, "VALUE_KEYWORDS", mock_values)
    mocker.patch.object(parser_module, "ALLOW_PERMISSIONS", mock_allow)
    mocker.patch.object(parser_module, "ALLOW_PERMISSIONS_L", mock_allow_l)


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
        q = lambda x: x.lower() in ["printing", "copying"]

        consumed, end_pos = _parse_multiple_arguments("allow", args, 0, q)
        assert consumed == 3  # "allow", "Printing", "Copying"
        assert end_pos == 3

    def test_parse_multiple_arguments_error(self):
        args = ["allow", "output"]
        q = lambda x: x.lower() in ["printing", "copying"]

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

    @patch("pdftl.cli.parser._parse_multiple_arguments", return_value=(3, 3))
    def test_parse_attach_files(self, mock_parse_multi, mock_constants):
        options = {}
        args = ["attach_files", "file1.txt", "file2.pdf"]

        consumed, end_pos = _parse_attach_files(args, 0, options)

        assert consumed == 3
        assert end_pos == 3
        assert options == {"attach_files": ["file1.txt", "file2.pdf"]}
        mock_parse_multi.assert_called_once()

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
            "attach_files",
            "f1.txt",
        ]

        specs, options = parse_options_and_specs(args)

        assert specs == ["1-end", "A=2"]  # Specs stop at first keyword
        assert options == {
            "allow": {"Printing"},
            "uncompress": True,
            "output": "out.pdf",
            "attach_files": ["f1.txt"],
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
        with pytest.raises(InvalidArgumentError, match="Invalid argument 'output'"):
            parse_cli_stage(args, is_first_stage=True)

    def test_parse_cli_stage_attach_files_stops_at_keyword(self, mock_registry, mock_constants):
        args = [
            "in.pdf",
            "cat",
            "attach_files",
            "file1.txt",
            "file2.pdf",  # These are for attach_files
            "uncompress",  # This is a new keyword
            "output",
            "out.pdf",
        ]

        stage = parse_cli_stage(args, is_first_stage=True)

        # Check that attach_files ended correctly
        assert stage.options["attach_files"] == ["file1.txt", "file2.pdf"]
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
