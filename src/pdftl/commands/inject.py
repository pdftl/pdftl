# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/inject.py

"""Inject PDF code at the start or end of a page content stream"""

import logging

logger = logging.getLogger(__name__)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Pdf

from pdftl.core.registry import register_operation
from pdftl.utils.affix_content import affix_content
from pdftl.utils.page_specs import page_number_matches_page_spec

from .parsers.inject_parser import parse_inject_args

_INJECT_LONG_DESC = """

Add PDF code `<code>` at head or tail of page content streams for pages.

`<injection_spec> = [<spec>...] (head|tail) <code>`

where `<spec>` is a page specification (default: all pages),
and `<code>` is a PDF content stream fragment to instead at
either the head or tail of those pages' streams.

**Warning:** This can easily break content streams.
The resulting PDF files will not render as expected.

"""

_INJECT_EXAMPLES = [
    {
        "cmd": "in.pdf inject head '2 0 0 2 0 0 cm' output out.pdf",
        "desc": "Scale up page content by 2x:",
    }
]


@register_operation(
    "inject",
    tags=["in_place", "dangerous", "content_stream"],
    type="single input operation",
    desc="Inject code at start or end of page content streams",
    long_desc=_INJECT_LONG_DESC,
    usage="<input> inject <injection_spec>... output <file> [<option...>]",
    examples=_INJECT_EXAMPLES,
    args=(["input_pdf", "operation_args"], {}),
)
def inject_pdf(pdf: "Pdf", inject_args: list):
    """
    Injects code at the start and/or end of page content streams.
    """
    # 1. Parse arguments into structured rules
    heads, tails, remaining_specs = parse_inject_args(inject_args)

    # 2. Validate that there are no dangling arguments
    if len(remaining_specs) > 0:
        raise ValueError(
            f"Unexpected arguments {remaining_specs} to inject. "
            "Did you forget to specify 'head' or 'tail'?"
        )

    # 3. Apply the rules to each page
    for idx, page in enumerate(pdf.pages):
        page_num = idx + 1
        # The original function applied all head injections before tail injections for each page.
        # This order is preserved.
        _apply_injection_rules(pdf, page, page_num, heads, "head")
        _apply_injection_rules(pdf, page, page_num, tails, "tail")

    return pdf


def _apply_injection_rules(pdf, page, page_num, rules, injection_type):
    """
    Applies a set of injection rules to a single page.

    Args:
        rules: The list of rules to apply (e.g., the 'heads' or 'tails' list).
        injection_type: A string, either "head" or "tail".
    """
    total_pages = len(pdf.pages)
    # A descriptive verb for the debug message
    action_verb = "prefixed" if injection_type == "head" else "postfixed"

    for rule in rules:
        code = rule["code"]
        for spec in rule["specs"]:
            if page_number_matches_page_spec(page_num, spec, total_pages):
                logger.debug("page %s will have '%s' %s", page_num, code, action_verb)
                affix_content(page, code, injection_type)
