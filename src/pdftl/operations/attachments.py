# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/attachments.py

"""Extract file attachments from a PDF

See also: pdftl.output.attach for adding attachments to output.
"""

import json
import logging
from pathlib import Path

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.output.dump import dump
from pdftl.utils.user_input import dirname_completer
from pdftl.utils.attachment_utils import attachment_metadata
from pdftl.utils.string_utils import compact_json_string
from pdftl.utils.hooks import from_result_meta

logger = logging.getLogger(__name__)

_DUMP_FILES_LONG_DESC = """
The `dump_files` operation lists files attached to the input PDF as JSON.

Each attachment is an object with the following fields (all optional except
`key` and `filename`):

| Field         | Description                                                  |
| :------------ | :----------------------------------------------------------- |
| `key`         | Internal NameTree key (may differ from `filename`)           |
| `filename`    | Attachment filename as stored in the filespec                |
| `description` | Human-readable description, if present                       |
| `mime_type`   | MIME type (e.g. `/text/plain`, `/application/pdf`)           |
| `relationship`| Relationship type (e.g. `Source`, `Data`, `Unspecified`)     |
| `file_size`   | Uncompressed file size in bytes                              |
| `stored_size` | Size as stored in PDF (compressed), in bytes                 |
| `compression` | PDF filter name (e.g. `/FlateDecode`), if compressed         |
| `created`     | Creation date in PDF date format                             |
| `modified`    | Modification date in PDF date format                         |
| `pages`       | List of pages this attachment is attached to                 |

Note: document-level attachments are attachments which are not attached to any pages.
In this case, the `pages` field is absent.
"""

_DUMP_FILES_EXAMPLES = [
    {
        "cmd": "a.pdf dump_files",
        "desc": "List all files attached to a.pdf as JSON",
    },
    {
        "cmd": "a.pdf dump_files | jq '.[] | select(.file_size > 1000000)'",
        "desc": "List attachments larger than 1MB",
    },
    {
        "cmd": "a.pdf dump_files | jq '.[].filename'",
        "desc": "List just the filenames of all attachments",
    },
]


_UNPACK_FILES_LONG_DESC = """

The `unpack_files` operation unpacks files attached to the input
PDF file, if there are any. The directory to save attachments in
defaults to the working directory, and may be controlled by adding
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


def dump_files_cli_hook(result: OpResult, stage, _pipeline):
    """CLI Hook to print the attachment list as JSON."""

    if not result.success:
        return

    if result.meta is None:
        raise AttributeError("Missing metadata")

    output_file = from_result_meta(result, c.META_OUTPUT_FILE)

    dump(
        compact_json_string(json.dumps(result.data, indent=2), fold_dicts=False, max_content=50),
        dest=output_file,
    )


def unpack_files_cli_hook(result: OpResult, stage, _pipeline):
    """CLI Hook to write extracted files to disk."""

    if not result.success:
        return

    if result.meta is None:
        raise AttributeError("Missing metadata")

    output_dir = result.meta.get("output_dir")
    has_attachments = False

    if output_dir:
        output_path = Path(output_dir)
        if not output_path.is_dir():
            logger.error("Output directory %s does not seem to be a directory", output_path)
            return

    for filename, file_bytes in result.data:
        has_attachments = True
        out_path = Path(output_dir) / filename if output_dir else Path(filename)

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
    desc="List file attachments as JSON",
    long_desc=_DUMP_FILES_LONG_DESC,
    cli_hook=dump_files_cli_hook,
    usage="<input> dump_files [output <output>]",
    examples=_DUMP_FILES_EXAMPLES,
    args=(
        [c.INPUT_FILENAME, c.INPUT_PDF],
        {"output_file": c.OUTPUT},
    ),
    skip_pipeline_save=True,
)
def dump_files(input_filename, pdf, output_file=None) -> OpResult:
    """
    List files attached to the PDF.
    Returns a list of dicts with all available attachment metadata.
    """
    meta = {"input_filename": input_filename, c.META_OUTPUT_FILE: output_file}
    if not pdf.attachments:
        return OpResult(success=True, data=[], meta=meta)

    # Map attachments to the pages they appear on
    annot_map = {}
    for p_num, page in enumerate(pdf.pages, start=1):
        if "/Annots" in page:
            for annot in page.Annots:
                if annot.get("/Subtype") == "/FileAttachment":
                    fs = annot.get("/FS")
                    if fs is not None:
                        annot_map.setdefault(fs.objgen, set()).add(p_num)

    data = []
    for name, attachment in pdf.attachments.items():
        objgen = attachment.obj.objgen
        record = {
            "key": name,
            "filename": attachment.filename,
        }
        pages = sorted(annot_map.get(objgen, []))
        if pages:
            record["pages"] = pages
        record.update(attachment_metadata(attachment))
        data.append(record)

    return OpResult(
        success=True,
        data=data,
        meta=meta,
    )


def _resolve_output_dir(output_dir, get_input):
    if output_dir == "PROMPT":
        return get_input(
            "Enter an output directory for the attachments: ",
            completer=dirname_completer,
        )
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
    skip_pipeline_save=True,
)
def unpack_files(pdf, get_input, output_dir=None) -> OpResult:
    """
    Unpacks attachments from a single PDF file.
    Returns a generator yielding (filename, bytes).
    """
    import pikepdf

    final_output_dir = _resolve_output_dir(output_dir, get_input)

    def _generator():
        if not pdf.attachments:
            return

        for name, attachment in pdf.attachments.items():
            logger.debug("found attachment=%s", name)
            try:
                file_bytes = attachment.get_file().read_bytes()
            except (pikepdf.PdfError, AttributeError):
                logger.warning("Skipping attachment '%s': invalid or missing internal data.", name)
                continue

            # Strip any directory components from the filename to prevent
            # path traversal (e.g. attachments with names like /home/user/file.png)
            safe_name = Path(attachment.filename).name
            yield safe_name, file_bytes

    return OpResult(success=True, data=_generator(), meta={"output_dir": final_output_dir})
