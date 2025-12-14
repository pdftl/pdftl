# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/overlay.py

"""Apply an overlay or underlay of page(s) from one PDF file to
another"""

import logging

logger = logging.getLogger(__name__)

from pdftl.core.registry import register_operation

# Shared implementation for stamp, multistamp, background, multibackground


_STAMP_LONG_DESC = """

The `stamp` operation overlays the first page of the stamp PDF
onto every page of the input document.

"""

_STAMP_EXAMPLES = [
    {
        "cmd": "in.pdf stamp watermark.pdf output stamped.pdf",
        "desc": "Apply a watermark to all pages:",
    }
]

_MULTISTAMP_LONG_DESC = """

Overlay pages from the stamp PDF onto the corresponding pages
of the input PDF: page 1 of the stamp is overlaid
onto page 1 of the input, etc.

"""

_MULTISTAMP_EXAMPLES = [
    {
        "cmd": "in.pdf multistamp overlay.pdf output out.pdf",
        "desc": "Apply a multi-page overlay:",
    }
]

_BACKGROUND_LONG_DESC = """

The `background` operation places the first page of the
background PDF underneath every page of the input document.

"""

_BACKGROUND_EXAMPLES = [
    {
        "cmd": "in.pdf background letterhead.pdf output final.pdf",
        "desc": "Apply a letterhead to a document:",
    }
]


_MULTIBACKGROUND_LONG_DESC = """

Underlay pages from the background PDF behind
the corresponding pages in the input PDF: page 1 is placed
behind page 1 of the input, etc.

"""

_MULTIBACKGROUND_EXAMPLES = [
    {
        "cmd": "in.pdf multibackground bgs.pdf output final.pdf",
        "desc": "Apply a multi-page background:",
    }
]


def _register_overlay_op(name, desc, long_desc, examples):
    """
    A simple helper to register an overlay operation, filling in
    the common boilerplate for this file.
    """
    stamp_input_prefix = "stamp" if "stamp" in name else "background"
    return register_operation(
        name=name,
        desc=desc,
        usage=f"<input> {name} <{stamp_input_prefix}_pdf> output <file> [<option...>]",
        long_desc=long_desc,
        examples=examples,
        tags=["in_place", "overlay"],
        type="single input operation",
        args=(["input_pdf", "overlay_pdf"], {"multi": "multi", "on_top": "on_top"}),
    )


@_register_overlay_op(
    "stamp",
    desc="Stamp a 1-page PDF onto each page of an input PDF",
    long_desc=_STAMP_LONG_DESC,
    examples=_STAMP_EXAMPLES,
)
@_register_overlay_op(
    "multistamp",
    desc="Stamp multiple pages onto an input PDF",
    long_desc=_MULTISTAMP_LONG_DESC,
    examples=_MULTISTAMP_EXAMPLES,
)
@_register_overlay_op(
    "background",
    desc="Use a 1-page PDF as the background for each page",
    long_desc=_BACKGROUND_LONG_DESC,
    examples=_BACKGROUND_EXAMPLES,
)
@_register_overlay_op(
    "multibackground",
    desc="Use multiple pages as backgrounds",
    examples=_MULTIBACKGROUND_EXAMPLES,
    long_desc=_MULTIBACKGROUND_LONG_DESC,
)
def apply_overlay(
    input_pdf,
    overlay_filename,
    on_top=True,
    multi=False,
    scale_to_fit=True,
):
    """
    Apply overlay or underlay PDF to input PDF. Optional scaling to fit
    base page like pdftk.
    """
    import pikepdf

    def debug(x):
        logger.debug(x)

    debug(
        f"  input_pdf={input_pdf}, "
        f"overlay_filename={overlay_filename}, "
        f"multi={multi}, on_top={on_top}, "
        f"scale_to_fit={scale_to_fit}"
    )

    overlay_pdf = pikepdf.open(overlay_filename)
    base_pages = input_pdf.pages
    overlay_pages_all = overlay_pdf.pages
    if not overlay_pages_all:
        raise ValueError("Overlay PDF has no pages")

    for i, base_page in enumerate(base_pages):
        overlay_idx = min(i, len(overlay_pages_all) - 1) if multi else 0
        overlay_page = overlay_pages_all[overlay_idx]
        rect = (
            pikepdf.Rectangle(*map(float, base_page.trimbox or base_page.MediaBox))
            if scale_to_fit
            else None
        )
        if on_top:
            base_page.add_overlay(overlay_page, rect=rect)
        else:
            base_page.add_underlay(overlay_page, rect=rect)

    return input_pdf
