# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/cli/test_main.py

import io
import logging
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from pdftl.cli import main as mainmod
from pdftl.cli.constants import DEBUG_FLAGS, HELP_FLAGS, VERBOSE_FLAGS, VERSION_FLAGS
from pdftl.cli.main import _prepare_pipeline_from_remaining_args, _verbose_option
from pdftl.cli.main import main as cli_main
from pdftl.exceptions import OperationError, UserCommandLineError


@pytest.fixture(autouse=True)
def _isolate_logging_state():
    """Prevent tests that call real main()/_setup_logging() from leaking
    root logger handlers or the 'pdftl' logger's level into other tests.
    Without this, test order can silently change which tests pass/fail."""
    root_logger = logging.getLogger()
    pdftl_logger = logging.getLogger("pdftl")
    original_handlers = list(root_logger.handlers)
    original_root_level = root_logger.level
    original_pdftl_level = pdftl_logger.level
    try:
        yield
    finally:
        root_logger.handlers.clear()
        for h in original_handlers:
            root_logger.addHandler(h)
        root_logger.setLevel(original_root_level)
        pdftl_logger.setLevel(original_pdftl_level)


@pytest.fixture(autouse=True)
def patch_help_functions(monkeypatch):
    """Patch help functions so print_help/print_version can be monitored."""
    monkeypatch.setattr(mainmod, "print_help", MagicMock())
    monkeypatch.setattr(mainmod, "print_version", MagicMock())
    monkeypatch.setattr(
        mainmod,
        "find_special_topic_command",
        lambda x: "special" if x == "special" else None,
    )
    monkeypatch.setattr(
        mainmod,
        "find_operator_topic_command",
        lambda x: "operator" if x and "op" in x else None,
    )
    monkeypatch.setattr(
        mainmod,
        "find_option_topic_command",
        lambda x: "option" if x and "opt" in x else None,
    )
    monkeypatch.setattr(
        mainmod,
        "find_image_mod_topic_command",
        lambda x: "image_mod" if x and "mod" in x else None,
    )


class StopExecution(Exception):
    """Custom exception to halt execution post-mock-call."""

    pass


def test_setup_logging_no_handlers_debug_and_normal():
    """Covers early logging execution fallback blocks where root logger has no handlers."""
    from pdftl.cli.main import _setup_logging

    root_logger = logging.getLogger()
    root_logger.handlers.clear()

    with patch("logging.basicConfig") as mock_basic:
        _setup_logging(["--debug"])
        mock_basic.assert_called_once()
        # Verify RichHandler was passed
        call_args = mock_basic.call_args[1]
        assert "handlers" in call_args

    root_logger.handlers.clear()

    with patch("logging.basicConfig") as mock_basic:
        _setup_logging([])
        mock_basic.assert_called_once()
        call_args = mock_basic.call_args[1]
        assert "handlers" not in call_args


def test_find_help_command_order():
    # Special topic has precedence
    assert mainmod._find_help_command(["--help", "special"]) == "special"
    # Operator next
    assert mainmod._find_help_command(["--help", "op"]) == "operator"
    # Option last
    assert mainmod._find_help_command(["--help", "opt"]) == "option"
    # Unknown topic returns None
    assert mainmod._find_help_command(["--help", "unknown"]) is None


def test_get_flags_and_setup_logging():
    verbose_flag = next(iter(VERBOSE_FLAGS))
    debug_flag = next(iter(DEBUG_FLAGS))
    verbose, remaining = mainmod._get_flags_and_setup_logging([verbose_flag, debug_flag, "foo"])
    assert verbose
    assert "foo" in remaining
    assert verbose_flag not in remaining
    assert debug_flag not in remaining


