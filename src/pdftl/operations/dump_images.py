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

if TYPE_CHECKING:
    import pikepdf

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
* **colorspace**: Colorspace family, e.g. /DeviceRGB, /DeviceCMYK, /ICCBased
* **bits**: Bit depth per component
* **stream_bytes**: Compressed stream size in bytes as stored in the PDF
* **format**: Compression filter, e.g. flatedecode (PNG-style), dctdecode (JPEG)

Note: If the same image object is drawn multiple times (e.g. as a tiling pattern),
it will appear once per placement with its own bbox and ppi values. The obj_id
field can be used to identify duplicate placements of the same underlying stream.

You can optionally provide page specifications to limit extraction to specific pages.
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
]


def _multiply_matrices(m1, m2):
    """Multiplies two PDF transformation matrices: M_new = M1 x M2."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return [
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    ]


def _calculate_bbox(ctm):
    """Calculates the bounding box for a 1x1 unit square transformed by the CTM."""
    a, b, c, d, e, f = ctm
    x_coords = [e, a + e, c + e, a + c + e]
    y_coords = [f, b + f, d + f, b + d + f]
    return [
        round(min(x_coords), 2),
        round(min(y_coords), 2),
        round(max(x_coords), 2),
        round(max(y_coords), 2),
    ]


def _get_format(xobj, pikepdf):
    f = xobj.get("/Filter")
    if f is None:
        return "unknown"
    if isinstance(f, pikepdf.Array):
        # Multiple filters — report the first (outermost)
        f = f[0]
    return str(f).lstrip("/").lower()  # e.g. "dctdecode", "flatedecode"


def _extract_image_metadata(xobj, obj_name_str, ctm, image_list, pikepdf):
    bbox = _calculate_bbox(ctm)
    logger.debug("Extracting metadata for Image %s. Calculated bbox: %s", obj_name_str, bbox)

    try:
        stream_bytes = _read_stream_bytes(xobj)
    except (pikepdf.PdfError, ValueError):
        stream_bytes = 0

    width_px = int(xobj.get("/Width", 0))
    height_px = int(xobj.get("/Height", 0))
    bbox_width = bbox[2] - bbox[0]
    bbox_height = bbox[3] - bbox[1]
    image_list.append(
        {
            "name": obj_name_str,
            "obj_id": xobj.objgen[0],
            "bbox": bbox,
            "width_px": width_px,
            "height_px": height_px,
            "ppi_x": round(width_px / bbox_width * 72) if bbox_width > 0 else 0,
            "ppi_y": round(height_px / bbox_height * 72) if bbox_height > 0 else 0,
            "colorspace": _get_colorspace_name(xobj, pikepdf),
            "bits": int(xobj.get("/BitsPerComponent", 8)),
            "stream_bytes": stream_bytes,
            "format": _get_format(xobj, pikepdf),
        }
    )


def _get_colorspace_name(xobj, pikepdf):
    cs = xobj.get("/ColorSpace")
    if cs is None:
        return "Unknown"
    if isinstance(cs, pikepdf.Array):
        # First element is the colorspace family name e.g. /ICCBased, /Indexed
        return str(cs[0])
    return str(cs)


def _read_stream_bytes(xobj):
    return len(xobj.read_raw_bytes())


def _process_form_xobject(xobj, parent_resources, current_ctm, image_list):
    """Calculates a Form's internal matrix and recursively parses its stream."""
    form_matrix = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    if "/Matrix" in xobj:
        form_matrix = [float(x) for x in xobj.Matrix]

    logger.debug("Diving into Form XObject. Internal Matrix: %s", form_matrix)

    form_ctm = _multiply_matrices(form_matrix, current_ctm)
    form_resources = xobj.get("/Resources", parent_resources)

    _parse_stream(xobj, form_resources, form_ctm, image_list)


