# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/burst.py

"""Burst a PDF file into individual pages"""

import logging

logger = logging.getLogger(__name__)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

import pdftl.api
import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.page_specs import page_numbers_matching_page_specs

_BURST_LONG_DESC = """

The `burst` operation splits a single input PDF into multiple
single-page PDF files. An optional output template can be provided.

`split_spec` is an optional page specification, giving the 'split
points', i.e.,, the initial page of each split chunk. The list of
split points will be sorted and deduplicated before it is used, so
order is irrelevant here. If omitted, burst defaults to splitting into
single pages (equivalent to `split_spec` being `1-end`).

"""

_BURST_EXAMPLES = [
    {
        "cmd": "my.pdf burst",
        "desc": "Burst a file into page_1.pdf, page_2.pdf, etc.",
    },
    {
        "cmd": "my.pdf burst output out%04d.pdf",
        "desc": "Burst a file into single-page files out0001.pdf, out0002.pdf, etc.",
    },
    {
        "cmd": "my.pdf burst step3 output out%04d.pdf",
        "desc": (
            "Burst a file into chunks out0001.pdf with pages 1-3, "
            "out0002.pdf with pages 4-6, etc."
        ),
    },
]


def burst_cli_hook(result: OpResult, stage, pipeline):
    """
    CLI-specific side effect: Writes the burst pages to disk.
    This function is only called by the CLI pipeline.
    """
    # The generator yields (filename, pil_image) tuples

    burst_generator = result.data

    if not burst_generator:
        logger.debug("No burst_generator")
        return

    logger.info("Bursting pages to disk...")
    count = 0
    for filename, pdf in burst_generator:
        pipeline.save_pdf_file(pdf, filename, stage)
        pdf.close()
        count += 1

    logger.info("Burst to %s files.", count)
    pdftl.api.dump_data(result.pdf, output="doc_data.txt", run_cli_hook=True)


@register_operation(
    "burst",
    tags=["from_scratch"],
    type="single input operation with optional output",
    desc="Split a single PDF into individual page files",
    long_desc=_BURST_LONG_DESC,
    examples=_BURST_EXAMPLES,
    usage="<input> burst [split_spec...] [output <template>]",
    args=(
        [c.OPENED_PDFS, c.OPERATION_ARGS],
        {
            c.OUTPUT_PATTERN: c.OUTPUT_PATTERN,
        },
    ),
    cli_hook=burst_cli_hook,
    skip_pipeline_save=True,
)
def burst_pdf(opened_pdfs, operation_args=None, output_pattern="pg_%04d.pdf") -> OpResult:
    """Split one or more PDFs into multiple files,
    single-page files by default.

    Args:
        opened_pdfs (list): A list of opened PDF files to burst

        operation_args (list): User-supplied arguments

        output_pattern (str): A C-style format string for the output
                              filenames, e.g., "page_%03d.pdf".

    Return: the first input pdf (for pipeline chainability)

    Note: Uses the hook side-effect to actually burst

    Bugs:

    * Discards various parts of the PDF file that may still be
      relevant to single-page files, e.g., internal links

    """
    specs = operation_args or ["1-end"]
    return OpResult(
        success=True,
        data=_generate_burst_chunks(opened_pdfs, specs, output_pattern),  # for API or hook
        pdf=opened_pdfs[
            0
        ],  # for possible subsequent pipeline (and dump_data call), NOT for saving
    )


def _generate_burst_chunks(opened_pdfs, specs, output_pattern):
    import pikepdf

    pattern = output_pattern or "pg_%04d.pdf"
    if "%" not in pattern:
        raise InvalidArgumentError("Output pattern must include a format specifier (e.g., %d)")

    chunk_counter = 0
    try:
        for source_pdf in opened_pdfs:
            previous_page_num = 1
            logger.debug("source_pdf=%s", source_pdf)
            pages = source_pdf.pages
            split_points = sorted(list(set(page_numbers_matching_page_specs(specs, len(pages)))))
            logger.debug("split_points = %s", split_points)
            for page_num in [*split_points, len(pages) + 1]:
                chunk_pages = pages[previous_page_num - 1 : page_num - 1]
                if not chunk_pages:
                    logger.debug("Empty chunk: %s to %s", previous_page_num, page_num)
                    continue
                previous_page_num = page_num
                chunk_counter += 1
                page_file = pattern % chunk_counter
                new_pdf = pikepdf.Pdf.new()
                new_pdf.pages.extend(chunk_pages)
                yield (page_file, new_pdf)
    finally:
        for source_pdf in opened_pdfs:
            source_pdf.close()