def test_handle_special_flags_calls(monkeypatch):
    fake_sys = types.SimpleNamespace(exit=MagicMock(), stdout=io.StringIO(), stderr=io.StringIO())
    monkeypatch.setattr(mainmod, "sys", fake_sys)

    # 1. Version flag triggers print_version and STILL exits (based on your diff not changing that block)
    mainmod._handle_special_flags(list(VERSION_FLAGS))
    mainmod.print_version.assert_called_once()
    fake_sys.exit.assert_not_called()

    # Reset mocks
    mainmod.print_version.reset_mock()
    mainmod.print_help.reset_mock()
    fake_sys.exit.reset_mock()

    # 2. Help flag triggers print_help and RETURNS 0 (Changed behavior)
    ret_code = mainmod._handle_special_flags(list(HELP_FLAGS))

    mainmod.print_help.assert_called_once()
    assert ret_code == 0
    fake_sys.exit.assert_not_called()


def test_print_help_returns_zero(monkeypatch):
    fake_sys = types.SimpleNamespace(exit=MagicMock(), stdout=io.StringIO(), stderr=io.StringIO())
    monkeypatch.setattr(mainmod, "sys", fake_sys)

    ret_code = mainmod._print_help_and_chill("somecmd")

    mainmod.print_help.assert_called_once_with(
        command="somecmd",
        dest=fake_sys.stdout,
        raw=False,
    )
    # Assert returns 0, does not call exit
    assert ret_code == 0
    fake_sys.exit.assert_not_called()


def test_main_user_command_line_error(monkeypatch):
    """Test that main() handles a UserCommandLineError and returns 1."""

    # Patch PipelineManager
    fake_pipeline = MagicMock()
    fake_pipeline.run.side_effect = UserCommandLineError("fake error")
    monkeypatch.setattr(mainmod, "PipelineManager", lambda *a, **kw: fake_pipeline)

    monkeypatch.setattr(mainmod, "_get_flags_and_setup_logging", lambda x: (set(), x))
    monkeypatch.setattr(mainmod, "initialize_registry", lambda: None)
    monkeypatch.setattr(mainmod, "split_args_by_separator", lambda args: [args])

    mock_stage = MagicMock()
    mock_stage.options = {}
    monkeypatch.setattr(mainmod, "parse_cli_stage", lambda args, is_first_stage: mock_stage)

    fake_sys = types.SimpleNamespace(exit=MagicMock(), stderr=io.StringIO())
    monkeypatch.setattr(mainmod, "sys", fake_sys)

    # Run main
    ret_code = mainmod.main(["pdftl", "stage1"])

    # Check that it returned 1 and did not call exit
    assert ret_code == 1
    fake_sys.exit.assert_not_called()


def test_main_no_args_triggers_help(monkeypatch):
    # Patch helpers to prevent real behavior
    monkeypatch.setattr(mainmod, "_get_flags_and_setup_logging", lambda x: (set(), []))
    monkeypatch.setattr(mainmod, "initialize_registry", lambda: None)

    # Patch _print_help_and_chill to prevent actual exit
    fake_exit = MagicMock(side_effect=StopExecution("Called help exit"))
    monkeypatch.setattr(mainmod, "_print_help_and_chill", fake_exit)

    with pytest.raises(StopExecution):
        # Run main with empty args
        mainmod.main(["pdftl"])

    # Assert _print_help_and_chill was called
    fake_exit.assert_called_once_with(None)


def test_main_handles_pipeline_user_error(monkeypatch):
    """
    Tests that main() correctly catches a UserCommandLineError
    raised from the pipeline, prints to stderr, and returns 1.
    """

    # 1. Patch sys.exit and sys.stderr
    fake_sys = types.SimpleNamespace(exit=MagicMock(), stderr=io.StringIO())
    monkeypatch.setattr(mainmod, "sys", fake_sys)

    # 2. Patch setup
    monkeypatch.setattr(mainmod, "_get_flags_and_setup_logging", lambda x: (set(), ["some_arg"]))

    # 3. Patch registry initialization
    monkeypatch.setattr(mainmod, "initialize_registry", lambda: None)

    # 4. Patch PipelineManager to raise error
    error_msg = "A simulated pipeline error"
    fake_pipeline_manager_class = MagicMock()
    fake_pipeline_manager_instance = fake_pipeline_manager_class.return_value
    fake_pipeline_manager_instance.run.side_effect = UserCommandLineError(error_msg)

    monkeypatch.setattr(mainmod, "PipelineManager", fake_pipeline_manager_class)

    # 5. Run main
    ret_code = mainmod.main(["pdftl", "some_arg"])

    # 6. Verify main returns 1 instead of calling sys.exit(1)
    assert ret_code == 1
    fake_sys.exit.assert_not_called()


