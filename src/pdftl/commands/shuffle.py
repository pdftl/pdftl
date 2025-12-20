# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/shuffle.py

"""Shuffle together pages from several PDF files."""

# FIXME: what happens to links?

import logging

logger = logging.getLogger(__name__)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from pdftl.core.registry import register_operation
from pdftl.core.types import Compatibility, FeatureType, Parity, Status
from pdftl.pages.add_pages import add_pages
from pdftl.utils.page_specs import expand_specs_to_pages


def _get_page_tuples_array(inputs, specs, opened_pdfs, aliases=None):
    if not specs:
        # construct default page_tuples_array
        return [
            expand_specs_to_pages(["1-end"], {}, [input], [opened_pdfs[input_idx]])
            for input_idx, input in enumerate(inputs)
        ]

    return [expand_specs_to_pages([spec], aliases, inputs, opened_pdfs) for spec in specs]


_SHUFFLE_LONG_DESC = """

The `shuffle` operation collates pages from multiple input documents,
taking one page from each document in turn to create an interleaved
output file.

"""

_SHUFFLE_EXAMPLES = [
    {
        "cmd": "A=doc1.pdf B=doc2.pdf shuffle A B output shuffled.pdf",
        "desc": "Shuffle two documents together:",
    }
]

_SHUFFLE_COMPATABILITY = Compatibility(
    type=FeatureType.PDFTK_COMPAT,
    status=Status.STABLE,
    parity=Parity.FULL,
    notes="Supports standard shuffling behavior.",
    pdftk_op="shuffle",
    todo=[
        "Investigate link preservation (currently links might be lost)",
        "Support complex collating patterns (e.g. 2 pages from A, 1 from B)",
        "Allow reversing input streams during shuffle",
    ],
)


@register_operation(
    "shuffle",
    tags=["from_scratch", "page_order"],
    desc="Interleave pages from multiple input PDFs",
    long_desc=_SHUFFLE_LONG_DESC,
    usage="<input>... shuffle [<spec>...] output <file> [<option...>]",
    examples=_SHUFFLE_EXAMPLES,
    args=(
        ["inputs", "operation_args", "opened_pdfs"],
        {"aliases": "aliases"},
    ),
    compatibility=_SHUFFLE_COMPATABILITY,
)
def shuffle_pdfs(inputs, specs, opened_pdfs, aliases=None):
    """
    Shuffles (interleaves) pages from multiple PDFs, applying
    transformations like rotation and scaling.
    """
    from pikepdf import Pdf

    assert len(opened_pdfs) > 0
    page_tuples_array = _get_page_tuples_array(inputs, specs, opened_pdfs, aliases)
    # logger.debug("page_tuples_array = \n  %s", page_tuples_array)
    if not page_tuples_array:
        raise ValueError("Range specifications gave no pages")
    max_len = max(len(x) for x in page_tuples_array)
    logger.debug("max_len=%s", max_len)
    source_pages_to_process = []
    for i in range(max_len):
        for page_tuples in page_tuples_array:
            # logger.debug("page_tuples=%s", page_tuples)
            if i >= len(page_tuples):
                continue
            source_pages_to_process.append(page_tuples[i % len(page_tuples)])
    new_pdf = Pdf.new()
    add_pages(new_pdf, opened_pdfs, source_pages_to_process)
    return new_pdf
