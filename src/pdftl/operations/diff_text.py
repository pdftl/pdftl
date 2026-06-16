# src/pdftl/operations/diff_text.py

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Diff the text content of two PDFs using precise native mapping."""

from __future__ import annotations

import io
import json
import logging
from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.utils.dependencies import ensure_dependencies
from pdftl.utils.hooks import text_dump_hook
from pdftl.utils.keyval_parser import parse_keyval_list
from pdftl.utils.string_utils import compact_json_string
from pdftl.utils.pdf_text.global_stream_mapper import GlobalStreamMapper
from pdftl.utils.pdf_text.text_diff_calc import compute_diff_chunks, process_diff_stream


if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)

LONG_DESC = """
Performs a highly granular, spatially-aware text comparison between two PDFs.
Outputs a JSON array of structural changes, including precise bounding box
coordinates for where the changes occurred on the page.

### Options

* **`granularity=<level>`**: Controls how the diff engine groups text before comparing. Options:
    `char`, `word`, `line`, `paragraph`. Using `word` prevents sub-word shredding on
    typos. *(Default: word)*

* **`ignore_whitespace=<bool>`**: If true, drops changes where the only difference is invisible
    space (e.g., reflow line-breaks). *(Default: true)*

* **`ignore_soft_hyphens=<bool>`**: If true, strips `\\ufffe` soft hyphens before comparing. Useful
    for ignoring hyphenation differences caused by text reflowing across margins. *(Default:
    false)*


* **`include_bboxes=<bool>`**: If true, includes spatial bounding box coordinates for every
    change. Turn this off for a cleaner, text-only JSON output. *(Default: true)*

* **`margin_top=<float>`**, **`margin_bottom=<float>`**, **`margin_left=<float>`**,
    **`margin_right=<float>`**: Filters out changes that fall entirely within these margins (in
    points). Excellent for removing noisy page headers, footers, or marginalia. *(Default: 0)*

"""

# --- Core Operation Entrypoint ---


@register_operation(
    "diff_text",
    tags=["text", "compare", "utility"],
    cli_hook=text_dump_hook,
    type="single input operation",
    desc="Diff the text content of two PDFs and output bounding boxes",
    long_desc=LONG_DESC,
    usage="<input_a> diff_text <input_b> [options...] [output <output>]",
    args=([c.INPUT_PDF, c.OVERLAY_PDF, c.OPERATION_ARGS], {"output_file": c.OUTPUT}),
    skip_pipeline_save=True,
)
def diff_text(
    pdf_a: pikepdf.Pdf, file_b_path: str, operation_args: list[str], output_file=None
) -> OpResult:
    ensure_dependencies(
        feature_name="diff_text",
        dependencies=["pypdfium2", "diff_match_patch"],
        extra_tag="diff-text",
    )
    import pypdfium2 as pdfium
    from pdftl.utils.pdf_text.text_provider import TextProvider

    _kv = parse_keyval_list(operation_args, bare_tokens=True, context="diff_text")
    granularity = _kv.get("granularity", "word").lower()
    ignore_whitespace = _kv.get("ignore_whitespace", "true").lower() in ("true", "1", "yes")
    ignore_soft_hyphens = _kv.get("ignore_soft_hyphens", "false").lower() in ("true", "1", "yes")
    include_bboxes = _kv.get("include_bboxes", "true").lower() in ("true", "1", "yes")
    merge_bboxes = _kv.get("merge_bboxes", "true").lower() in ("true", "1", "yes")

    margins = {
        "top": float(_kv.get("margin_top", 0)),
        "bottom": float(_kv.get("margin_bottom", 0)),
        "left": float(_kv.get("margin_left", 0)),
        "right": float(_kv.get("margin_right", 0)),
    }

    logger.debug("Generating clean byte streams for text extraction...")

    pdf_a_bytes = io.BytesIO()
    pdf_a.save(pdf_a_bytes)
    pdf_a_bytes.seek(0)

    doc_a = pdfium.PdfDocument(pdf_a_bytes)
    doc_b = pdfium.PdfDocument(file_b_path)

    tp_a = TextProvider(pdf_path="", opened_pdfium_doc=doc_a)
    tp_b = TextProvider(pdf_path="", opened_pdfium_doc=doc_b)

    try:
        mapper_a = GlobalStreamMapper(tp_a, len(doc_a), doc_a, margins)
        mapper_b = GlobalStreamMapper(tp_b, len(doc_b), doc_b, margins)

        logger.debug("Executing text diff sequence mapping...")
        diff_chunks = compute_diff_chunks(
            mapper_a.full_stream, mapper_b.full_stream, granularity, ignore_soft_hyphens
        )

        change_records = process_diff_stream(
            diff_chunks, mapper_a, mapper_b, ignore_whitespace, include_bboxes, merge_bboxes
        )
    finally:
        tp_a.close()
        tp_b.close()

    output_data = {
        "summary": {
            "total_changes": len(change_records),
        },
        "changes": change_records,
    }

    json_output = compact_json_string(json.dumps(output_data, indent=2), fold_dicts=False)
    return OpResult(success=True, data=json_output)