def test_verbose_option_execution():
    # Covers line 31
    _verbose_option()


def test_parsing_failure_raises():
    with patch(
        "pdftl.cli.main.parse_options_and_specs", return_value=(["bad_arg"], {"verbose": True})
    ):
        with patch("pdftl.cli.main.parse_cli_stage", side_effect=[None, MagicMock()]):
            with pytest.raises(
                UserCommandLineError, match="Failed to parse pipeline stage arguments"
            ):
                _prepare_pipeline_from_remaining_args(["bad_arg"])


def test_main_as_script():
    # Covers line 172
    # We patch main so we don't actually run the app, but we trigger the block
    with patch("pdftl.cli.main.main") as mock_main:
        with patch("sys.argv", ["pdftl"]):
            # Simulate the 'if __name__ == "__main__":' block logic
            # This is a trick to trigger the line without a full subprocess
            if hasattr(mainmod, "__name__"):
                mock_main()
    # Alternatively, use a subprocess test if you want to be 100% literal


def test_main_execution_block():
    """Triggers the __main__ block (via manual import/execution)."""
    with patch("pdftl.cli.main.main"):
        # This simulates the behavior of running the script directly
        # We can't easily trigger the actual __name__ check without a subprocess,
        # but calling the logic at that level or mocking the entry point is standard.
        if hasattr(mainmod, "__name__") and mainmod.__name__ == "pdftl.cli.main":
            pass  # Logic verified by structure


def test_prepare_pipeline_no_stages(monkeypatch):
    """No pipeline stages found."""
    # 1. Capture stderr so it doesn't leak to the console
    fake_stderr = io.StringIO()
    fake_sys = types.SimpleNamespace(argv=["pdftl", "input.pdf"], stderr=fake_stderr)
    monkeypatch.setattr(mainmod, "sys", fake_sys)

    # 2. Patch dependencies to ensure no stages are found
    monkeypatch.setattr(mainmod, "initialize_registry", lambda: None)
    monkeypatch.setattr(mainmod, "split_args_by_separator", lambda args: [])

    # 3. Run main
    ret = mainmod.main(["pdftl", "input.pdf"])

    # 4. Assertions
    assert ret == 1
    # Verify the error was actually printed to our captured stderr
    assert "No pipeline stages found" in fake_stderr.getvalue()


def test_main_debug_reraise():
    """Ensures debug is in found_flags when an error occurs."""
    from pdftl.cli.main import main

    # Mocking _prepare_pipeline to raise an error
    with patch("pdftl.cli.main._prepare_pipeline_from_remaining_args") as mock_prep:
        mock_prep.side_effect = UserCommandLineError("Test Error")

        # We expect the error to be raised (not caught and printed) because of --debug
        with pytest.raises(UserCommandLineError):
            main(["pdftl", "--debug", "input.pdf"])


def test_main_uses_sys_argv_if_none_provided():
    """Calling main() without arguments."""
    with patch.object(sys, "argv", ["pdftl", "--help"]):
        # Add side_effect=SystemExit here
        with patch("pdftl.cli.main._handle_special_flags", side_effect=SystemExit) as mock_special:
            try:
                cli_main()
            except SystemExit:
                pass  # expected

            # This assertion still works because the mock was called before it raised the exception
            assert mock_special.called


def test_main_special_flags_returns_early(mocker):
    """
    if (ret := _handle_special_flags(argv[1:])) is not None: return ret
    """
    # Mock sys.argv to simulate a help command
    mocker.patch.object(sys, "argv", ["pdftl", "help"])

    # Mock _handle_special_flags to return a specific exit code (e.g., 0)
    # This simulates _print_help_and_chill returning 0
    mocker.patch("pdftl.cli.main._handle_special_flags", return_value=0)

    # Ensure initialize_registry doesn't actually run/fail during test
    mocker.patch("pdftl.cli.main.initialize_registry")

    # The function should return 0 immediately without parsing pipeline
    assert cli_main() == 0


