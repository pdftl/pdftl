# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/dump_text.py

"""Dump information about destinations in a PDF file"""

import io
import logging
from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.fonts.type3_tounicode_synthesis import patch_missing_to_unicode
from pdftl.utils.dependencies import ensure_dependencies
from pdftl.utils.hooks import text_dump_hook
from pdftl.utils.string_utils import remove_ignored_nonprinting_chars

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)

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


def _extract_text_from_pdf(pdf_pike: "pikepdf.Pdf", pdfium, password=None) -> list:
    """
    Opens a PDF, iterates over each page, and returns a list of text
    blocks, one per page, using pdfium's own text extraction for
    everything.

    Before handing the PDF to pdfium, patches a synthetic /ToUnicode
    onto any Type3 font that lacks one (see
    pdftl.fonts.type3_tounicode_synthesis). pdfium's own Type3 fallback
    -- unicode == charcode, used only when /ToUnicode is absent --
    produces raw PDF character codes as if they were Unicode codepoints
    whenever a Type3 font's character codes don't happen to match their
    intended Unicode values, which is exactly the case this patching
    fixes at the source rather than by replacing pdfium's output.

    This patching happens on the in-memory `pdf_pike` copy only; the
    synthesized /ToUnicode is never written back to the user's actual
    output file (`dump_text` is a read-only/info operation --
    `skip_pipeline_save=True` below).
    """
    texts = []

    patched_count = patch_missing_to_unicode(pdf_pike)
    logger.debug("Patched synthetic /ToUnicode onto %d Type3 font(s).", patched_count)

    with io.BytesIO() as buffer:
        pdf_pike.save(buffer)
        buffer.seek(0)

        with pdfium.PdfDocument(buffer, password=password) as pdf:
            logger.debug("Opened from buffer using pdfium with %s pages.", len(pdf))
            for page_idx, page in enumerate(pdf):
                try:
                    textpage = page.get_textpage()
                    text = textpage.get_text_range()
                finally:
                    page.close()

                texts.append(text)

    return texts


@register_operation(
    "dump_text",
    cli_hook=text_dump_hook,
    tags=["info", "text", "experimental"],
    type="single input operation",
    desc="Print PDF text data to the console or a file",
    long_desc=_DUMP_TEXT_LONG_DESC,
    usage="<input> dump_text [output <output>]",
    skip_pipeline_save=True,
    examples=_DUMP_TEXT_EXAMPLES,
    args=([c.INPUT_PDF, c.INPUT_PASSWORD], {"output_file": c.OUTPUT}),
)
def dump_text(input_pdf, input_password, output_file=None) -> OpResult:
    """
    Dump text content of a PDF file.
    """
    # FIXME: output_file unused, why?

    if input_password is None:
        logger.debug("No password supplied.")
        input_password = ""  # nosec

    ensure_dependencies(
        feature_name="dump_text", dependencies=["pypdfium2"], extra_tag="dump-text"
    )

    import pypdfium2

    output_text = "\n\f\n".join(
        map(
            remove_ignored_nonprinting_chars,
            _extract_text_from_pdf(input_pdf, pypdfium2, input_password),
        )
    )

    return OpResult(success=True, data=output_text)
