# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/dump_data_fields.py

"""Dump form data from a PDF file"""

import logging
import sys

logger = logging.getLogger(__name__)

import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.utils.io_helpers import smart_open_output
from pdftl.utils.string import xml_encode_for_info

_DUMP_DATA_FIELDS_UTF8_LONG_DESC = """

Extracts data from all interactive form fields (AcroForm
fields) within the input PDF, identical to the
`dump_data_fields` operation, with one difference: all
string values (such as `FieldValue` or `FieldOptions`) are
written as raw UTF-8. No XML-style escaping is applied.

This output is for informational purposes. It is **not**
designed to be read by the `update_info` or
`update_info_utf8` operations.

For a complete description of the stanza format, see the
help for `dump_data_fields`.

"""

_DUMP_DATA_FIELDS_UTF8_EXAMPLES = [
    {
        "cmd": "Form.pdf dump_data_fields_utf8 output data.txt",
        "desc": "Save form field data for in.pdf to data.txt",
    }
]


_DUMP_DATA_FIELDS_LONG_DESC = """

Extracts data from all interactive form fields (AcroForm
fields) within the input PDF.

The output uses a stanza-based format similar to
`dump_data`, but is specific to form fields. All string
values (such as the field's content) are processed with
XML-style escaping.

This output is for informational purposes or for use in
external scripts. It is **not** designed to be read by the
`update_info` operation. To fill form fields, use the
`fill_form` operation.

### Field Stanza Format

Each field is represented by a single stanza.

* `FieldBegin`

* `FieldName: <full_field_name>`
  The unique identifying name of the field (e.g., `form1.name`).

* `FieldType: <Tx|Btn|Ch|Sig|...>`
  The AcroForm type
  (e.g., `Tx` for text, `Btn` for button, `Ch` for choice).

* `FieldValue: <current_value>`
  The current value of the field.

* `FieldFlags: <integer>`
  An integer representing a bitmask of field properties.

* `FieldJustification: <Left|Center|Right>`
  Text alignment for text fields.

* `FieldOptions: [<option1>, <option2>, ...]` (For Choice/List fields)
  A list of the available options for dropdowns or list boxes.
"""

_DUMP_DATA_FIELDS_EXAMPLES = [
    {
        "cmd": "in.pdf dump_data",
        "desc": "Print XML-escaped form field data for in.pdf",
    },
    {
        "cmd": "Form.pdf dump_data_fields output data.txt",
        "desc": "Save XML-escaped form field data for in.pdf to data.txt",
    },
]

# --- CLI Hook ---


def dump_fields_cli_hook(result, stage):
    """
    Formats the structured field data (List[Dict]) into the standard
    stanza-based text format (pdftk style).
    """
    if not result.data:
        return

    output_file = stage.options.get("output_file")
    escape_xml = stage.options.get("escape_xml", True)

    # Helper for conditional XML escaping
    def fmt(val):
        s_val = str(val)
        return xml_encode_for_info(s_val) if escape_xml else s_val

    with smart_open_output(output_file) as f:

        num_fields = len(result.data)

        for idx, field in enumerate(result.data):
            # 1. FieldType / SubType
            print(f"FieldType: {fmt(field.get('FieldType', ''))}", file=f)
            if "FieldSubType" in field:
                print(f"FieldSubType: {fmt(field['FieldSubType'])}", file=f)

            # 2. FieldName
            print(f"FieldName: {fmt(field.get('FieldName', ''))}", file=f)

            # 3. FieldFlags
            if "FieldFlags" in field:
                print(f"FieldFlags: {field['FieldFlags']}", file=f)

            # 4. FieldValue
            if "FieldValue" in field:
                print(f"FieldValue: {fmt(field['FieldValue'])}", file=f)

            # 5. FieldOptions (List behavior)
            if "FieldStateOption" in field:
                for opt in field["FieldStateOption"]:
                    # Option can be a string or a tuple (value, display_name)
                    if isinstance(opt, (list, tuple)) and len(opt) == 2:
                        print(f"FieldStateOption: {fmt(opt[0])}", file=f)
                        print(f"FieldStateOptionDisplay: {fmt(opt[1])}", file=f)
                    else:
                        print(f"FieldStateOption: {fmt(opt)}", file=f)

            # 6. Justification
            if "FieldJustification" in field:
                print(f"FieldJustification: {fmt(field['FieldJustification'])}", file=f)

            # Separator between stanzas (but not after the last one, to match pdftk)
            if idx + 1 < num_fields:
                print("---", file=f)