def _handle_do_operator(obj_name_node, resources, current_ctm, image_list, pikepdf):
    """Routes a drawn XObject to the Image or Form handler."""
    if resources is None or "/XObject" not in resources:
        logger.debug(
            "Do operator called for %s, but no /XObject dictionary found in resources.",
            obj_name_node,
        )
        return

    xobjects = resources["/XObject"]
    if obj_name_node not in xobjects:
        logger.debug("XObject %s not found in the /XObject resource dictionary.", obj_name_node)
        return

    xobj = xobjects[obj_name_node]
    subtype = str(xobj.get("/Subtype", ""))
    obj_name_str = str(obj_name_node)

    if subtype == "/Image":
        logger.debug("Successfully resolved %s as /Image.", obj_name_str)
        _extract_image_metadata(xobj, obj_name_str, current_ctm, image_list, pikepdf)
    elif subtype == "/Form":
        logger.debug("Successfully resolved %s as /Form. Executing nested parse.", obj_name_str)
        _process_form_xobject(xobj, resources, current_ctm, image_list)
    else:
        logger.debug("Ignored XObject %s with unhandled subtype %s.", obj_name_str, subtype)


def _parse_stream(content_stream, resources, initial_ctm, image_list):
    """Parses a content stream, tracks graphics state, and finds drawn XObjects."""
    ctm_stack = []
    current_ctm = list(initial_ctm)
    import pikepdf

    try:
        for inst in pikepdf.parse_content_stream(content_stream):
            op = str(inst.operator)
            if op == "q":
                ctm_stack.append(list(current_ctm))
            elif op == "Q":
                if ctm_stack:
                    current_ctm = ctm_stack.pop()
            elif op == "cm":
                operand_matrix = [float(x) for x in inst.operands]
                current_ctm = _multiply_matrices(operand_matrix, current_ctm)
            elif op == "Do":
                # Preserve the operand as a pikepdf.Name object for accurate dictionary lookup
                obj_name_node = inst.operands[0]
                logger.debug("Encountered 'Do' operator for operand: %s", obj_name_node)
                _handle_do_operator(obj_name_node, resources, current_ctm, image_list, pikepdf)

    except (pikepdf.PdfError, KeyError, TypeError, ValueError, AttributeError) as err:
        logger.warning("Error parsing content stream: %s", err)


def _extract_image_info(pdf: "pikepdf.Pdf", specs: list | None = None) -> dict:
    """Main entry point: Iterates pages and initiates extraction."""
    result: dict[str, list] = {"pages": []}
    num_pages = len(pdf.pages)

    if not specs:
        # If no page specs provided, process all pages
        target_pages = list(range(1, num_pages + 1))
    else:
        # Resolve page specs to specific page numbers
        target_pages = sorted(list(page_numbers_matching_page_specs(specs, num_pages)))

    logger.debug("Starting image extraction for %d pages.", len(target_pages))

    for page_num in target_pages:
        # target_pages are 1-indexed, but pdf.pages is 0-indexed
        page = pdf.pages[page_num - 1]
        page_info = {"page": page_num, "images": []}
        try:
            identity_ctm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
            _parse_stream(page, page.Resources, identity_ctm, page_info["images"])
        except AttributeError:
            logger.debug("Page %d skipped: no resources.", page_num)

        if page_info["images"]:
            result["pages"].append(page_info)
            logger.debug("Found %d images on Page %d.", len(page_info["images"]), page_num)

    logger.debug("Image extraction complete.")
    return result


def dump_images_cli_hook(result: OpResult, stage, _pipeline):
    """Writes the image data to stdout or a file in JSON."""
    output_file = from_result_meta(result, c.META_OUTPUT_FILE)

    with smart_open_maybe_dash(output_file) as file:
        json.dump(result.data, file, indent=2)
        file.write("\n")


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
    """
    Dump embedded image metadata of a PDF file.
    """
    image_data = _extract_image_info(pdf, specs)

    return OpResult(
        success=True,
        data=image_data,
        meta={
            c.META_OUTPUT_FILE: output_file,
        },
    )
