# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/add_images.py

"""Add user-specified images onto PDF pages as overlays or underlays.

This operation uses parsed rule strings mapping page ranges to individual
image paths and geometric rendering parameters, applying them in-place.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pikepdf

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError

logger = logging.getLogger(__name__)

_ADD_IMAGES_LONG_DESC = r"""
Add user-specified images to PDF pages as overlays or underlays.

An image rule specification has the format:

>  `[page range]<delimiter><image path(s)><delimiter>[<options>]`

`<delimiter>` must be a single, non-alphanumeric character (e.g., /, !, #).
Multiple image paths within delimiters can be separated by spaces or commas.

### Options

Options are passed as comma-separated key=value pairs inside
parentheses, e.g., (`position=top-right`, `scale_mode=fit`, `opacity=0.5`).

#### Positioning and Layout Options

* `underlay=<true|false>`: Draw the image behind the page contents instead of on top.
  Default: `false`.

* `scale_mode=<stretch|fit|fill|none>`: Aspect ratio preservation logic.
  Default: `none`.

* `position=<keyword>`: Preset position (top-left, center, bottom-right, etc.).
  Default: `bottom-left`.

* `width=<dim>`, `height=<dim>`: Dimensions with a unit (e.g., `10cm`, `2in`, `150pt`).

* `offset-x=<dim>`, `offset-y=<dim>`: Extra offset displacement relative to the anchor position.
  Default: `0`.

* `opacity=<float>`: Transparency setting between 0.0 (transparent) and 1.0 (opaque).
  Default: `1.0`.

"""

_ADD_IMAGES_EXAMPLES = [
    {
        "desc": "Stamp a logo on the top right of all pages",
        "cmd": (
            "in.pdf add_images "
            "'/logo.png/(position=top-right, width=5cm, offset-x=-0.5cm, offset-y=-0.5cm)'"
            " output out.pdf"
        ),
        "test_setup": {"copy_images": {"logo.png": "logo.png"}},
    },
    {
        "desc": "Add a background underlay image to even pages, scaling to fill",
        "cmd": (
            "in.pdf add_images "
            "'even!background.jpg!(underlay=true, scale_mode=fill)'"
            " output out.pdf"
        ),
        "test_setup": {"copy_images": {"background.jpg": "background.jpg"}},
    },
    {
        "desc": "Overlay a semi-transparent watermark centered on page 1",
        "cmd": (
            "in.pdf add_images "
            "'1#watermark.png#(position=center, opacity=0.3, width=200pt)'"
            " output out.pdf"
        ),
        "test_setup": {"copy_images": {"watermark.png": "watermark.png"}},
    },
]


@register_operation(
    "add_images",
    tags=["in_place", "images", "overlay", "underlay"],
    type="single input operation",
    desc="Stamp user-specified images onto PDF pages",
    long_desc=_ADD_IMAGES_LONG_DESC,
    usage="<input> add_images <spec>... output <file>",
    examples=_ADD_IMAGES_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def add_images_pdf(pdf: pikepdf.Pdf, raw_rules: list[str]) -> OpResult:
    """
    Applies all parsed add_images rules to a PDF **in-place**.

    This function coordinates the rule parser and the ImageStamper
    engine to apply overlays/underlays to the input PDF.
    """
    from pdftl.operations.parsers.add_images_parser import parse_add_images_rules
    from pdftl.utils.images.stamper import stamp_images_on_pdf

    try:
        page_rules = parse_add_images_rules(raw_rules, len(pdf.pages))
    except ValueError as exc:
        raise InvalidArgumentError(str(exc))

    for page_idx, rules in page_rules.items():
        for rule in rules:
            stamp_images_on_pdf(
                pdf=pdf,
                images=rule["images"],
                pages=str(page_idx + 1),
                underlay=rule["underlay"],
                scale_mode=rule["scale_mode"],
                position=rule["position"],
                width=rule["width"],
                height=rule["height"],
                offset_x=rule["offset-x"],
                offset_y=rule["offset-y"],
                opacity=rule["opacity"],
            )

    return OpResult(success=True, pdf=pdf)