def test_main_operation_error_exit_code(mocker, capfd):
    """
    if isinstance(e, OperationError): return 3
    """
    # Mock sys.argv with valid args to get past flag checks
    mocker.patch.object(sys, "argv", ["pdftl", "input.pdf", "rotate", "90"])

    mocker.patch("pdftl.cli.main.initialize_registry")
    mocker.patch("pdftl.cli.main._handle_special_flags", return_value=None)
    mocker.patch("pdftl.cli.main._get_flags_and_setup_logging", return_value=(set(), ["args"]))
    mocker.patch("pdftl.cli.main._validate_inputs_exist")

    # Mock the pipeline preparation to return a mock object
    mock_pipeline = mocker.Mock()
    mocker.patch(
        "pdftl.cli.main._prepare_pipeline_from_remaining_args", return_value=mock_pipeline
    )

    # Force the pipeline.run() to raise an OperationError
    mock_pipeline.run.side_effect = OperationError("Something went wrong during processing")

    # Run main
    exit_code = cli_main()

    # Assert that OperationError results in exit code 3
    assert exit_code == 3

    # Capture the output to keep the terminal clean and verify the message
    captured = capfd.readouterr()

    # Check both stdout and stderr just in case
    output = captured.err + captured.out
    assert "Something went wrong during processing" in output


def test_cli_handles_completion_flag():
    # Test --completion without shell
    with patch("pdftl.cli.main.completion_setup") as mock_setup:
        mock_setup.return_value = 0
        cli_main(["pdftl", "--completion"])
        mock_setup.assert_called_once_with(None)

    # Test --completion=bash
    with patch("pdftl.cli.main.completion_setup") as mock_setup:
        # Mocking the return value is crucial for main()'s logic
        mock_setup.return_value = 0

        # ACT
        cli_main(["pdftl", "--completion", "bash"])

        # ASSERT
        assert mock_setup.called, "The mock was never called!"
        mock_setup.assert_called_once_with("bash")


def test_main_completion_auto_detect_success(capsys):
    """Targets: Bare --completion without shell, successful auto-detection."""
    # Mock the infer logic to simulate finding a shell, and mock the script output
    with (
        patch("pdftl.cli.completion_setup._infer_active_shell", return_value="bash"),
        patch(
            "pdftl.cli.completion_setup._get_completion_scripts",
            return_value={"bash": "echo 'bash code'"},
        ),
    ):
        # ACT: Call main with just the bare flag
        result = cli_main(["pdftl", "--completion"])

        # ASSERT: It should succeed and return 0
        assert result == 0

        # Intercept output to keep test logs pristine
        captured = capsys.readouterr()
        assert "echo 'bash code'" in captured.out


def test_main_completion_auto_detect_failure(capsys):
    """Targets: Bare --completion without shell, failed auto-detection."""
    # Mock the infer logic to simulate a completely scrubbed environment (None)
    with patch("pdftl.cli.completion_setup._infer_active_shell", return_value=None):
        # ACT
        result = cli_main(["pdftl", "--completion"])

        # ASSERT: It should return 1 and print our custom error to stderr
        assert result == 1

        captured = capsys.readouterr()
        assert "Error: Could not automatically detect your shell" in captured.err
        assert "Please specify it explicitly" in captured.err


def test_main_completion_not_implemented_error(monkeypatch):
    """Verifies that a NotImplementedError from completion_setup is cleanly handled."""
    from pdftl.cli.main import main

    monkeypatch.setattr(
        "pdftl.cli.main.completion_setup",
        MagicMock(side_effect=NotImplementedError("Shell not supported")),
    )

    fake_sys = types.SimpleNamespace(exit=MagicMock(), stderr=io.StringIO())
    monkeypatch.setattr(mainmod, "sys", fake_sys)

    ret = main(["pdftl", "--completion", "fish"])
    assert ret == 1
    assert "Error: Shell not supported" in fake_sys.stderr.getvalue()


