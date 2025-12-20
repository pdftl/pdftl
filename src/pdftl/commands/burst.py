# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/burst.py

"""Burst a PDF file into individual pages"""

import logging

logger = logging.getLogger(__name__)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from pdftl.core.registry import register_operation

_BURST_LONG_DESC = """

The `burst` operation splits a single input PDF into multiple
single-page PDF files. An optional output template can be provided.

"""

_BURST_EXAMPLES = [
    {
        "cmd": "my.pdf burst",
        "desc": "Burst a file into page_1.pdf, page_2.pdf, etc.:",
    }
]


@register_operation(
    "burst",
    tags=["from_scratch"],
    type="single input operation with optional output",
    desc="Split a single PDF into individual page files",
    long_desc=_BURST_LONG_DESC,
    examples=_BURST_EXAMPLES,
    usage="<input> burst [output <template>]",
    args=(
        ["opened_pdfs"],
        {
            "output_pattern": "output_pattern",
        },
    ),
)
def burst_pdf(opened_pdfs, output_pattern="pg_%04d.pdf"):
    """Split one or more PDFs into single-page files.

    Args:
        opened_pdfs (list): A list of opened PDF files to burst.

        output_pattern (str): A C-style format string for the output
                              filenames, e.g., "page_%03d.pdf".

    Return: a generator outputting pairs (filename, pdf) for
    saving to disk in callee

    Bugs:

    * Discards various parts of the PDF file that may still be
      relevant to single-page files, e.g., internal links

    """
    import pikepdf

    logger.debug("%s: opened_pdfs=%s", __name__, opened_pdfs)
    logger.debug("%s: output_pattern=%s", __name__, output_pattern)
    if output_pattern is None:
        output_pattern = "pg_%04d.pdf"

    page_counter = 0

    if "%" not in output_pattern:
        raise ValueError("Output pattern must include a format specifier (e.g., %d)")

    for source_pdf in opened_pdfs:
        logger.debug("source_pdf=%s", source_pdf)
        for page in source_pdf.pages:
            page_counter += 1
            page_file = output_pattern % page_counter
            new_pdf = pikepdf.Pdf.new()
            new_pdf.pages.append(page)
            logger.debug(
                "Burst: yielding. page_file=%s. source_pdf=%s. page.objgen=%s.",
                page_file,
                source_pdf,
                page.objgen,
            )
            yield (page_file, new_pdf)
