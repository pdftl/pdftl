# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/attachments.py

"""Extract file attachments from a PDF

See also: pdftl.output.attach for adding attachments to output.
"""

import logging

logger = logging.getLogger(__name__)

from pdftl.core.registry import register_operation
from pdftl.utils.user_input import dirname_completer

_LIST_FILES_LONG_DESC = """

The `list_files` operation lists files attached to the input
PDF file, if there are any.

The output format is

```
  filesize filename
```

where filesize is in bytes.

"""

_LIST_FILES_EXAMPLES = [
    {
        "cmd": "a.pdf list_files",
        "desc": "List all files attached to a.pdf",
    },
]


_UNPACK_FILES_LONG_DESC = """

The `unpack_files` operation unpacks files attached to the input
PDF file, if there are any. The directory to save attachments in
defaults to the working directory, any may be controlled by adding
`output <directory>`.

**Warning** This command will silently overwrite any existing files with
clashing filenames.

"""

_UNPACK_FILES_EXAMPLES = [
    {
        "cmd": "a.pdf unpack_files",
        "desc": "Save all files attached to a.pdf in the current directory",
    },
    {
        "cmd": "a.pdf unpack_files output /tmp/",
        "desc": "Save all files attached to a.pdf in /tmp/",
    },
    {
        "cmd": "a.pdf unpack_files output PROMPT",
        "desc": "Prompt for a directory in which to save all files attached to a.pdf",
    },
]


@register_operation(
    "list_files",
    tags=["attachments", "info"],
    type="single input operation",
    desc="List file attachments",
    long_desc=_LIST_FILES_LONG_DESC,
    usage="<input> list_files [output <dir>]",
    examples=_LIST_FILES_EXAMPLES,
    args=(
        ["input_filename", "input_pdf", "get_input"],
        {"operation": "operation", "output_dir": "output"},
    ),
)
@register_operation(
    "unpack_files",
    tags=["attachments"],
    type="single input operation",
    desc="Unpack file attachments",
    long_desc=_UNPACK_FILES_LONG_DESC,
    usage="<input> unpack_files [output <dir>]",
    examples=_UNPACK_FILES_EXAMPLES,
    args=(
        ["input_filename", "input_pdf", "get_input"],
        {"output_dir": "output", "operation": "operation"},
    ),
)
def unpack_files(input_filename, pdf, get_input, output_dir=None, operation=None):
    """
    Lists or unpacks attachments from a single PDF file.
    """

    try:
        output_path = _get_output_path(output_dir, operation, get_input)
    except ValueError as e:
        logger.error(e)
        return

    action_func = _get_operation_action(operation)
    if not action_func:
        return

    if not pdf.attachments:
        _handle_no_attachments(operation, input_filename)
        return

    _process_attachments(pdf, action_func, output_path)


##################################################


def _get_output_path(output_dir_str, operation, get_input):
    """Determines and validates the output directory path."""
    from pathlib import Path

    path_str = output_dir_str if output_dir_str is not None else "./"

    if operation == "unpack_files":
        if path_str == "PROMPT":
            path_str = get_input(
                "Enter an output directory for the attachments: ",
                completer=dirname_completer,
            )

        output_path = Path(path_str)
        if not output_path.is_dir():
            raise ValueError(f"\n  Output directory {output_path} does not seem to be a directory")
        return output_path

    # For any other operation, just return the Path object without validation
    return Path(path_str)


def _get_operation_action(operation):
    """Returns the function corresponding to the requested operation."""
    actions = {
        "unpack_files": _unpack_single_file,
        "list_files": _list_single_file,
    }
    action_func = actions.get(operation)
    if not action_func:
        logger.warning("No valid operation '%s' specified to process attachments.", operation)
    return action_func


def _handle_no_attachments(operation, input_filename):
    """Prints or logs a message when a PDF has no attachments."""
    if operation == "list_files":
        print(f"No attachments found in {input_filename}")
    else:
        logger.debug("No attachments found")


def _process_attachments(pdf, action_func, output_path):
    """Iterates through attachments and executes the given action for each one."""
    for name, attachment in pdf.attachments.items():
        logger.debug("found attachment=%s", name)
        action_func(attachment, name, output_path)


##################################################


def _unpack_single_file(attachment, name, output_dir):
    """Saves a single attachment to the specified output directory."""
    file_bytes = attachment.get_file().read_bytes()
    output_filename = output_dir / name
    logger.debug("saving %s bytes to %s", len(file_bytes), output_filename)
    try:
        with open(output_filename, "wb") as f:
            f.write(file_bytes)
    except OSError as e:
        logger.warning("Could not write file %s: %s", output_filename, e)


def _list_single_file(attachment, name, output_dir):
    """Prints the size and projected path of a single attachment."""
    file_bytes = attachment.get_file().read_bytes()
    output_filename = output_dir / name
    print(f"{len(file_bytes):>9} {output_filename}")