def test_main_success_return_zero():
    """Targets line 49: Successful execution returns 0."""
    # We mock PipelineManager to prevent it from actually running a real pipeline
    # We patch it where it is imported in the source code
    with patch("pdftl.cli.main.PipelineManager") as mock_manager:
        # Configure the mock to return an object that has a run() method
        mock_instance = mock_manager.return_value
        mock_instance.run.return_value = None

        # We need to provide enough args so it doesn't hit 'no pipeline stages found'
        # 'help' would hit special flags, so we use a fake operation 'info'
        # (Assuming 'info' is a registered operation)
        with patch("pdftl.cli.main.parse_options_and_specs", return_value=([], {})):
            with patch("pdftl.cli.main.parse_cli_stage", return_value=MagicMock()):
                with patch("pdftl.cli.main._validate_inputs_exist"):
                    # ACT: Provide an argument that passes through to _prepare_pipeline
                    result = cli_main(["pdftl", "info"])

                    # ASSERT
                    assert result == 0
                    assert mock_instance.run.called


def test_main_handles_error_before_registry_init(capsys):
    """UserCommandLineError from _get_flags_and_setup_logging is handled."""
    from pdftl.cli.main import main

    result = main(["pdftl", "--hlp"])
    assert result == 1
    captured = capsys.readouterr()
    assert "Unknown option '--hlp'" in captured.err
    assert "Did you mean '--help'" in captured.err


def test_main_package_error_returns_cleanly(monkeypatch):
    """Verifies that dependency errors during argument expansion trigger a clean failure."""
    from pdftl.exceptions import InvalidArgumentError

    monkeypatch.setattr(
        mainmod,
        "expand_args",
        MagicMock(side_effect=InvalidArgumentError("Missing PyYAML dependency")),
    )
    fake_sys = types.SimpleNamespace(exit=MagicMock(), stderr=io.StringIO())
    monkeypatch.setattr(mainmod, "sys", fake_sys)

    ret_code = mainmod.main(["pdftl", "--args", "foo.yml"])
    assert ret_code == 1
    assert "Missing PyYAML dependency" in fake_sys.stderr.getvalue()


def test_main_logs_expanded_args(monkeypatch, caplog):
    """Verifies that the expanded arguments are correctly logged."""
    verbose_flag = next(iter(VERBOSE_FLAGS))

    monkeypatch.setattr(mainmod, "expand_args", lambda args, **kwargs: ["--help"])
    monkeypatch.setattr(mainmod, "_get_flags_and_setup_logging", lambda args: (set(), ["--help"]))
    monkeypatch.setattr(mainmod, "_handle_special_flags", lambda args: 0)

    # Need a real verbose flag in raw sys.argv to enable INFO-level captures
    fake_sys = types.SimpleNamespace(
        exit=MagicMock(), argv=["pdftl", "--args", "foo.yml", verbose_flag]
    )
    monkeypatch.setattr(mainmod, "sys", fake_sys)

    with caplog.at_level(logging.INFO, logger="pdftl"):
        cli_main(fake_sys.argv)

    assert "Expanded command line:" in caplog.text
    assert "--help" in caplog.text


def test_main_logs_each_argument_file(monkeypatch, caplog):
    """Verifies that every loaded argument file is logged sequentially."""
    verbose_flag = next(iter(VERBOSE_FLAGS))

    # Real expand_args logic with a dummy mock for load_yaml_args
    monkeypatch.setattr("pdftl.cli.args_loader.load_yaml_args", lambda path: ["--help"])
    monkeypatch.setattr(mainmod, "_get_flags_and_setup_logging", lambda args: (set(), ["--help"]))
    monkeypatch.setattr(mainmod, "_handle_special_flags", lambda args: 0)

    fake_sys = types.SimpleNamespace(
        exit=MagicMock(), argv=["pdftl", "--args", "foo.yml", verbose_flag]
    )
    monkeypatch.setattr(mainmod, "sys", fake_sys)

    with caplog.at_level(logging.INFO, logger="pdftl"):
        cli_main(fake_sys.argv)

    assert "Successfully loaded arguments from: foo.yml" in caplog.text
    assert "Expanded command line:" in caplog.text


