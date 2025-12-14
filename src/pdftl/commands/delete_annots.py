# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/delete_annots.py

"""Delete annotations"""

from pdftl.core.registry import register_operation
from pdftl.utils.page_specs import page_numbers_matching_page_spec

_DELETE_ANNOTS_LONG_DESC = """

The `delete_annots` operation deletes annotations in a PDF file.
Page ranges can be specified. All annotations are removed from those pages.
The default page range is all pages.

"""

_DELETE_ANNOTS_EXAMPLES = [
    {
        "cmd": "in.pdf delete_annots output out.pdf",
        "desc": "Delete all annotations from in.pdf",
    }
]


@register_operation(
    "delete_annots",
    tags=["in_place", "annotations"],
    type="single input operation",
    desc="Delete annotation info",
    long_desc=_DELETE_ANNOTS_LONG_DESC,
    usage="<input> delete_annots [<spec>...] output <output>",
    examples=_DELETE_ANNOTS_EXAMPLES,
    args=(["input_pdf", "operation_args"], {}),
)
def delete_annots(pdf, specs):
    """
    Delete annotations from a PDF.
    """
    import pikepdf

    if not specs:
        specs = ["-"]

    num_pages = len(pdf.pages)
    for spec in specs:
        for page_num in page_numbers_matching_page_spec(spec, num_pages):
            page = pdf.pages[page_num - 1]
            if hasattr(page, "Annots"):
                page.Annots = pikepdf.Array([])
    return pdf
