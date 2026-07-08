# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/cli/main.py

"""Main CLI entry point and helper methods"""

import logging
import os
import sys

from pdftl.cli.args_loader import expand_args
from pdftl.cli.completion_setup import completion_setup
from pdftl.cli.constants import (
    COMPLETION_FLAGS,
    DEBUG_FLAGS,
    HELP_FLAGS,
    VERBOSE_FLAGS,
    VERSION_FLAGS,
)
from pdftl.cli.help import (
    TAG_PREFIX,
    find_image_mod_topic_command,
    find_operator_topic_command,
    find_option_topic_command,
    find_special_topic_command,
    print_help,
)
from pdftl.cli.help_version import print_version
from pdftl.cli.parser import (
    parse_cli_stage,
    parse_options_and_specs,
    split_args_by_separator,
)
from pdftl.cli.pipeline import PipelineManager
from pdftl.cli.whoami import ISSUES, WHOAMI
from pdftl.core.registry import register_option
from pdftl.exceptions import OperationError, PackageError, PdftlOutputError, UserCommandLineError
from pdftl.registry_init import initialize_registry
from pdftl.utils.user_input import UserInputContext, get_input

logger = logging.getLogger(__name__)


@register_option("verbose", desc="Turn on verbose output", type="flag")
def _verbose_option():
    pass


def _setup_logging(cli_args):
    """Configure standard logging levels and handlers early based on flags."""
    debug = any(arg in DEBUG_FLAGS for arg in cli_args)
    verbose = debug or any(arg in VERBOSE_FLAGS for arg in cli_args)
    level = logging.DEBUG if debug else logging.INFO if verbose else logging.WARN

    if debug:
        log_format = "[%(levelname)s]%(filename)s:%(funcName)s:%(lineno)d: %(message)s"
    else:
        log_format = f"[{WHOAMI}] %(message)s"

    # Only configure basicConfig if no handlers exist. This prevents duplicate
    # formatting in production and respects pytest's captured log handlers in testing.
    if not logging.getLogger().hasHandlers():
        if debug:
            from rich.logging import RichHandler

            logging.basicConfig(level=logging.WARN, format=log_format, handlers=[RichHandler()])
        else:
            logging.basicConfig(level=logging.WARN, format=log_format)

    # Allow logs to propagate naturally to the root logger where they are handled
    logging.getLogger("pdftl").setLevel(level)


def main(argv=None):
    """Main entry point for the command-line interface."""
    if argv is None:
        argv = sys.argv

    raw_args = argv[1:]

    # Configure logging early based on raw arguments so parser and pre-processor
    # logs (such as recursive expansions) are visible.
    _setup_logging(raw_args)

    try:
        expansions = []
        expanded_args = expand_args(raw_args, expansions=expansions)
        found_flags, args_for_parsing = _get_flags_and_setup_logging(expanded_args)

        if expansions:
            for item in expansions:
                logger.info("Successfully loaded arguments from: %s", item)

        if "--args" in raw_args:
            import shlex

            expanded_cmd = " ".join(shlex.quote(arg) for arg in expanded_args)
            logger.info("Expanded command line: %s %s", WHOAMI, expanded_cmd)

    except (UserCommandLineError, PackageError) as e:
        debug = any(arg in DEBUG_FLAGS for arg in raw_args)
        return _handle_error_from_main(e, debug)

    initialize_registry()
    if (ret := _handle_special_flags(expanded_args)) is not None:
        return ret

    if not args_for_parsing:
        return _print_help_and_chill(None)

    try:
        pipeline = _prepare_pipeline_from_remaining_args(args_for_parsing)
        _validate_inputs_exist(pipeline)
        pipeline.run()
        # Flush stdout permanently so any BrokenPipeErrors
        # happen safely inside this try/except block.
        sys.stdout.flush()
        return 0

    except BrokenPipeError:
        # Centralized handling for closed pipes (e.g., 'pdftl ... | head')
        # Redirect stdout's file descriptor to devnull to shield the terminal from
        # secondary interpreter-level "Exception ignored in flush" error dumps on exit.
        import io

        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
            os.close(devnull)
        except (OSError, AttributeError, io.UnsupportedOperation):
            # ignore OS-level fd failures, mocked stdout, or memory streams
            pass
        return 0

    except (UserCommandLineError, PackageError, OperationError, PdftlOutputError) as e:
        return _handle_error_from_main(e, "debug" in found_flags)


def _validate_inputs_exist(pipeline):
    """Check input files exist before running the pipeline."""

    for stage in pipeline.stages:
        for filespec in stage.inputs:
            _validate_single_input(filespec, stage)


def _validate_single_input(filespec, stage):
    if not isinstance(filespec, str) or filespec in ("-", "_"):
        return
    if filespec in getattr(stage, "handles", {}):
        return
    if "=" in filespec:
        _, filespec = filespec.split("=", 1)
    if not os.path.exists(filespec):
        raise UserCommandLineError(f"Unable to find file: {filespec}")


