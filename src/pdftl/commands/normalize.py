# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/normalize.py

"""Normalize page content streams"""

import logging

logger = logging.getLogger(__name__)
from pdftl.core.registry import register_operation
from pdftl.utils.normalize import normalize_page_content_stream
from pdftl.utils.page_specs import page_numbers_matching_page_spec

_NORMALIZE_LONG_DESC = """

The `normalize` operation formats page
content streams for readability. This results in the content
stream appearing with one PDF content operator per line.

To see the effect of this operation in the PDF file (for
example, to examine a content stream in order to come up
with a regular expression to use in the `replace` operation)
you should pass the `uncompress` option to avoid compression
of the page content stream.

The `<spec>` specification defines the pages on which to
perform this operation. See the help topic [[`page_specs`]].

"""

_NORMALIZE_EXAMPLES = [
    {
        "cmd": "in.pdf normalize 1-3 output out.pdf uncompress",
        "desc": "Normalize the content streams for pages 1-3, with uncompressed output",
    }
]


@register_operation(
    "normalize",
    tags=["in_place"],
    type="single input operation",
    desc="Reformat page content streams",
    long_desc=_NORMALIZE_LONG_DESC,
    usage="<input> replace [<spec>...] output <output>",
    examples=_NORMALIZE_EXAMPLES,
    args=(["input_pdf", "operation_args"], {}),
)
def normalize_content_streams(pdf, specs):
    """
    Normalize page content streams.
    """
    if not specs:
        specs = ["1-end"]

    for spec in specs:
        for page_num in page_numbers_matching_page_spec(spec, len(pdf.pages)):
            normalize_page_content_stream(pdf, pdf.pages[page_num - 1])
            logger.debug(
                "After normalization, page content starts: %s",
                pdf.pages[page_num - 1].Contents,
            )
    return pdf
