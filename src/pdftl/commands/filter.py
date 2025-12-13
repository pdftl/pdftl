# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/filter.py

"""Pass-through operation"""

from pikepdf import Pdf

from pdftl.core.registry import register_operation

# FIXME: repeated data here (cf CLI_DATA)

_FILTER_LONG_DESC = """

This does nothing. Use `filter` to keep a PDF file
unchanged, before applying output options (such as
encryption, compression, etc).  This is the default
operation if no operation is explicitly provided.

"""

_FILTER_EXAMPLES = [
    {
        "cmd": "in.pdf output out.pdf",
        "desc": "Do nothing",
    },
    {
        "cmd": "in.pdf filter output out.pdf",
        "desc": "Do nothing",
    },
    {
        "cmd": "in.pdf output out.pdf uncompress",
        "desc": "Uncompress in.pdf",
    },
]


@register_operation(
    "filter",
    tags=["in_place"],
    type="single input operation",
    desc="Do nothing. (The default if no `<operation>` is given.)",
    long_desc=_FILTER_LONG_DESC,
    usage="<input> [filter] output <file> [<option...>]",
    examples=_FILTER_EXAMPLES,
    args=(["input_pdf"], {}),
)
def filter_pdf(pdf: Pdf):
    """
    Return the given PDF.
    """
    return pdf
