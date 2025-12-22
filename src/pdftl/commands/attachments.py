# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/attachments.py

"""Extract file attachments from a PDF

See also: pdftl.output.attach for adding attachments to output.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.utils.user_input import dirname_completer

_DUMP_FILES_LONG_DESC = """

The `dump_files` operation lists files attached to the input
PDF file, if there are any.

The output format is

```
  filesize filename
```

where filesize is in bytes.

"""

_DUMP_FILES_EXAMPLES = [
    {
        "cmd": "a.pdf dump_files",
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


def dump_files_cli_hook(result: OpResult, _stage):
    """CLI Hook to print the file list."""
    if not result.success:
        return

    if not result.data:
        # Original behavior: print message if empty
        input_filename = result.meta.get("input_filename", "input")
        print(f"No attachments found in {input_filename}")
        return

    output_dir = result.meta.get("output_dir")
    base_path = Path(output_dir) if output_dir else Path(".")

    for item in result.data:
        # Show where the file would be saved (projected path)
        display_path = base_path / item["name"]
        print(f"{item['size']:>9} {display_path}")


def unpack_files_cli_hook(result: OpResult, _stage):
    """CLI Hook to write extracted files to disk."""
    if not result.success:
        return

    output_dir = result.meta.get("output_dir")

    # We iterate the generator here to trigger the file saves
    # The generator yields nothing if there were no attachments
    has_attachments = False

    if output_dir:
        output_path = Path(output_dir)
        if not output_path.is_dir():
            # We can try to create it, or raise error as per original logic
            # Original raised ValueError inside command.
            # We'll log error here to not crash pipeline.
            logger.error("Output directory %s does not seem to be a directory", output_path)
            return

    for name, file_bytes in result.data:
        has_attachments = True
        out_path = Path(output_dir) / name if output_dir else Path(name)

        logger.debug("saving %s bytes to %s", len(file_bytes), out_path)
        try:
            with open(out_path, "wb") as f:
                f.write(file_bytes)
        except OSError as e:
            logger.warning("Could not write file %s: %s", out_path, e)

    if not has_attachments:
        logger.debug("No attachments found")


@register_operation(
    "dump_files",
    tags=["attachments", "info"],
    type="single input operation",
    desc="List file attachments",
    long_desc=_DUMP_FILES_LONG_DESC,
    cli_hook=dump_files_cli_hook,
    usage="<input> dump_files [output <dir>]",
    examples=_DUMP_FILES_EXAMPLES,
    args=(
        [c.INPUT_FILENAME, c.INPUT_PDF, c.GET_INPUT],
        {"output_dir": c.OUTPUT},
    ),
)
def dump_files(input_filename, pdf, get_input, output_dir=None) -> OpResult:
    """
    List files attached to the PDF.
    Returns a list of dicts: {'name': str, 'size': int}.
    """
    if not pdf.attachments:
        return OpResult(success=True, data=[], meta={"input_filename": input_filename})

    # Handle prompt logic for consistency (even if just displaying projected path)
    final_output_dir = _resolve_output_dir(output_dir, get_input)

    data = []
    for name, attachment in pdf.attachments.items():
        file_bytes = attachment.get_file().read_bytes()
        data.append({"name": name, "size": len(file_bytes)})

    return OpResult(
        success=True,
        data=data,
        meta={"input_filename": input_filename, "output_dir": final_output_dir},
    )


def _resolve_output_dir(output_dir, get_input):
    if output_dir == "PROMPT":
        return get_input(
            "Enter an output directory for the attachments: ",
            completer=dirname_completer,
        )
    else:
        return output_dir


@register_operation(
    "unpack_files",
    tags=["attachments"],
    type="single input operation",
    desc="Unpack file attachments",
    long_desc=_UNPACK_FILES_LONG_DESC,
    cli_hook=unpack_files_cli_hook,
    usage="<input> unpack_files [output <dir>]",
    examples=_UNPACK_FILES_EXAMPLES,
    args=(
        [c.INPUT_PDF, c.GET_INPUT],
        {"output_dir": c.OUTPUT},
    ),
)
def unpack_files(pdf, get_input, output_dir=None) -> OpResult:
    """
    Unpacks attachments from a single PDF file.
    Returns a generator yielding (filename, bytes).
    """
    # Resolve output path prompt here because it requires user interaction
    final_output_dir = _resolve_output_dir(output_dir, get_input)

    # We return a generator to keep memory usage low for large attachments
    def _generator():
        if not pdf.attachments:
            return

        for name, attachment in pdf.attachments.items():
            logger.debug("found attachment=%s", name)
            file_bytes = attachment.get_file().read_bytes()
            yield name, file_bytes

    return OpResult(success=True, data=_generator(), meta={"output_dir": final_output_dir})
