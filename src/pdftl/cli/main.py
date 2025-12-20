# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/cli/main.py

"""Main CLI entry point and helper methods"""

import getpass
import logging
import sys

logger = logging.getLogger(__name__)

from pdftl.cli.constants import DEBUG_FLAGS, HELP_FLAGS, VERBOSE_FLAGS, VERSION_FLAGS
from pdftl.cli.parser import (
    parse_cli_stage,
    parse_options_and_specs,
    split_args_by_separator,
)
from pdftl.cli.pipeline import PipelineManager
from pdftl.cli.whoami import WHOAMI
from pdftl.core.registry import register_option
from pdftl.exceptions import PackageError, UserCommandLineError
from pdftl.registry_init import initialize_registry
from pdftl.utils.user_input import UserInputContext, get_input


@register_option("verbose", desc="Turn on verbose output", type="flag")
def _verbose_option():
    pass


def main(argv=None):
    """Main entry point for the command-line interface."""
    if argv is None:
        argv = sys.argv

    _, args_for_parsing = _is_verbose_and_setup_logging(argv[1:])
    initialize_registry()
    _handle_special_flags(argv[1:])

    if not args_for_parsing:
        _print_help_and_exit(None)

    pipeline = _prepare_pipeline_from_remaining_args(args_for_parsing)

    try:
        pipeline.run()

    except (UserCommandLineError, PackageError) as e:
        print(f"Error: {e}", file=sys.stderr)
        logger.debug("A user command line error occurred", exc_info=True)
        sys.exit(1)


##################################################


def _prepare_pipeline_from_remaining_args(args_for_parsing):
    logger.debug("args_for_parsing=%s", args_for_parsing)
    stages_args = split_args_by_separator(args_for_parsing)
    logger.debug("stages_args=%s", stages_args)
    final_stage_args, global_options = parse_options_and_specs(stages_args[-1])
    logger.debug("final_stage_args=%s, global_options=%s", final_stage_args, global_options)
    stages_args[-1] = final_stage_args
    parsed_stages = []
    for i, stage_args in enumerate(stages_args):
        if not stage_args and i == len(stages_args) - 1:
            continue
        parsed_stages.append(parse_cli_stage(stage_args, is_first_stage=i == 0))
    if not parsed_stages:
        raise UserCommandLineError(
            "No pipeline stages found.\n "
            "Did you forget an operation?  Hint: pdftl help operations"
        )
    input_context = UserInputContext(get_input=get_input, get_pass=getpass.getpass)
    return PipelineManager(parsed_stages, global_options, input_context)


def _print_help_and_exit(command):
    """Prints the relevant help topic and exits the program."""
    from pdftl.cli.help import print_help

    print_help(command=command, dest=sys.stdout)
    sys.exit(0)


def _find_help_command(cli_args):
    """
    Determines the specific help command based on CLI arguments.
    It searches topics in a specific order: special, operator, then option.
    """
    from pdftl.cli.help import (
        TAG_PREFIX,
        find_operator_topic_command,
        find_option_topic_command,
        find_special_topic_command,
    )

    tag_queries = [arg for arg in cli_args if arg.startswith(TAG_PREFIX)]
    help_topics = [arg for arg in cli_args if arg not in HELP_FLAGS]
    first_topic = help_topics[0].lower() if help_topics else None
    help_args = [arg for arg in cli_args if arg in HELP_FLAGS]
    return (
        (tag_queries and tag_queries[0])
        or find_special_topic_command(first_topic)
        or find_operator_topic_command(help_topics)
        or find_option_topic_command(help_topics)
        or (len(help_args) > 1 and find_special_topic_command(help_args[1]))
        or None
    )


def _is_verbose_and_setup_logging(cli_args) -> tuple[bool, list[str]]:
    """Initializes the root logger based on quiet and/or verbose
    flags. Returns the verbose flag and a list of remaining
    arguments, with all verbose flags removed."""
    debug = any(arg in DEBUG_FLAGS for arg in cli_args)
    verbose = debug or any(arg in VERBOSE_FLAGS for arg in cli_args)
    level = logging.DEBUG if debug else logging.INFO if verbose else logging.WARN

    # Use a simpler format for info, detailed format for debug
    if verbose:
        log_format = "[%(levelname)s]%(filename)s:%(funcName)s:%(lineno)d: %(message)s"
    else:
        log_format = f"[{WHOAMI}] %(message)s"

    # Configure the root logger and the pdftl-specific loggers
    if debug:
        from rich.logging import RichHandler

        logging.basicConfig(format=log_format, handlers=[RichHandler()])
    else:
        # avoid importing rich.logging this way
        logging.basicConfig(format=log_format)

    logging.getLogger("pdftl").setLevel(level)
    flags_to_remove = VERBOSE_FLAGS.union(DEBUG_FLAGS)
    remaining_args = [x for x in cli_args if x not in flags_to_remove]
    return verbose, remaining_args


def _handle_special_flags(nonverbose_cli_args):
    """
    Handles --version and --help flags by delegating to helper functions (and exiting).
    """
    if any(arg in VERSION_FLAGS for arg in nonverbose_cli_args):
        from pdftl.cli.help import print_version

        print_version()
        sys.exit(0)
    if any(arg in HELP_FLAGS for arg in nonverbose_cli_args):
        command = _find_help_command(nonverbose_cli_args)
        _print_help_and_exit(command)


if __name__ == "__main__":
    main()
