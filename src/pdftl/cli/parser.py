# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/cli/parser.py

"""Parser for CLI interface"""

import logging
from contextlib import suppress

import pdftl.core.constants as c
from pdftl.cli.constants import SUB_EACH, SUB_END, SUB_START
from pdftl.cli.pipeline import CliStage, EachSubPipeline, InlineSubPipeline
from pdftl.core.registry import registry
from pdftl.exceptions import DuplicateArgumentError, InvalidArgumentError, MissingArgumentError

logger = logging.getLogger(__name__)


def _get_registry_data_entries(main_key, sub_key, test, transform=None):
    """Helper to filter and transform entries from the registry dictionary."""
    return {
        transform(x) if transform is not None else x
        for x, y in getattr(registry, main_key).items()
        if sub_key in y and test(y[sub_key])
    }


def _get_flag_keywords():
    """Fetch current flag keywords from registry."""
    return _get_registry_data_entries("options", "type", lambda x: x == "flag")


def _get_value_keywords():
    """Fetch current value keywords from registry."""
    return _get_registry_data_entries(
        "options", "type", lambda x: "mandatory argument" in x, lambda x: x.split(" ")[0]
    )


def _find_operation_and_split(args):
    """Splits arguments into pre-operation and post-operation lists."""
    for i, token in enumerate(args):
        if not isinstance(token, str):
            continue
        if token.lower() in registry.operations:
            return (token.lower(), args[:i], args[i + 1 :])
    return (None, args, [])


def _parse_flag_keyword(arg, options):
    """Parses a flag keyword (e.g., 'encrypt_128bit')."""
    options[arg.lower()] = True
    return 1


def _parse_value_keyword(arg, args, i, options):
    """Parses a keyword that requires a single following value."""
    if arg.lower() in options:
        raise DuplicateArgumentError(f"Duplicate keyword: {arg}")
    if i + 1 >= len(args):
        raise MissingArgumentError(f"Missing value for keyword: {arg}")
    options[arg.lower()] = args[i + 1]
    return 2


def _raise_unknown_arg_error(arg, just_slurped_allow_index):
    """Generates and raises a detailed error for an unknown argument."""
    msg = f"Unknown argument in <option>... section: {arg}"
    if just_slurped_allow_index:
        allow_kw_list = ", ".join(sorted(c.ALLOW_PERMISSIONS))
        msg += "\n  Maybe you wanted to give an additional 'allow' permission?"
        msg += f" Valid permissions are:\n  {allow_kw_list}"
    raise InvalidArgumentError(msg)


def _parse_multiple_arguments(option, args, i, argument_q, allow_no_args=False, hint=None):
    """Parses a keyword which can take multiple values."""
    if i + 1 >= len(args):
        if allow_no_args:
            return (1, i + 1)
        raise MissingArgumentError(f"Missing value for option '{option}': {args[i]}")
    if not argument_q(args[i + 1]):
        if allow_no_args:
            return (1, i + 1)
        raise InvalidArgumentError(
            f"Invalid argument '{args[i + 1]}' following '{option}' keyword." + (" " + hint)
            if hint
            else ""
        )
    current_pos = i + 1
    while current_pos < len(args) and argument_q(args[current_pos]):
        current_pos += 1
    consumed_count = current_pos - i
    return (consumed_count, current_pos)


def _parse_allow_permissions(args, i, options):
    """Parses the 'allow' keyword, which can take multiple values."""
    keyword = "allow"

    def allow_argument_q(x):
        """Is x a valid argument for allow?"""
        return x.lower() in c.ALLOW_PERMISSIONS_L

    allow_kw_list = ", ".join(sorted(c.ALLOW_PERMISSIONS))
    consumed_count, current_pos = _parse_multiple_arguments(
        keyword,
        args,
        i,
        allow_argument_q,
        allow_no_args=True,
        hint=f"Value 'allow' arguments are:\n  {allow_kw_list}",
    )
    permissions = options.setdefault(keyword, set())
    for j in range(1, consumed_count):
        permissions.add(c.ALLOW_PERMISSIONS_L[args[i + j].lower()])
    return (consumed_count, current_pos)


