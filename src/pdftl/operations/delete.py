# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/delete.py

"""Delete pages from a single PDF file"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Pdf

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.utils.page_specs import page_numbers_matching_page_specs
from pdftl.operations.helpers.del_pages import del_pages

logger = logging.getLogger(__name__)

_DELETE_LONG_DESC = """

The `delete` operation is used to delete pages from one PDF.  This
operation is performed 'in-place' and the output PDF should retain all
essential features of the input except the missing pages. So if you
want to extract pages 1-5 of a PDF file, then it may be a good idea is
use this operation to delete pages `6-end` rather than using `cat`
(which would have to create a new PDF file from scratch and graft
pages 1-5 from the input file in, using all sorts of trickery).

If the document has custom page labels, they are remapped so that every
surviving page keeps its own original label.

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
    tags=["in_place", "pages", "delete"],
    type="single input operation",
    desc="Delete pages from an input PDF",
    long_desc=_DELETE_LONG_DESC,
    usage="<input> delete <spec>... output <file> [<option...>]",
    examples=_DELETE_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def delete_pages(pdf: "Pdf", specs) -> OpResult:
    """
    Deletes pages from input PDF, and otherwise leaves the PDF
    structure essentially unchanged.
    """
    pages_to_delete = page_numbers_matching_page_specs(specs, len(pdf.pages))
    del_pages(pdf, pages_to_delete)
    return OpResult(success=True, pdf=pdf)
