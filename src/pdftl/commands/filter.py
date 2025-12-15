# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/filter.py

"""Pass-through operation"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Pdf

from pdftl.core.registry import register_help_topic, register_operation
from pdftl.core.types import HelpExample

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
    desc="Do nothing (the default if `<operation>` is absent)",
    long_desc=_FILTER_LONG_DESC,
    usage="<input> [filter] output <file> [<option...>]",
    examples=_FILTER_EXAMPLES,
    args=(["input_pdf"], {}),
)
def filter_pdf(pdf: "Pdf"):
    """
    Return the given PDF.
    """
    return pdf


@register_help_topic(
    "filter_mode",
    title="filter mode",
    desc="Filter mode: apply output options only",
    examples=[
        HelpExample(cmd="in.pdf output out.pdf", desc="Do nothing"),
        HelpExample(cmd="in.pdf filter output out.pdf", desc="Do nothing"),
        HelpExample(
            cmd="in.pdf output out.pdf uncompress",
            desc="uncompress the input file, making minimal changes",
        ),
    ],
)
def _filter_mode_help_topic():
    """
    If no operation is given and one input PDF file is specified,
    then filter mode is activated. The input PDF file is processed
    minimally and saved with the given output options. This is
    likely to be less destructive than using cat.
    """