def parse_options_and_specs(args):
    """
    Parses arguments into specifications and keyword options.
    Returns (specs, options).
    """
    logger.debug("args=%s", args)
    options, specs = ({}, [])

    i = 0
    just_slurped_allow_index = None
    while i < len(args):
        arg = args[i]

        if not isinstance(arg, str):
            specs.append(arg)
            i += 1
            continue

        arg_lower = arg.lower()

        if arg_lower == "allow":
            consumed, end_index = _parse_allow_permissions(args, i, options)
            i += consumed
            just_slurped_allow_index = end_index
            continue

        if arg_lower == c.OUTPUT or arg_lower in _get_value_keywords():
            i += _parse_value_keyword(arg, args, i, options)
        elif arg_lower in _get_flag_keywords():
            i += _parse_flag_keyword(arg, options)
        elif not options:
            specs.append(arg)
            i += 1
        else:
            _raise_unknown_arg_error(arg, just_slurped_allow_index is not None)
            # _raise_unknown_arg_error raises, so exits the loop
        just_slurped_allow_index = None

    return (specs, options)


def _separate_file_and_pw_args(pre_op_args):
    """Separates pre-operation args into file-related and password-related lists."""
    try:
        pw_keyword_index = pre_op_args.index("input_pw")
        file_args = pre_op_args[:pw_keyword_index]
        pw_args = pre_op_args[pw_keyword_index + 1 :]
    except ValueError:
        file_args = pre_op_args
        pw_args = []
    return (file_args, pw_args)


def _parse_file_handles(file_args):
    """Parses file arguments to extract inputs and handles."""
    inputs, handles = ([], {})
    for i, arg in enumerate(file_args):
        if isinstance(arg, str):
            if len(arg) > 2 and arg[1] == "=" and arg[0].isupper():
                handle, filename = arg.split("=", 1)
                handles[handle] = i
                inputs.append(filename)
            else:
                inputs.append(arg)
        else:
            # Suppose InlineSubPipeline or other non-str inputs, which may be named
            inputs.append(arg)
            if hasattr(arg, "handle_name") and arg.handle_name:
                handles[arg.handle_name] = i
    return (inputs, handles)


def _handle_pipeline_input(inputs, handles, is_first_stage):
    """Injects the pipeline input placeholder '_' if needed."""
    if not is_first_stage and (not any(f in ["-", "_"] for f in inputs)):
        inputs.insert(0, "_")
        for handle in handles:
            handles[handle] += 1
    if "_" not in handles:
        with suppress(ValueError):
            handles["_"] = inputs.index("_")
    return (inputs, handles)


def _parse_passwords(pw_args):
    """Parses password arguments into dictionaries for handle-based and positional passwords."""
    passwords_by_handle, passwords_by_order = ({}, [])
    for arg in pw_args:
        if len(arg) > 2 and arg[1] == "=" and arg[0].isupper():
            handle, password = arg.split("=", 1)
            passwords_by_handle[handle] = password
        else:
            passwords_by_order.append(arg)
    return (passwords_by_handle, passwords_by_order)


def _assign_passwords(num_inputs, handles, passwords_by_handle, passwords_by_order):
    """Assigns passwords to inputs, prioritizing handles over positional order."""
    input_passwords: list[str | None] = [None] * num_inputs
    for handle, index in handles.items():
        if handle in passwords_by_handle and index < num_inputs:
            input_passwords[index] = passwords_by_handle[handle]
    pw_iter = iter(passwords_by_order)
    for i in range(num_inputs):
        if input_passwords[i] is None:
            try:
                input_passwords[i] = next(pw_iter)
            except StopIteration:
                break
    return input_passwords


def _parse_pre_operation_args(pre_op_args, is_first_stage):
    """
    Parses input files, handles, and passwords.
    """
    logger.debug("pre_op_args=%s", pre_op_args)
    file_args, pw_args = _separate_file_and_pw_args(pre_op_args)
    # logger.debug("file_args=%s, pw_args=%s", file_args, pw_args)
    inputs, handles = _parse_file_handles(file_args)
    inputs, handles = _handle_pipeline_input(inputs, handles, is_first_stage)
    passwords_by_handle, passwords_by_order = _parse_passwords(pw_args)
    input_passwords = _assign_passwords(
        len(inputs), handles, passwords_by_handle, passwords_by_order
    )
    return (inputs, handles, input_passwords)


