# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/dump_data.py

"""Dump PDF metadata, and update PDF metadata from such a dump.

Public methods:

update_info
pdf_info

"""

import logging

logger = logging.getLogger(__name__)
import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.info.output_info import get_info, write_info
from pdftl.utils.io_helpers import smart_open_output

# BUG: 000301.pdf: rounding errors. Does pdftk just always round? Or
# do we need Decimal?


_DUMP_DATA_UTF8_LONG_DESC = """

Extracts document-level metadata and structural information
from the input PDF, identical to the `dump_data` operation,
except all string values in the output are written as raw
UTF-8. No XML-style escaping is applied.

This format is designed to be read by the `update_info_utf8`
operation. Use this if you need to inspect or process the
data with tools that do not understand XML escaping.

For a complete description of the output format and all
possible fields, see the help for `dump_data`.

"""

_DUMP_DATA_UTF8_EXAMPLES = [
    {
        "cmd": "in.pdf dump_data_utf8 output data.txt",
        "desc": "Save raw metadata for in.pdf to data.txt",
    }
]


_DUMP_DATA_LONG_DESC = """

Extracts document-level metadata and structural information
from the input PDF and prints it to the console (or a
specified file).

This operation is the primary way to export data for
inspection or for later use by the `update_info`
operation. All string values in the output are processed
with XML-style escaping (e.g., `<` becomes `&lt;`).

### Output Format Details

The output is a plain text, line-based, key-value format. It
consists of both simple top-level fields and multi-line
"stanzas". A stanza is a block of related data that begins
with a line like `InfoBegin` or `BookmarkBegin`.

The data from this command is consumed by `update_info`.

#### Top-Level Fields

These fields appear as simple `Key: Value` lines.

* `PdfID0: <hex_string>`
    * The first part of the PDF's unique file identifier.
    * *Updatable by `update_info`.*

* `PdfID1: <hex_string>`
    * The second part of the PDF's unique file identifier.
    * *Not updatable by `update_info`.*

* `NumberOfPages: <integer>`
    * The total number of pages in the document.
    * *Read-only. Not used by `update_info`.*

* `PdfVersion: <string>`
    * The PDF version string (e.g., `1.7`).
    * *Read-only. Not used by `update_info`.*

* `Encrypted: <Yes|No>`
    * Indicates if the document is encrypted.
    * *Read-only. Not used by `update_info`.*

* `InputFile: <path>`
    * The local path of the file being processed.
    * *Read-only. Not used by `update_info`.*

#### Stanzas

These are multi-line blocks, each describing a single record.
These can all be updated by `update_info`.

##### 1. Info Stanza (Document Metadata)

Represents a single entry in the PDF's `DocInfo` metadata dictionary.

* `InfoBegin`
* `InfoKey: <key_name>` - a standard PDF metadata field
    (like `Title`, `Author`, `Subject`, `Keywords`,
    `Creator`, `Producer`, `CreationDate`, `ModDate`) or any
    custom key.
* `InfoValue: <value_string>`


##### 2. Bookmark Stanza

Represents a single bookmark (outline) item.

* `BookmarkBegin`
* `BookmarkTitle: <title_string>`
* `BookmarkLevel: <integer>` - the nesting depth (1 is top level)
* `BookmarkPageNumber: <integer>` - 1-indexed target page number


##### 3. PageMedia Stanza (Page-level Boxes)

Describes the various geometry boxes for a specific page,
identified by `PageMediaNumber`. All coordinates are given
in PDF points.

* `PageMediaBegin`
* `PageMediaNumber: <integer>` - 1-indexed page number
* `PageMediaRotation: <0|90|180|270>`
* `PageMediaRect: [x1 y1 x2 y2]`
* `PageMediaCropRect: [x1 y1 x2 y2]`
* `PageMediaTrimRect: [x1 y1 x2 y2]`


##### 4. PageLabel Stanza (Logical Page Numbers)

Defines a page labelling style.

* `PageLabelBegin`
* `PageLabelNewIndex: <integer>`
   The 1-indexed physical starting page for this numbering
* `PageLabelPrefix: <string>`
   String to prepend to page label (e.g., `A-` for labels A-1, A-2 etc.)
* `PageLabelNumStyle: <Decimal|RomanUpper|RomanLower|AlphaUpper|AlphaLower>`
* `PageLabelStart: <integer>`
   The starting number for this labelling (e.g., 4)
"""

_DUMP_DATA_EXAMPLES = [
    {"cmd": "in.pdf dump_data", "desc": "Print XML-escaped metadata for in.pdf"},
    {
        "cmd": "in.pdf dump_data output data.txt",
        "desc": "Save XML-escaped metadata for in.pdf to data.txt",
    },
]


_SHORT_DUMP_DATA_DESC_PREFIX = "Metadata, page and bookmark info"


def dump_data_cli_hook(result: OpResult, _stage):
    """
    CLI-specific side effect: Writes the snapshot to stdout or a file.
    This function is only called by the CLI pipeline.
    """
    output_file = result.meta.get(c.META_OUTPUT_FILE)
    escape_xml = result.meta.get(c.META_ESCAPE_XML, True)
    extra_info = result.meta.get(c.META_EXTRA_INFO, False)

    with smart_open_output(output_file) as file:

        def writer(text):
            print(text, file=file)

        write_info(writer, result.data, escape_xml=escape_xml, extra_info=extra_info)


@register_operation(
    "dump_data_utf8",
    tags=["info", "metadata"],
    type="single input operation",
    desc=_SHORT_DUMP_DATA_DESC_PREFIX + " (in UTF-8)",
    long_desc=_DUMP_DATA_UTF8_LONG_DESC,
    cli_hook=dump_data_cli_hook,
    usage="<input> dump_data_utf8 [output <output>]",
    examples=_DUMP_DATA_UTF8_EXAMPLES,
    args=(
        [c.INPUT_PDF, c.INPUT_FILENAME],
        {"output_file": c.OUTPUT},
        {"escape_xml": False},
    ),
)
@register_operation(
    "dump_data",
    tags=["info", "metadata"],
    type="single input operation",
    desc=_SHORT_DUMP_DATA_DESC_PREFIX + " (XML-escaped)",
    long_desc=_DUMP_DATA_LONG_DESC,
    cli_hook=dump_data_cli_hook,
    usage="<input> dump_data [output <output>]",
    examples=_DUMP_DATA_EXAMPLES,
    args=([c.INPUT_PDF, c.INPUT_FILENAME], {"output_file": c.OUTPUT}),
)
def pdf_info(
    pdf,
    input_filename,
    output_file=None,
    escape_xml=True,
    extra_info=False,
) -> OpResult:
    """
    Imitate pdftk's dump_data output, writing to a file or stdout.
    """

    info = get_info(pdf, input_filename, extra_info=extra_info)

    return OpResult(
        success=True,
        pdf=pdf,
        data=info,
        is_discardable=True,
        meta={
            c.META_OUTPUT_FILE: output_file,
            c.META_ESCAPE_XML: escape_xml,
            c.META_EXTRA_INFO: extra_info,
        },
    )
