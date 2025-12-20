import io
import types
from unittest.mock import MagicMock

import pytest

from pdftl.cli import help as helpmod
from pdftl.cli import main as mainmod
from pdftl.cli.constants import DEBUG_FLAGS, HELP_FLAGS, VERBOSE_FLAGS, VERSION_FLAGS
from pdftl.exceptions import UserCommandLineError


@pytest.fixture(autouse=True)
def patch_main_help_functions(monkeypatch):
    """Patch the functions actually used by main.py."""
    monkeypatch.setattr("pdftl.cli.help.print_help", MagicMock())
    monkeypatch.setattr("pdftl.cli.help.print_version", MagicMock())
    monkeypatch.setattr(
        "pdftl.cli.help.find_special_topic_command",
        lambda x: "special" if x == "special" else None,
    )
    monkeypatch.setattr(
        "pdftl.cli.help.find_operator_topic_command",
        lambda x: "operator" if "op" in x else None,
    )
    monkeypatch.setattr(
        "pdftl.cli.help.find_option_topic_command",
        lambda x: "option" if "opt" in x else None,
    )


@pytest.fixture(autouse=True)
def patch_help_functions(monkeypatch):
    """Patch help functions so print_help/print_version can be monitored."""
    monkeypatch.setattr(helpmod, "print_help", MagicMock())
    monkeypatch.setattr(helpmod, "print_version", MagicMock())
    # Patch find_* functions to simple return values for testing _find_help_command
    monkeypatch.setattr(
        helpmod,
        "find_special_topic_command",
        lambda x: "special" if x == "special" else None,
    )
    monkeypatch.setattr(
        helpmod,
        "find_operator_topic_command",
        lambda x: "operator" if "op" in x else None,
    )
    monkeypatch.setattr(
        helpmod, "find_option_topic_command", lambda x: "option" if "opt" in x else None
    )


class StopExecution(Exception):
    """Custom exception to halt execution post-mock-call."""

    pass


def test_find_help_command_order():
    # Special topic has precedence
    assert mainmod._find_help_command(["--help", "special"]) == "special"
    # Operator next
    assert mainmod._find_help_command(["--help", "op"]) == "operator"
    # Option last
    assert mainmod._find_help_command(["--help", "opt"]) == "option"
    # Unknown topic returns None
    assert mainmod._find_help_command(["--help", "unknown"]) is None


def test_is_verbose_and_setup_logging():
    verbose_flag = next(iter(VERBOSE_FLAGS))
    debug_flag = next(iter(DEBUG_FLAGS))
    verbose, remaining = mainmod._is_verbose_and_setup_logging([verbose_flag, debug_flag, "foo"])
    assert verbose
    assert "foo" in remaining
    assert verbose_flag not in remaining
    assert debug_flag not in remaining


def test_handle_special_flags_calls(monkeypatch):
    fake_sys = types.SimpleNamespace(exit=MagicMock(), stdout=io.StringIO())
    monkeypatch.setattr(mainmod, "sys", fake_sys)

    # Version flag triggers print_version and exit
    mainmod._handle_special_flags(list(VERSION_FLAGS))
    helpmod.print_version.assert_called_once()
    fake_sys.exit.assert_called_once_with(0)

    # Reset mocks
    helpmod.print_version.reset_mock()
    helpmod.print_help.reset_mock()
    fake_sys.exit.reset_mock()

    # Help flag triggers print_help and exit
    mainmod._handle_special_flags(list(HELP_FLAGS))
    helpmod.print_help.assert_called_once()
    fake_sys.exit.assert_called_once_with(0)


def test_main_no_stages_raises(monkeypatch):
    fake_sys = types.SimpleNamespace(
        argv=["pdftl", "combine"], exit=MagicMock(), stdout=io.StringIO()
    )
    monkeypatch.setattr(mainmod, "sys", fake_sys)

    # Patch parsing to simulate no stages
    monkeypatch.setattr(mainmod, "split_args_by_separator", lambda x: [[]])
    monkeypatch.setattr(mainmod, "parse_options_and_specs", lambda x: ([], {}))
    monkeypatch.setattr(mainmod, "parse_cli_stage", lambda x, is_first_stage=False: None)
    monkeypatch.setattr(mainmod, "initialize_registry", lambda: None)
    monkeypatch.setattr(mainmod, "UserInputContext", lambda *args, **kwargs: MagicMock())
    monkeypatch.setattr(
        mainmod, "PipelineManager", lambda *args, **kwargs: MagicMock(run=lambda: None)
    )

    with pytest.raises(UserCommandLineError):
        mainmod.main()