# --- Extraction Logic ---


def _get_field_type_strings(field):
    """Get a long and a short string representing the type of the field"""
    type_string_in = type(field).__name__
    if "button" in type_string_in.lower():
        type_string_out = "Button"
    elif type_string_in.endswith("Field"):
        type_string_out = type_string_in[:-5]
    else:
        # Fallback for unknown types
        type_string_out = type_string_in
    return type_string_in, type_string_out


def _extract_field_data(field, extra_info=False):
    """
    Extracts data from a single pikepdf field into a Python dictionary.
    """
    import pikepdf

    data = {}

    # 1. Type info
    ts_in, ts_out = _get_field_type_strings(field)
    data["FieldType"] = ts_out
    if extra_info:
        data["FieldSubType"] = ts_in

    # 2. Name
    data["FieldName"] = field.fully_qualified_name

    # 3. Flags
    if hasattr(field.obj, "Ff"):
        data["FieldFlags"] = int(field.obj.Ff)

    # 4. Value
    # The value is usually stored in /V.
    # For some button types (checkboxes), it might be /AS.
    if hasattr(field.obj, "V"):
        val = field.obj.V
        if isinstance(val, pikepdf.Name):
            val = str(val).lstrip("/")
        else:
            val = str(val)
        data["FieldValue"] = val
    elif hasattr(field.obj, "AS"):
        # For checkboxes/radios, appearance state often indicates value
        data["FieldValue"] = str(field.obj.AS).lstrip("/")

    # 5. Options
    if hasattr(field.obj, "Opt"):
        opts = []
        for opt in field.obj.Opt:
            if isinstance(opt, pikepdf.Array):
                # Tuple: (export_value, display_value)
                opts.append((str(opt[0]), str(opt[1])))
            else:
                opts.append(str(opt))
        data["FieldStateOption"] = opts

    # 6. Justification
    if hasattr(field.obj, "Q"):
        data["FieldJustification"] = ("Left", "Center", "Right")[int(field.obj.Q)]
    elif ts_out in ("Text", "Button"):
        data["FieldJustification"] = "Left"

    return data


# --- Operations ---


@register_operation(
    "dump_data_fields_utf8",
    tags=["info", "forms"],
    type="single input operation",
    desc="Print PDF form field data in UTF-8",
    long_desc=_DUMP_DATA_FIELDS_UTF8_LONG_DESC,
    examples=_DUMP_DATA_FIELDS_UTF8_EXAMPLES,
    cli_hook=dump_fields_cli_hook,
    usage="<input> dump_data_fields_utf8 [output <output>]",
    args=(
        [c.INPUT_PDF],
        {"output_file": c.OUTPUT},
        {"escape_xml": False},
    ),
)
@register_operation(
    "dump_data_fields",
    tags=["info", "forms"],
    type="single input operation",
    desc="Print PDF form field data with XML-style escaping",
    long_desc=_DUMP_DATA_FIELDS_LONG_DESC,
    examples=_DUMP_DATA_FIELDS_EXAMPLES,
    cli_hook=dump_fields_cli_hook,
    usage="<input> dump_data_fields [output <output>]",
    args=([c.INPUT_PDF], {"output_file": c.OUTPUT}, {"escape_xml": True}),
)
def dump_data_fields(
    pdf,
    output_file=None,
    escape_xml=True,
    extra_info=False,
) -> OpResult:
    """
    Extracts form field data from the PDF.

    Returns:
        OpResult:
            data: List[Dict] (Structured field data)
            pdf: pikepdf.Pdf (The input PDF)
    """
    from pikepdf.form import Form

    # 1. Extract Data
    form = Form(pdf)
    all_fields_data = []

    for field in form:
        field_data = _extract_field_data(field, extra_info=extra_info)
        all_fields_data.append(field_data)

    # 2. Return Structured Result
    # The cli_hook handles the text formatting and output_file writing.
    return OpResult(
        success=True,
        data=all_fields_data,
        pdf=pdf,
        is_discardable=True,
    )