def _handle_error_from_main(e, debug):
    if debug:
        raise e
    print(f"[{WHOAMI}] Error: {e}", file=sys.stderr)
    logger.debug("A user command line error occurred", exc_info=True)
    if isinstance(e, OperationError):
        print(
            f"If this looks like a bug, please report it at\n{ISSUES}\n"
            "Include the command you ran and the pdftl --version output.",
            file=sys.stderr,
        )
        return 3
    return 1


##################################################


def _prepare_pipeline_from_remaining_args(args_for_parsing):
    import getpass

    logger.debug("args_for_parsing=%s", args_for_parsing)
    stages_args = split_args_by_separator(args_for_parsing)
    logger.debug("stages_args=%s", stages_args)

    parsed_stages = []
    for i, stage_args in enumerate(stages_args):
        # We parse every stage independently.
        # If a stage contains only options (e.g., 'output out.pdf'), parse_cli_stage
        # will return a stage with no explicit operation args, which the system
        # treats as an implicit 'filter' stage (or purely for saving), as designed.
        stage_args_core, stage_options = parse_options_and_specs(stage_args)
        stage = parse_cli_stage(stage_args_core, is_first_stage=i == 0)
        if stage_options and not stage:
            raise UserCommandLineError(
                f"Failed to parse pipeline stage arguments: {stage_args_core}"
            )
        if stage:
            stage.options.update(stage_options)
            parsed_stages.append(stage)

    if not parsed_stages:
        raise UserCommandLineError(
            "No pipeline stages found.\n Did you forget an operation?  Hint: pdftl help operations"
        )

    input_context = UserInputContext(get_input=get_input, get_pass=getpass.getpass)
    # We no longer pass global_options; all options are encapsulated within their specific stages.
    return PipelineManager(parsed_stages, input_context)


def _print_help_and_chill(command, raw=False):
    """Prints the relevant help topic and exits the program."""
    print_help(command=command, dest=sys.stdout, raw=raw)
    return 0


def _find_help_command(cli_args):
    """
    Determines the specific help command based on CLI arguments.
    It searches topics in a specific order: special, operator, then option.
    """
    tag_queries = [arg for arg in cli_args if arg.startswith(TAG_PREFIX)]
    help_topics = [arg for arg in cli_args if arg not in HELP_FLAGS]
    first_topic = help_topics[0].lower() if help_topics else None
    help_args = [arg for arg in cli_args if arg in HELP_FLAGS]
    return (
        (tag_queries and tag_queries[0])
        or find_special_topic_command(first_topic)
        or find_operator_topic_command(help_topics)
        or find_option_topic_command(help_topics)
        or find_image_mod_topic_command(help_topics)
        or (len(help_args) > 1 and find_special_topic_command(help_args[1]))
        or None
    )


def _get_flags_and_setup_logging(cli_args) -> tuple[set, list[str]]:
    """Initializes standard logging and filters flags."""
    found_flags = set()

    debug = any(arg in DEBUG_FLAGS for arg in cli_args)
    if debug:
        found_flags.add("debug")
    verbose = debug or any(arg in VERBOSE_FLAGS for arg in cli_args)
    if verbose:
        found_flags.add("verbose")

    # Re-apply standard logging configuration in case arguments loaded from files
    # changed the debug or verbose level post-initialization.
    _setup_logging(cli_args)

    flags_to_remove = VERBOSE_FLAGS.union(DEBUG_FLAGS)
    remaining_args = [x for x in cli_args if x not in flags_to_remove]
    _check_remaining_args_or_raise(remaining_args)
    return found_flags, remaining_args


def _check_remaining_args_or_raise(remaining_args):
    import difflib

    all_known_flaglike = DEBUG_FLAGS.union(
        COMPLETION_FLAGS, HELP_FLAGS, VERBOSE_FLAGS, VERSION_FLAGS, {"-", "---"}
    )
    for r_arg in remaining_args:
        if r_arg.startswith("--") and r_arg.strip() not in all_known_flaglike:
            msg = f"Unknown option '{r_arg}'."

            matches = difflib.get_close_matches(r_arg, all_known_flaglike, n=1, cutoff=0.6)
            if matches:
                msg += f" Did you mean '{matches[0]}'?"
            raise UserCommandLineError(msg)


def _handle_special_flags(nonverbose_cli_args):
    """
    Handles --version and --help flags by delegating to helper functions (and exiting).
    And also --completion
    """
    if any(arg in VERSION_FLAGS for arg in nonverbose_cli_args):
        print_version()
        return 0

    if any(arg in HELP_FLAGS for arg in nonverbose_cli_args):
        command = _find_help_command(nonverbose_cli_args)
        return _print_help_and_chill(command)

    try:
        i, _completion_arg = next(
            x for x in enumerate(nonverbose_cli_args) if (x[1] == "--completion")
        )
        if i + 1 >= len(nonverbose_cli_args):
            shell = None
        else:
            shell = nonverbose_cli_args[i + 1]
        return _handle_completion_arg(shell)
    except StopIteration:
        return None


def _handle_completion_arg(shell):
    try:
        return completion_setup(shell)
    except NotImplementedError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
