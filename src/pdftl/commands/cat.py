# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/cat.py

"""Concatenate PDFs, attempting to preserve links as far as possible."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from pdftl.core.registry import register_operation
from pdftl.pages.add_pages import add_pages
from pdftl.utils.page_specs import expand_specs_to_pages

_CAT_LONG_DESC = """

The `cat` operation is used to assemble pages from one or more PDFs.
Input PDFs can be assigned to handles (e.g., `A=one.pdf B=two.pdf`).
Each spec may refer to these handles to select pages or page ranges.
When no handle is specified, the first input file is used.

Considerable effort is made to do "the right thing" as regards
hyperlinks and any outlines (table of contents). Since `cat` is quite
flexible, exactly what the right thing is sometimes not clearly
defined, but nevertheless if you do get a clearly incorrect output,
then please report it as a bug.

"""

_CAT_EXAMPLES = [
    {
        "cmd": "A=doc1.pdf B=doc2.pdf cat A B output combined.pdf",
        "desc": "Concatenate two entire files",
    },
    {
        "cmd": "in.pdf cat 1-5 9-end output partial.pdf",
        "desc": "Select a range of pages from one file:",
    },
    {
        "cmd": "in.pdf cat 1-5x2east 9-endleftz-1 output partial.pdf",
        "desc": (
            "Double the size of pages 1-5 and rotate east.\n"
            "Zoom out (e.g., A4 to A3) and turn pages 9-end left."
        ),
    },
    {
        "cmd": "A=a.pdf B=b.pdf cat A1-5 B3-end output result.pdf",
        "desc": "Concatenate pages 1-5 from a.pdf with pages 3 onwards from b.pdf",
    },
]


@register_operation(
    "cat",
    tags=["from_scratch", "page_order"],
    desc="Concatenate pages from input PDFs into a new PDF",
    usage="<input>... cat <spec>... output <file> [<option>...]",
    long_desc=_CAT_LONG_DESC,
    examples=_CAT_EXAMPLES,
    args=(
        ["inputs", "operation_args", "opened_pdfs"],
        {"aliases": "aliases"},
    ),
)
def cat_pages(inputs, specs, opened_pdfs, aliases=None):
    """
    Concatenates pages from input PDFs into a new PDF, then rebuilds all
    links and named destinations, including transforming link target
    coordinates.
    """
    import pikepdf

    new_pdf = pikepdf.new()

    # Resolve the user's page specifications into a concrete list of pages.
    source_pages_to_process = expand_specs_to_pages(specs, aliases, inputs, opened_pdfs)

    if not source_pages_to_process:
        raise ValueError("Range specifications gave no pages")

    add_pages(new_pdf, opened_pdfs, source_pages_to_process)

    return new_pdf