def parse_cli_stage(stage_args, is_first_stage):
    """Parses a list of arguments for a single pipeline stage."""
    logger.debug("stage_args=%s, is_first_stage=%s", stage_args, is_first_stage)
    operation, pre_op, post_op = _find_operation_and_split(stage_args)
    logger.debug(
        "before filter block: operation=%s, pre_op=%s, post_op=%s",
        operation,
        pre_op,
        post_op,
    )
    implicit_op = False
    if not stage_args:
        logger.debug("no stage_args, going to filter mode")
        operation = "filter"
        implicit_op = True
    elif operation is None:
        logger.debug("no operation, going to filter mode")
        operation = "filter"
        implicit_op = True
        pre_op = stage_args
        post_op = []
    logger.debug(
        "after filter block: operation=%s, pre_op=%s, post_op=%s, implicit_op=%s",
        operation,
        pre_op,
        post_op,
        implicit_op,
    )
    inputs, handles, passwords = _parse_pre_operation_args(pre_op, is_first_stage)
    logger.debug("inputs=%s, handles=%s", inputs, handles)
    specs, options = parse_options_and_specs(post_op)
    if implicit_op and len(inputs) > 1:
        operation = "cat"
        logging.debug("Changing from filter to cat")
    logger.debug(  # nosemgrep - passwords are redacted
        "CliStage(operation=%s, inputs=%s, input_passwords=%s,"
        "handles=%s, operation_args=%s, options=%s",
        operation,
        inputs,
        "<REDACTED>",
        handles,
        specs,
        options,
    )
    stage = CliStage(
        operation=operation,
        inputs=inputs,
        input_passwords=passwords,
        handles=handles,
        operation_args=specs,
        options=options,
    )
    return stage


def _recursive_group_pipelines(arg_iter, depth=0):
    """
    Recursively consumes an iterator of arguments.
    Returns a list of strings and InlinePipeline objects.
    """
    result = []

    while True:
        try:
            token = next(arg_iter)
        except StopIteration as exc:
            if depth > 0:
                raise InvalidArgumentError(f"Unclosed sub-pipeline, missing {SUB_END}.") from exc
            break

        is_named_sub = isinstance(token, str) and token.endswith("=" + SUB_START)
        if token == SUB_START or is_named_sub:
            # Recurse
            inner_args = _recursive_group_pipelines(arg_iter, depth + 1)

            # Now we must PARSE these args into Stages immediately,
            # because InlinePipeline expects list[CliStage]

            # 1. Split inner args by '---' (handles nested separators naturally
            #    because the recursive call already consumed the nested JOB DONE)
            inner_stages_raw = _split_flat_by_separator(inner_args, "---")

            # 2. Parse into CliStage objects
            #    We assume the inner pipeline starts fresh (is_first=True for its first stage)
            inner_stages_parsed = [
                parse_cli_stage(s, is_first_stage=(k == 0)) for k, s in enumerate(inner_stages_raw)
            ]
            pipeline_obj = InlineSubPipeline(stages=inner_stages_parsed)

            if is_named_sub:
                # token is like "B=JOB", split at the LAST '=' to be safe,
                # though strictly handles are usually simple.
                # Using maxsplit=1 from the left is consistent with your other parsing.
                handle, _ = token.split("=", 1)
                pipeline_obj.handle_name = handle

            result.append(pipeline_obj)
        elif token == SUB_EACH:
            inner_args = _recursive_group_pipelines(arg_iter, depth + 1)
            inner_stages_raw = _split_flat_by_separator(inner_args, "---")
            inner_stages_parsed = [
                parse_cli_stage(s, is_first_stage=False) for s in inner_stages_raw
            ]
            result.append(EachSubPipeline(stages=inner_stages_parsed))
        elif token == SUB_END:
            _validate_final_depth(depth)
            return result
        else:
            result.append(token)

    return result


def _validate_final_depth(depth):
    if depth == 0:
        raise InvalidArgumentError(f"Unexpected '{SUB_END}' found without opening '{SUB_START}'.")


def _split_flat_by_separator(argv, separator="---"):
    """Splits a list of arguments into stages based on a separator."""
    stages, current_stage = ([], [])
    for arg in argv:
        if arg == separator:
            stages.append(current_stage)
            current_stage = []
        else:
            current_stage.append(arg)
    stages.append(current_stage)
    return stages


def split_args_by_separator(argv, separator="---"):
    """
    Splits a list of arguments into stages based on a separator.
    Handles 'JOB ... DONE' inline sub-pipelines by pre-parsing them.
    """
    # 1. Pre-process to resolve JOB ... DONE into InlinePipeline objects
    #    We convert argv list to an iterator for the recursive function
    grouped_args = _recursive_group_pipelines(iter(argv))

    # 2. Now split the top-level list by '---'
    #    Since inner '---' were inside the recursive call, they are gone/encapsulated.
    return _split_flat_by_separator(grouped_args, separator)
