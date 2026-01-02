# tests/api/test_api_registry_examples.py
import shlex
from unittest.mock import MagicMock, patch

import pikepdf
import pytest

import pdftl.core.constants as c
from pdftl import api
from pdftl.cli.parser import parse_cli_stage
from pdftl.core.registry import registry


def discover_all_examples():
    """
    Reuses the discovery logic from tests/test_examples.py to find
    every HelpExample in the registry.
    """
    all_examples = []
    # 1. Discover from operations
    for op_name, op_data in registry.operations.items():
        for i, example in enumerate(op_data.examples):
            if "PROMPT" not in example.cmd:
                all_examples.append(pytest.param(op_name, example.cmd, id=f"op-{op_name}-{i}"))

    # 2. Discover from help topics
    for topic_name, topic in registry.help_topics.items():
        for i, example in enumerate(topic.examples):
            all_examples.append(pytest.param(None, example.cmd, id=f"topic-{topic_name}-{i}"))

    # 3. Discover from options
    for opt_name, opt in registry.options.items():
        examples = getattr(opt, "examples", [])
        for i, example in enumerate(examples):
            all_examples.append(pytest.param(None, example.cmd, id=f"opt-{opt_name}-{i}"))

    return all_examples


def translate_cli_to_api_kwargs(operation_name, example_cmd_string):
    """
    Bridges the CLI parser with the API for testing.
    Strips the command prefix and parses the rest into API-ready kwargs.
    """
    stage_strings = example_cmd_string.split(" --- ")

    api_calls = []

    for idx, stage_str in enumerate(stage_strings):
        args = shlex.split(stage_str)
        if idx == 0 and args and args[0] == "pdftl":
            args.pop(0)

        stage = parse_cli_stage(args, is_first_stage=idx == 0)

        api_calls.append(
            {
                "op": stage.operation,
                "kwargs": {
                    c.INPUTS: stage.inputs,
                    c.OPERATION_ARGS: stage.operation_args,
                    c.ALIASES: stage.handles,
                    c.OPTIONS: stage.options,
                },
            }
        )

    return api_calls


# We patch at the EXECUTOR level to avoid triggering command logic (which crashes on mocks)
# We patch OPEN to avoid FileNotFoundError on dummy example paths
@pytest.mark.parametrize("fixed_op_name, command_str", discover_all_examples())
@patch("pikepdf.open")
@patch("pdftl.core.executor.run_operation")
def test_all_registry_examples_via_api(mock_run, mock_open, fixed_op_name, command_str):
    """
    Integration test: Run every HelpExample in the registry through the API.
    Ensures documentation, parser logic, and API signatures are in sync.
    """
    # Setup mocks
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.pages = [MagicMock()] * 10
    mock_open.return_value = mock_pdf
    mock_run.return_value = MagicMock(spec=pikepdf.Pdf)

    # Translate the CLI string into API components
    translations = translate_cli_to_api_kwargs(fixed_op_name, command_str)

    for translation in translations:
        op_to_call = translation["op"]
        api_kwargs = translation["kwargs"]

        if not op_to_call or op_to_call not in registry.operations:
            pytest.skip(f"Could not resolve operation for: {command_str}")

        # Get the dynamically generated API function
        try:
            api_func = getattr(api, op_to_call)
        except AttributeError:
            pytest.fail(
                f"API has no function for operation '{op_to_call}' found in: {command_str}"
            )

        # Call the API function
        # This will trigger api.call -> _normalize_inputs (uses mock_open) -> executor.run_operation (mocked)
        api_func(**api_kwargs)

    # # Verify the call reached the executor with the correct operation name
    # mock_run.assert_called_once()
    # args, _ = mock_run.call_args
    # assert args[0] == op_to_call
