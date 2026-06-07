# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/dump_images.py

"""Dump information about embedded images in a PDF file"""

import json
import logging
from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.utils.hooks import from_result_meta
from pdftl.utils.io_helpers import smart_open_maybe_dash
from pdftl.utils.page_specs import page_numbers_matching_page_specs
from pdftl.utils.string_utils import compact_json_string
from pdftl.utils.image_utils import extract_pdf_images

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


_DUMP_IMAGES_LONG_DESC = """

The `dump_images` operation extracts metadata about embedded images
in a PDF file.

It traverses the PDF's content streams (including nested Form XObjects)
to correctly calculate the absolute bounding boxes of all drawn images using
the Current Transformation Matrix (CTM).

Outputs a JSON object containing page-level image metadata, including:
* **name**: Internal PDF resource name
* **obj_id**: PDF object number (shared across pages if the same image is reused)
* **bbox**: Absolute bounding box coordinates [x_min, y_min, x_max, y_max] in PDF points
* **width_px**: Native image width in pixels
* **height_px**: Native image height in pixels
* **ppi_x**: Horizontal resolution in pixels per inch, derived from bbox and pixel dimensions
* **ppi_y**: Vertical resolution in pixels per inch, derived from bbox and pixel dimensions
* **colorspace**: Resolved color space descriptor — includes family, ICC profile details,
  colorant names for spot colors, and alternate space where applicable
* **bits**: Bit depth per component
* **stream_bytes**: Compressed stream size in bytes as stored in the PDF
* **format**: Compression filter, e.g. flatedecode (PNG-style), dctdecode (JPEG)

Note: If the same image object is drawn multiple times (e.g. as a tiling pattern),
it will appear once per placement with its own bbox and ppi values. The obj_id
field can be used to identify duplicate placements of the same underlying stream.

You can optionally provide page specifications to limit extraction to specific pages.
You can also filter by resolution by providing `min_dpi=<n>` or `max_dpi=<n>` as arguments.
"""

_DUMP_IMAGES_EXAMPLES = [
    {"cmd": "in.pdf dump_images", "desc": "Print image metadata for in.pdf to console"},
    {
        "cmd": "in.pdf dump_images output images.json",
        "desc": "Save image metadata for in.pdf to a file",
    },
    {
        "cmd": "in.pdf dump_images output images.json --- output copy.pdf",
        "desc": "Save image metadata for in.pdf to a file and save a copy of in.pdf",
    },
    {
        "cmd": "in.pdf dump_images 1 3-5",
        "desc": "Print image metadata for pages 1, 3, 4, and 5",
    },
    {
        "cmd": "in.pdf dump_images max_dpi=150",
        "desc": "List only images with a resolution exceeding 150 DPI.",
    },
]


def dump_images_cli_hook(result: OpResult, stage, _pipeline):
    """Writes the image data to stdout or a file in JSON."""
    output_file = from_result_meta(result, c.META_OUTPUT_FILE)

    with smart_open_maybe_dash(output_file) as file:
        json_string = compact_json_string(
            json.dumps({"images": result.data}, indent=2), fold_dicts=False
        )
        file.write(json_string + "\n")


@register_operation(
    "dump_images",
    tags=["info", "metadata", "images"],
    type="single input operation",
    desc="Extract PDF embedded image metadata to JSON",
    long_desc=_DUMP_IMAGES_LONG_DESC,
    examples=_DUMP_IMAGES_EXAMPLES,
    cli_hook=dump_images_cli_hook,
    usage="<input> dump_images [<spec>...] [output <output>]",
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {"output_file": c.OUTPUT}),
    skip_pipeline_save=True,
)
def dump_images(pdf, specs, output_file=None) -> OpResult:
    """Dump embedded image metadata of a PDF file."""
    page_specs = []
    min_dpi = 0
    max_dpi = float("inf")

    for spec in specs or []:
        if spec.startswith("min_dpi="):
            min_dpi = int(spec.split("=")[1])
        elif spec.startswith("max_dpi="):
            max_dpi = int(spec.split("=")[1])
        else:
            page_specs.append(spec)

    num_pages = len(pdf.pages)
    target_pages = (
        sorted(list(page_numbers_matching_page_specs(page_specs, num_pages)))
        if page_specs
        else list(range(1, num_pages + 1))
    )

    image_data = extract_pdf_images(pdf, target_pages)

    filtered_data = []
    for img in image_data:
        img.pop("xobj", None)  # Ensure the raw object isn't dumped to JSON
        if min_dpi <= img["ppi_x"] <= max_dpi or min_dpi <= img["ppi_y"] <= max_dpi:
            filtered_data.append(img)

    return OpResult(
        success=True,
        data=filtered_data,
        meta={c.META_OUTPUT_FILE: output_file},
    )