def test_print_help_exits(monkeypatch):
    fake_sys = types.SimpleNamespace(exit=MagicMock(), stdout=io.StringIO())
    monkeypatch.setattr(mainmod, "sys", fake_sys)

    mainmod._print_help_and_exit("somecmd")
    helpmod.print_help.assert_called_once_with(command="somecmd", dest=fake_sys.stdout)
    fake_sys.exit.assert_called_once_with(0)


def test_main_user_command_line_error(monkeypatch):
    """Test that main() handles a UserCommandLineError and calls sys.exit(1)."""

    # Patch PipelineManager to raise UserCommandLineError when run
    fake_pipeline = MagicMock()
    fake_pipeline.run.side_effect = UserCommandLineError("fake error")
    monkeypatch.setattr(mainmod, "PipelineManager", lambda *a, **kw: fake_pipeline)

    # Patch _is_verbose_and_setup_logging to skip logging setup
    monkeypatch.setattr(mainmod, "_is_verbose_and_setup_logging", lambda x: (False, x))

    # Patch initialize_registry to do nothing
    monkeypatch.setattr(mainmod, "initialize_registry", lambda: None)

    # Patch split_args_by_separator and parse_options_and_specs
    monkeypatch.setattr(mainmod, "split_args_by_separator", lambda args: [args])
    monkeypatch.setattr(mainmod, "parse_options_and_specs", lambda args: (args, {}))

    # Patch parse_cli_stage to return the args as-is
    monkeypatch.setattr(mainmod, "parse_cli_stage", lambda args, is_first_stage: args)

    # Patch sys.exit so it doesn't actually exit
    fake_sys = types.SimpleNamespace(exit=MagicMock(), stderr=io.StringIO())
    monkeypatch.setattr(mainmod, "sys", fake_sys)

    # Run main with dummy args
    mainmod.main(["pdftl", "stage1"])

    # Check that sys.exit(1) was called
    fake_sys.exit.assert_called_once_with(1)

    # Optional: check that the error message was printed to stderr
    output = fake_sys.stderr.getvalue()
    assert "fake error" in output


def test_main_no_args_triggers_help(monkeypatch):
    # Patch helpers to prevent real behavior
    monkeypatch.setattr(mainmod, "_is_verbose_and_setup_logging", lambda x: (False, []))
    monkeypatch.setattr(mainmod, "initialize_registry", lambda: None)

    # Patch _print_help_and_exit to prevent actual exit
    fake_exit = MagicMock(side_effect=StopExecution("Called help exit"))
    monkeypatch.setattr(mainmod, "_print_help_and_exit", fake_exit)

    with pytest.raises(StopExecution):
        # Run main with empty args
        mainmod.main(["pdftl"])

    # Assert _print_help_and_exit was called
    fake_exit.assert_called_once_with(None)


def test_main_handles_pipeline_user_error(monkeypatch):
    """
    Tests that main() correctly catches a UserCommandLineError
    raised from the pipeline, prints to stderr, and exits with 1.
    """

    # 1. Patch sys.exit and sys.stderr to monitor them
    fake_sys = types.SimpleNamespace(exit=MagicMock(), stderr=io.StringIO())
    monkeypatch.setattr(mainmod, "sys", fake_sys)

    # 2. Patch setup to return some generic arguments.
    monkeypatch.setattr(mainmod, "_is_verbose_and_setup_logging", lambda x: (False, ["some_arg"]))

    # 3. Patch registry initialization
    monkeypatch.setattr(mainmod, "initialize_registry", lambda: None)

    # 4. Patch PipelineManager's 'run' method to simulate a failure
    error_msg = "A simulated pipeline error"
    fake_pipeline_manager_class = MagicMock()
    fake_pipeline_manager_instance = fake_pipeline_manager_class.return_value
    fake_pipeline_manager_instance.run.side_effect = UserCommandLineError(error_msg)

    monkeypatch.setattr(mainmod, "PipelineManager", fake_pipeline_manager_class)

    # 5. Run main, which will trigger our simulated error
    mainmod.main(["pdftl", "some_arg"])

    # 6. Verify that main() did its job:

    # It called sys.exit(1)
    fake_sys.exit.assert_called_once_with(1)

    # It printed the *correct* error message to stderr
    stderr_output = fake_sys.stderr.getvalue()
    assert f"Error: {error_msg}" in stderr_output
