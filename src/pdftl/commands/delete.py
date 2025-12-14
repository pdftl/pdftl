# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/delete.py

"""Delete pages from a single PDF file"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Pdf

from pdftl.core.registry import register_operation
from pdftl.utils.page_specs import page_numbers_matching_page_specs

_DELETE_LONG_DESC = """

The `delete` operation is used to delete pages from one PDF.  This
operation is performed 'in-place' and the output PDF should retain all
essential features of the input except the missing pages. So if you
want to extract pages 1-5 of a PDF file, then it may be a good idea is
use this operation to delete pages `6-end` rather than using `cat`
(which would have to create a new PDF file from scratch and graft
pages 1-5 from the input file in, using all sorts of trickery).

"""

_DELETE_EXAMPLES = [
    {
        "cmd": "in.pdf delete 1 output out.pdf",
        "desc": "Delete the first page",
    },
    {
        "cmd": "in.pdf delete '~1' output out.pdf",
        "desc": "Keep only the first page (by deleting the others)",
    },
]


@register_operation(
    "delete",
    tags=["in_place", "pages"],
    type="single input operation",
    desc="Delete pages from an input PDF",
    long_desc=_DELETE_LONG_DESC,
    usage="<input> delete <spec>... output <file> [<option...>]",
    examples=_DELETE_EXAMPLES,
    args=(["input_pdf", "operation_args"], {}),
)
def delete_pages(pdf: "Pdf", specs):
    """
    Deletes pages from input PDF, and otherwise leaves the PDF
    structure essentially unchanged.
    """
    pages_to_delete = page_numbers_matching_page_specs(specs, len(pdf.pages))
    for page_num in sorted(pages_to_delete, reverse=True):
        del pdf.pages[page_num - 1]

    return pdf
