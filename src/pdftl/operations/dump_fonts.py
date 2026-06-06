# src/pdftl/operations/dump_fonts.py

"""Dump information about embedded and referenced fonts in a PDF file"""

import json
import logging
from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.fonts.font_extraction_utils import process_single_font
from pdftl.utils.hooks import from_result_meta
from pdftl.utils.io_helpers import smart_open_maybe_dash
from pdftl.utils.page_specs import page_numbers_matching_page_specs
from pdftl.utils.pdf_resources import get_all_fonts_recursive
from pdftl.utils.string_utils import compact_json_string

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)


_DUMP_FONTS_LONG_DESC = """
The `dump_fonts` operation extracts comprehensive structural and layout metadata
about both embedded and un-embedded fonts defined across the document's page resources.

Outputs a normalized JSON object grouping fonts by their internal object IDs, including:
* **name**: Raw PostScript name of the font exactly as it appears in the PDF (including subset
  prefix)
* **base_font**: Cleaned PostScript name of the font (e.g., Helvetica-Bold)
* **subtype**: The layout design specification style (e.g., TrueType, Type0, Type1, Type3)
* **is_embedded**: Boolean indicating if the binary font asset stream exists inside the PDF
* **font_bytes**: Actual compressed payload size of the embedded stream in bytes (0 if un-embedded)
* **is_subset**: True if the font has been structurally subsetted to reduce file size
* **encoding**: Character mapping sequence used (e.g., WinAnsiEncoding, Identity-H, Standard)
* **has_to_unicode**: True if a /ToUnicode translation CMap exists (crucial for reliable text
  extraction)
* **traits**: Decoded stylistic metadata dictionary extracted from the font's descriptor bitmask
* **metrics**: Extracted typography metrics (like ascent, descent, and italic angle), only
  including keys natively present in the PDF descriptor.
* **obj_id**: PDF indirect object reference index number
* **usages**: A dictionary mapping the local resource name (e.g., "F1") to an array of pages
  where it appears.

You can optionally provide page specifications to limit inspection to specific pages.
"""

_DUMP_FONTS_EXAMPLES = [
    {"cmd": "in.pdf dump_fonts", "desc": "Print font metadata for in.pdf to console"},
    {
        "cmd": "in.pdf dump_fonts output fonts.json",
        "desc": "Save font metadata for in.pdf to a file",
    },
    {
        "cmd": "in.pdf dump_fonts 1 2-4",
        "desc": "Print font metadata for pages 1, 2, 3, and 4",
    },
]


def _extract_font_info(pdf: "pikepdf.Pdf", specs: list | None = None) -> dict:
    """Iterates targeted pages and normalizes font metadata by their indirect object IDs."""
    num_pages = len(pdf.pages)

    if not specs:
        target_pages = list(range(1, num_pages + 1))
    else:
        target_pages = sorted(list(page_numbers_matching_page_specs(specs, num_pages)))

    logger.debug("Starting recursive font extraction for %d pages.", len(target_pages))

    aggregated_fonts = {}

    for local_name, font_obj, page_num in get_all_fonts_recursive(pdf, target_pages):
        try:
            font = process_single_font(local_name, font_obj)
            if not font:
                continue
        except (AttributeError, KeyError, TypeError, ValueError) as err:
            logger.warning("Error parsing Font on Page %d: %s", page_num, err)
            continue

        f_id = (
            str(font["obj_id"])
            if font.get("obj_id")
            else f"inline_{font['resource_name']}_{font['base_font']}"
        )

        if f_id not in aggregated_fonts:
            aggregated_fonts[f_id] = {
                "name": font["name"],
                "base_font": font["base_font"],
                "subtype": font["subtype"],
                "is_embedded": font["is_embedded"],
                "font_bytes": font["font_bytes"],
                "is_subset": font["is_subset"],
                "encoding": font["encoding"],
                "has_to_unicode": font["has_to_unicode"],
                "traits": font["traits"],
                "metrics": font["metrics"],
                "obj_id": font["obj_id"],
                "usages": {},
            }

        if local_name not in aggregated_fonts[f_id]["usages"]:
            aggregated_fonts[f_id]["usages"][local_name] = []

        if page_num not in aggregated_fonts[f_id]["usages"][local_name]:
            aggregated_fonts[f_id]["usages"][local_name].append(page_num)

    logger.debug("Font extraction complete. Found %d unique fonts.", len(aggregated_fonts))

    return {"fonts": list(aggregated_fonts.values())}


def dump_fonts_cli_hook(result: OpResult, _stage, _pipeline):
    """Writes compiled font metadata details out via the registered file streaming target."""
    output_file = from_result_meta(result, c.META_OUTPUT_FILE)

    with smart_open_maybe_dash(output_file) as file:
        compact_json = compact_json_string(json.dumps(result.data, indent=2), fold_dicts=False)
        file.write(compact_json + "\n")


@register_operation(
    "dump_fonts",
    tags=["info", "metadata", "fonts"],
    type="single input operation",
    desc="Extract font metadata",
    long_desc=_DUMP_FONTS_LONG_DESC,
    examples=_DUMP_FONTS_EXAMPLES,
    cli_hook=dump_fonts_cli_hook,
    usage="<input> dump_fonts [<spec>...] [output <output>]",
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {"output_file": c.OUTPUT}),
    skip_pipeline_save=True,
)
def dump_fonts(pdf, specs, output_file=None) -> OpResult:
    """
    Dump detailed font metadata records from a PDF file.
    """
    font_data = _extract_font_info(pdf, specs)

    return OpResult(
        success=True,
        data=font_data,
        meta={
            c.META_OUTPUT_FILE: output_file,
        },
    )