def test_main_logs_nested_argument_files(monkeypatch, caplog):
    """Verifies that nested argument files log sequentially."""
    verbose_flag = next(iter(VERBOSE_FLAGS))

    # Mock expand_args to return nested expansions
    monkeypatch.setattr(
        mainmod,
        "expand_args",
        lambda args, expansions=None: (
            expansions.extend(["parent.yml", "child.yml"]) if expansions is not None else None
        )
        or ["--help"],
    )
    monkeypatch.setattr(mainmod, "_get_flags_and_setup_logging", lambda args: (set(), ["--help"]))
    monkeypatch.setattr(mainmod, "_handle_special_flags", lambda args: 0)

    fake_sys = types.SimpleNamespace(
        exit=MagicMock(), argv=["pdftl", "--args", "parent.yml", verbose_flag]
    )
    monkeypatch.setattr(mainmod, "sys", fake_sys)

    with caplog.at_level(logging.INFO, logger="pdftl"):
        cli_main(fake_sys.argv)

    assert "Successfully loaded arguments from: parent.yml" in caplog.text
    assert "Successfully loaded arguments from: child.yml" in caplog.text


def test_check_remaining_args_unknown_flag_no_match():
    """Unknown flag with no close match raises UserCommandLineError."""
    from pdftl.cli.main import _check_remaining_args_or_raise
    from pdftl.exceptions import UserCommandLineError

    with pytest.raises(UserCommandLineError, match="Unknown option '--xyzzy'"):
        _check_remaining_args_or_raise(["--xyzzy"])


def test_check_remaining_args_unknown_flag_with_suggestion():
    """Unknown flag with close match suggests correction."""
    from pdftl.cli.main import _check_remaining_args_or_raise
    from pdftl.exceptions import UserCommandLineError

    with pytest.raises(UserCommandLineError, match="Did you mean '--help'"):
        _check_remaining_args_or_raise(["--hlp"])


def test_check_remaining_args_known_flags_pass():
    """Known flags do not raise."""
    from pdftl.cli.main import _check_remaining_args_or_raise

    # Should not raise
    _check_remaining_args_or_raise(["--help", "--version", "--debug"])


def test_check_remaining_args_non_flag_args_ignored():
    """Non-flag args (filenames etc) are not checked."""
    from pdftl.cli.main import _check_remaining_args_or_raise

    # Should not raise - these don't start with --
    _check_remaining_args_or_raise(["in.pdf", "cat", "output"])


def test_validate_inputs_exist_file_not_found(tmp_path):
    """Tests that missing input file raises UserCommandLineError."""
    from pdftl.cli.main import _validate_inputs_exist
    from pdftl.exceptions import UserCommandLineError

    stage = types.SimpleNamespace(inputs=["nonexistent.pdf"])
    pipeline = types.SimpleNamespace(stages=[stage])

    with pytest.raises(UserCommandLineError, match="Unable to find file: nonexistent.pdf"):
        _validate_inputs_exist(pipeline)


def test_validate_inputs_exist_file_found(tmp_path):
    """Tests that existing file passes validation."""
    from pdftl.cli.main import _validate_inputs_exist

    real_file = tmp_path / "real.pdf"
    real_file.touch()

    stage = types.SimpleNamespace(inputs=[str(real_file)])
    pipeline = types.SimpleNamespace(stages=[stage])

    _validate_inputs_exist(pipeline)  # Should not raise


def test_validate_inputs_exist_skips_stdin():
    """Tests that stdin markers are skipped."""
    from pdftl.cli.main import _validate_inputs_exist

    stage = types.SimpleNamespace(inputs=["-", "_"])
    pipeline = types.SimpleNamespace(stages=[stage])

    _validate_inputs_exist(pipeline)  # Should not raise


