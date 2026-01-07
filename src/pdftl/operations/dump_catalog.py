# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/dump_catalog.py

"""Dump PDF Catalog (Root) settings, including Initial View and Language."""

import json
import logging

logger = logging.getLogger(__name__)
import pdftl.core.constants as c
from pdftl.catalog.catalog_types import PdfCatalog
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.io_helpers import smart_open_output

_DUMP_CATALOG_LONG_DESC = """
Extracts document-level configuration from the PDF Catalog (Root dictionary).
This includes "Initial View" settings such as Page Layout, Page Mode, 
and Document Language.

Passing the `json` parameter produces a structured JSON output, ideal for 
automation or use with `update_catalog`.

### Output Fields (Stanza Format)

* `CatalogPageLayout`: SinglePage, OneColumn, TwoColumnLeft, etc.
* `CatalogPageMode`: UseNone, UseOutlines, UseThumbs, FullScreen, etc.
* `CatalogLang`: Natural language identifier (e.g., en-US).
* `CatalogViewerPreferences`: Nested settings like HideToolbar or CenterWindow.
"""

_DUMP_CATALOG_EXAMPLES = [
    {"cmd": "in.pdf dump_catalog", "desc": "Print Catalog settings in stanza format"},
    {"cmd": "in.pdf dump_catalog json", "desc": "Print Catalog settings in JSON format"},
]


def dump_catalog_cli_hook(result: OpResult, _stage):
    if result.meta is None:
        raise AttributeError("No result metadata")

    from pdftl.utils.hooks import from_result_meta

    output_file = from_result_meta(result, c.META_OUTPUT_FILE)
    json_output = result.meta.get(c.META_JSON_OUTPUT, False)
    with smart_open_output(output_file) as file:
        if json_output:
            # Merge stats into the dict for JSON output
            out_dict = result.data.to_dict()
            out_dict["PdfVersion"] = result.meta.get("pdf_version")
            out_dict["Encrypted"] = "Yes" if result.meta.get("encrypted") else "No"
            file.write(json.dumps(out_dict, indent=2))
            file.write("\n")
        else:
            file.write("CatalogBegin\n")
            # Print global stats first
            file.write(f"CatalogPdfVersion: {result.meta.get('pdf_version')}\n")
            file.write(f"CatalogEncrypted: {'Yes' if result.meta.get('encrypted') else 'No'}\n")

            # Print the rest of the catalog
            data_dict = result.data.to_dict()
            for k, v in data_dict.items():
                if isinstance(v, dict):
                    for sk, sv in v.items():
                        file.write(f"Catalog{k}{sk}: {sv}\n")
                else:
                    file.write(f"Catalog{k}: {v}\n")


@register_operation(
    "dump_catalog",
    tags=["info", "catalog"],
    type="single input operation",
    desc="Document-level Catalog and Initial View settings",
    long_desc=_DUMP_CATALOG_LONG_DESC,
    cli_hook=dump_catalog_cli_hook,
    usage="<input> dump_catalog [output <output>] [json]",
    examples=_DUMP_CATALOG_EXAMPLES,
    args=(
        [c.OPERATION_NAME, c.INPUT_PDF, c.INPUT_FILENAME, c.OPERATION_ARGS],
        {"output_file": c.OUTPUT},
    ),
)
def pdf_catalog_info(op_name, pdf, input_filename, op_args, output_file=None) -> OpResult:
    json_output = any(arg.lower() == "json" for arg in op_args)

    # pikepdf pdf.Root behaves like a dictionary
    raw_root = {str(k): v for k, v in pdf.Root.items()}
    catalog_data = PdfCatalog.from_dict(raw_root, pdf=pdf)

    # Add document-level stats to the metadata for the CLI hook
    return OpResult(
        success=True,
        pdf=pdf,
        data=catalog_data,
        is_discardable=True,
        meta={
            c.META_OUTPUT_FILE: output_file,
            c.META_JSON_OUTPUT: json_output,
            "pdf_version": pdf.pdf_version,
            "encrypted": pdf.is_encrypted,
        },
    )
