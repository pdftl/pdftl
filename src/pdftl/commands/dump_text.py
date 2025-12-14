# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/dump_dests.py

"""Dump information about destinations in a PDF file"""

import logging

logger = logging.getLogger(__name__)

from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError
from pdftl.output.dump import dump
from pdftl.utils.string import remove_ignored_nonprinting_chars

_DUMP_TEXT_LONG_DESC = """

The `dump_text` operation attempts to extract text from a PDF file
and dumps it to stdout or the given output file.

**Warning** This is experimental and may be unreliable.

It uses the python library `pypdfium2`. To automatically install this
optional dependency run:

    pip install pdftl[dump-text]

"""

_DUMP_TEXT_EXAMPLES = [
    {"cmd": "in.pdf dump_text", "desc": "Print destinations for in.pdf"},
    {
        "cmd": "in.pdf dump_text output out.txt",
        "desc": "Save text from in.pdf to out.txt",
    },
]

_MISSING_DEPS_ERROR_MSG = """
The dump_text operation requires the 'pypdfium2' library.
To automatically install this optional dependency: pip install pdftl[dump-text]
"""


def _extract_text_from_pdf(pdf_path, pdfium, password=None) -> list:
    """
    Opens a PDF, iterates over each page, and return a list of text blocks,
    one per page.
    """
    texts = []

    with pdfium.PdfDocument(pdf_path, password=password) as pdf:
        logger.debug("Opened '%s' using pdfium with %s pages.", pdf_path, len(pdf))
        for page in pdf:
            try:
                textpage = page.get_textpage()
                texts.append(textpage.get_text_range())
            finally:
                page.close()
    return texts


@register_operation(
    "dump_text",
    tags=["info", "text", "experimental"],
    type="single input operation",
    desc="Print PDF text data to the console or a file",
    long_desc=_DUMP_TEXT_LONG_DESC,
    usage="<input> dump_text [output <output>]",
    examples=_DUMP_TEXT_EXAMPLES,
    args=(["input_filename", "input_password"], {"output_file": "output"}),
)
def dump_text(input_filename, input_password, output_file=None):
    """
    Dump text content of a PDF file.
    """
    logger.debug("Dumping text for '%s' using pdfminer", input_filename)

    if input_password is None:
        logger.debug("No password supplied.")
        input_password = ""

    try:
        import pypdfium2
    except ImportError:
        raise InvalidArgumentError(_MISSING_DEPS_ERROR_MSG)

    output_text = "\n\f\n".join(
        map(
            remove_ignored_nonprinting_chars,
            _extract_text_from_pdf(input_filename, pypdfium2, input_password),
        )
    )
    dump(output_text, dest=output_file)