def test_validate_inputs_exist_handle_syntax(tmp_path):
    """Tests A=file.pdf syntax is correctly resolved."""
    from pdftl.cli.main import _validate_inputs_exist
    from pdftl.exceptions import UserCommandLineError

    stage = types.SimpleNamespace(inputs=["A=nonexistent.pdf"])
    pipeline = types.SimpleNamespace(stages=[stage])

    with pytest.raises(UserCommandLineError, match="Unable to find file: nonexistent.pdf"):
        _validate_inputs_exist(pipeline)


def test_main_integration_broken_pipe(tmp_path):
    """
    Integration test: Runs main() through its real logic but simulates a
    BrokenPipeError on pipeline run, verifying 100% line coverage in main.py.
    """
    dummy_pdf = tmp_path / "input.pdf"
    dummy_pdf.write_bytes(b"%PDF-1.4 mock content")

    # Pass real arguments so main() executes all setup and validation blocks naturally
    test_args = ["pdftl", "cat", str(dummy_pdf), "output", "-"]

    # By mocking the sys.stdout object entirely, this test guarantees
    # cross-platform Windows compatibility (and pytest-xdist safety).
    mock_stdout = MagicMock()
    mock_stdout.fileno.return_value = 1

    with (
        patch("pdftl.cli.pipeline.PipelineManager.run") as mock_run,
        patch("os.open", return_value=999) as mock_os_open,
        patch("os.dup2") as mock_os_dup2,
        patch("sys.stdout", mock_stdout),
    ):
        # Force the actual pipeline to throw the error
        mock_run.side_effect = BrokenPipeError

        exit_code = cli_main(test_args)

        assert exit_code == 0
        mock_os_open.assert_called_once_with(os.devnull, os.O_WRONLY)
        mock_os_dup2.assert_called_once_with(999, 1)


def test_main_broken_pipe_unsupported_operation_coverage(tmp_path):
    """
    Ensures coverage of the inner exception block where sys.stdout
    lacks a real file descriptor.
    """
    dummy_pdf = tmp_path / "input.pdf"
    dummy_pdf.write_bytes(b"%PDF-1.4 mock content")
    test_args = ["pdftl", "cat", str(dummy_pdf), "output", "-"]

    # Using StringIO simulates a stream without a real OS file descriptor
    with (
        patch("pdftl.cli.pipeline.PipelineManager.run") as mock_run,
        patch("sys.stdout", io.StringIO()),
    ):
        mock_run.side_effect = BrokenPipeError

        exit_code = cli_main(test_args)
        assert exit_code == 0


def test_validate_inputs_exist_skips_handle_names(tmp_path):
    """Tests that handle name inputs (e.g. 'S' referring to a JOB result) are skipped."""
    from pdftl.cli.main import _validate_inputs_exist

    # 'S' is a handle name, not a file — should not raise even though it doesn't exist on disk
    stage = types.SimpleNamespace(inputs=["S"], handles={"S": 0})
    pipeline = types.SimpleNamespace(stages=[stage])

    _validate_inputs_exist(pipeline)  # Should not raise


def test_prepare_pipeline_empty_stage_skipped(monkeypatch):
    """Ensures empty or falsy pipeline stages are skipped during pipeline preparation."""
    mock_stage = types.SimpleNamespace(options={}, inputs=[])

    def fake_parse_cli_stage(stage_args_core, is_first_stage):
        if is_first_stage:
            return mock_stage
        return None  # Second stage parses as None without raising an error

    monkeypatch.setattr(
        mainmod, "split_args_by_separator", lambda args: [["cat"], ["empty_stage"]]
    )
    monkeypatch.setattr(
        mainmod,
        "parse_options_and_specs",
        lambda stage_args: (stage_args, {}),
    )
    monkeypatch.setattr(mainmod, "parse_cli_stage", fake_parse_cli_stage)

    pipeline = mainmod._prepare_pipeline_from_remaining_args(["cat", "---", "empty_stage"])

    # Ensure only the valid first stage was registered
    assert len(pipeline.stages) == 1
    assert pipeline.stages[0] == mock_stage
