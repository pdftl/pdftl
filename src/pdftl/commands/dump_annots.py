# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/dump_annots.py

"""Dump annotations info, in JSON"""

import json
import logging

logger = logging.getLogger(__name__)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Pdf

from pdftl.core.registry import register_operation
from pdftl.utils.io_helpers import smart_open_output
from pdftl.utils.json import pdf_obj_to_json
from pdftl.utils.string import compact_json_string, xml_encode_for_info

_DUMP_ANNOTS_LONG_DESC = """

The `dump_annots` operation extracts and prints information about
annotations within the PDF file.

"""

_DUMP_ANNOTS_EXAMPLES = [
    {
        "cmd": "in.pdf dump_annots",
        "desc": "Show annotation data for a file:",
    }
]


@register_operation(
    "dump_annots",
    tags=["in_place", "annotations", "info"],
    type="single input operation",
    desc="Dump annotation info",
    long_desc=_DUMP_ANNOTS_LONG_DESC,
    usage="<input> dump_annots [output <output>]",
    examples=_DUMP_ANNOTS_EXAMPLES,
    args=(["input_pdf"], {"output_file": "output"}),
)
def dump_annots(pdf, output_file=None):
    """
    Dumps all annotations from a PDF in JSON format, with compact arrays.
    """
    logger.debug("Dumping annotations for PDF with %s pages.", len(pdf.pages))
    all_annots_data = _get_all_annots_data(pdf)
    json_string = json.dumps(all_annots_data, indent=2)

    with smart_open_output(output_file) as file:
        print(compact_json_string(json_string), file=file)


_DUMP_DATA_ANNOTS_LONG_DESC = """

The `dump_data_annots` operation extracts and prints
information about annotations within the PDF file in the style of
`pdftk`.

"""

_DUMP_DATA_ANNOTS_EXAMPLES = [
    {
        "cmd": "in.pdf dump_data_annots",
        "desc": "Show annotation data for a file:",
    }
]


@register_operation(
    "dump_data_annots",
    tags=["in_place", "annotations", "info"],
    type="single input operation",
    desc="Dump annotation info in pdftk style",
    long_desc=_DUMP_DATA_ANNOTS_LONG_DESC,
    usage="<input> dump_data_annots [output <output>]",
    examples=_DUMP_DATA_ANNOTS_EXAMPLES,
    args=(["input_pdf"], {"output_file": "output"}),
)
def dump_data_annots(pdf, output_file=None, string_convert=xml_encode_for_info):
    """
    Dumps annotation data from a PDF in pdftk style
    """
    logger.debug("Dumping pdftk-style annotations data for PDF with %s pages.", len(pdf.pages))
    all_annots_data = _get_all_annots_data(pdf)
    data_strings = _data_to_strings(all_annots_data, string_convert)
    uri_line = ""
    if base := getattr(getattr(getattr(pdf, "Root", {}), "URI", {}), "Base", None):
        uri_line = f"\nPdfUriBase: {string_convert(str(base))}"
    data_strings = [f"NumberOfPages: {len(pdf.pages)}" + uri_line] + data_strings

    with smart_open_output(output_file) as file:
        print("\n---\n".join(data_strings), file=file)


##################################################


def _get_all_annots_data(pdf: "Pdf"):
    """Get all annotations data for a PDF"""
    from pikepdf import Name, NameTree

    page_object_to_num_map = {p.obj.objgen: i + 1 for i, p in enumerate(pdf.pages)}
    named_dests = {}
    if Name.Names in pdf.Root and Name.Dests in pdf.Root.Names:
        named_dests = dict(NameTree(pdf.Root.Names.Dests))
    all_annots_data = []
    for page_num, page in enumerate(pdf.pages, 1):
        all_annots_data.extend(
            _annots_json_for_page(page, page_num, page_object_to_num_map, named_dests)
        )
    return all_annots_data


def _data_to_strings(data, string_convert):
    """Convert data to strings for dump_data"""
    logger.debug(data)
    data_strings = []
    for datum in data:
        new_lines = _lines_from_datum(datum, string_convert)
        data_strings.append("\n".join(new_lines))
    return data_strings


##################################################


def _annots_json_for_page(page, page_num, page_object_to_num_map, named_dests):
    """Return annotations info for one page, in JSON"""
    return [
        {
            "Page": page_num,
            "AnnotationIndex": i + 1,
            "Properties": pdf_obj_to_json(annot, page_object_to_num_map, named_dests),
        }
        for i, annot in enumerate(getattr(page, "Annots", []))
    ]


def _lines_from_datum(datum, string_convert):
    """Get lines from one data entry, for dump_annots"""
    new_lines = []
    props = datum["Properties"]
    prefix = "Annot"
    # if 'JavaScript' in str(props):
    #     breakpoint()
    if "/Subtype" not in props:
        return []
    if props["/Subtype"][1:] not in (
        "FreeText",
        "Link",
        "Popup",
        "Square",
        "Text",
        "URI",
        "Widget",
        "FileAttachment",
    ):
        return []
    if "/A" in props and "/S" in props["/A"] and props["/A"]["/S"][1:] == "JavaScript":
        return []
    for key, value in props.items():
        new_lines.extend(_key_value_lines(key, value, prefix, string_convert))
    new_lines.extend(
        [
            _data_item_to_string_helper("PageNumber", datum["Page"], prefix, string_convert),
            _data_item_to_string_helper(
                "IndexInPage", datum["AnnotationIndex"], prefix, string_convert
            ),
        ]
    )
    return new_lines


def _key_value_lines(key, value, prefix, string_convert):
    """Convert a key-value pair to strings for dump_annots"""
    if key == "/A":
        return [
            _data_item_to_string_helper(key2, value2, prefix + "Action", string_convert)
            for key2, value2 in value.items()
        ]
    if key in ("/Type", "/Border") or len(key) < 4:
        return []
    try:
        return [_data_item_to_string_helper(key, value, prefix, string_convert)]
    except NotImplementedError as exc:
        logger.warning(exc)
        return []


def _data_item_to_string_helper(key, value, prefix, string_convert):
    """Helper method to convert a data item to a string"""
    if string_convert is None:

        def string_convert(x):
            return x

    if isinstance(value, str) and value.startswith("/"):
        value = value[1:]
    if isinstance(key, str) and key.startswith("/"):
        key = key[1:]
    if key == "S":
        key = "Subtype"
    # if isinstance(value, Object):
    #     value_string = pdf_obj_to_string(value)
    # else:
    value_string = str(value)
    value_string = value_string.replace("'", "").replace("[", "").replace("]", "")
    return f"{prefix}{key}: {string_convert(value_string)}"
