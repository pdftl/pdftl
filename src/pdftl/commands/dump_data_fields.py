# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/dump_data_fields.py

"""Dump form data from a PDF file"""

import logging

logger = logging.getLogger(__name__)

from pdftl.core.registry import register_operation
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


@register_operation(
    "dump_data_fields_utf8",
    tags=["info", "forms"],
    type="single input operation",
    desc="Print PDF form field data in UTF-8",
    long_desc=_DUMP_DATA_FIELDS_UTF8_LONG_DESC,
    usage="<input> dump_data_fields_utf8 [output <output>]",
    examples=_DUMP_DATA_FIELDS_UTF8_EXAMPLES,
    args=(
        ["input_pdf"],
        {"output_file": "output"},
        {"escape_xml": False},
    ),
)
@register_operation(
    "dump_data_fields",
    tags=["info", "forms"],
    type="single input operation",
    desc="Print PDF form field data with XML-style escaping",
    long_desc=_DUMP_DATA_FIELDS_LONG_DESC,
    usage="<input> dump_data_fields [output <output>]",
    examples=_DUMP_DATA_FIELDS_EXAMPLES,
    args=(["input_pdf"], {"output_file": "output"}),
)
def dump_data_fields(
    pdf,
    output_file=None,
    escape_xml=True,
    extra_info=False,
):
    """
    Imitate pdftk's dump_data_fields output, writing to a file or stdout.
    """
    logger.debug("escape_xml=%s", escape_xml)

    with smart_open_output(output_file) as file:

        if escape_xml:

            def writer(text):
                print(xml_encode_for_info(text), file=file)

        else:

            def writer(text):
                print(text, file=file)

        write_fields(writer, pdf, extra_info=extra_info)


def _get_field_type_strings(field):
    """Get a long and a short string representing the type of the field"""
    type_string_in = type(field).__name__
    if "button" in type_string_in.lower():
        type_string_out = "Button"
    elif type_string_in.endswith("Field"):
        type_string_out = type_string_in[:-5]
    else:
        raise ValueError(f"Unknown field type: {type_string_in}")
    return type_string_in, type_string_out


def _write_field_types_and_name(writer, field, type_string_in, type_string_out, extra_info):
    """Write field types and name for dump_data_fields"""
    writer(f"FieldType: {type_string_out}")
    if extra_info:
        writer(f"FieldSubType: {type_string_in}")
    writer(f"FieldName: {field.fully_qualified_name}")


def _write_field_flags(writer, field):
    """Write field flags for dump_data_fields"""
    if hasattr(field.obj, "Ff"):
        writer(f"FieldFlags: {field.obj.Ff}")


def _write_field_value(writer, field):
    """Write field value for dump_data_fields"""
    import pikepdf

    # The value is usually stored in /V.
    # For some button types (checkboxes), it might be /AS or require resolving.
    # We check the raw object first.
    if hasattr(field.obj, "V"):
        # Convert pikepdf object to string safely
        val = str(field.obj.V)
        # Handle Name objects (remove slash) if necessary, though str() usually keeps it.
        # pdftk usually strips the slash for Name objects.
        if isinstance(field.obj.V, pikepdf.Name):
            val = str(field.obj.V).lstrip("/")
        writer(f"FieldValue: {val}")
    elif hasattr(field.obj, "AS"):
        # For checkboxes/radios, appearance state often indicates value
        val = str(field.obj.AS).lstrip("/")
        writer(f"FieldValue: {val}")


def _write_field_options(writer, field):
    """Write field options for dump_data_fields"""
    import pikepdf

    if hasattr(field.obj, "Opt"):
        for opt in field.obj.Opt:
            if isinstance(opt, pikepdf.Array):
                writer(f"FieldStateOption: {opt[0]}")
                writer(f"FieldStateOptionDisplay: {opt[1]}")
            else:
                writer(f"FieldStateOption: {opt}")


def _write_field_justification(writer, field, short_type_string):
    """Write field justification for dump_data_fields"""
    if hasattr(field.obj, "Q"):
        writer(f"FieldJustification: {('Left', 'Center', 'Right')[int(field.obj.Q)]}")
    elif short_type_string in ("Text", "Button"):
        writer("FieldJustification: Left")


def write_fields(writer, pdf, extra_info):
    """Write form field info"""
    from pikepdf.form import Form

    form = Form(pdf)

    num_fields = len(list(form.items()))
    for idx, field in enumerate(form):
        type_strings = _get_field_type_strings(field)
        _write_field_types_and_name(writer, field, *type_strings, extra_info)
        if hasattr(field, "obj"):
            _write_field_flags(writer, field)
            _write_field_value(writer, field)
            _write_field_options(writer, field)
            _write_field_justification(writer, field, type_strings[1])
        if idx + 1 < num_fields:
            writer("---")
