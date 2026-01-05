# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/dump_data_fields.py

"""Dump form data from a PDF file"""

import logging

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
    """Formats structured field data into the standard stanza-based text format."""
    if not result.data:
        return

    output_file = stage.options.get("output_file")
    escape_xml = stage.options.get("escape_xml", True)

    with smart_open_output(output_file) as f:
        num_fields = len(result.data)

        for idx, field in enumerate(result.data):
            # Pass the field and the formatting preferences to the helper
            _write_field_stanza(f, field, escape_xml)

            # Standard pdftk-style separator
            if idx + 1 < num_fields:
                print("---", file=f)


def _write_field_stanza(file_handle, field: dict, escape_xml: bool):
    """Writes the key-value pairs for a single field stanza."""

    def fmt(val):
        """Internal formatter to handle XML escaping."""
        s_val = str(val) if val is not None else ""
        return xml_encode_for_info(s_val) if escape_xml else s_val

    # 1. Identity & Names
    print(f"FieldType: {fmt(field.get('FieldType', ''))}", file=file_handle)
    if "FieldSubType" in field:
        print(f"FieldSubType: {fmt(field['FieldSubType'])}", file=file_handle)

    print(f"FieldName: {fmt(field.get('FieldName', ''))}", file=file_handle)

    # 2. Simple Attributes
    if "FieldFlags" in field:
        print(f"FieldFlags: {field['FieldFlags']}", file=file_handle)

    if "FieldValue" in field:
        print(f"FieldValue: {fmt(field['FieldValue'])}", file=file_handle)

    # 3. Complex List Attributes (Options)
    if "FieldStateOption" in field:
        _write_field_options(file_handle, field["FieldStateOption"], fmt)

    # 4. Layout
    if "FieldJustification" in field:
        print(f"FieldJustification: {fmt(field['FieldJustification'])}", file=file_handle)


def _write_field_options(file_handle, options: list, fmt_func):
    """Handles the unique dual-printing of PDF field options."""
    for opt in options:
        if isinstance(opt, (list, tuple)) and len(opt) == 2:
            # Case: (export_value, display_name)
            print(f"FieldStateOption: {fmt_func(opt[0])}", file=file_handle)
            print(f"FieldStateOptionDisplay: {fmt_func(opt[1])}", file=file_handle)
        else:
            # Case: Simple string option
            print(f"FieldStateOption: {fmt_func(opt)}", file=file_handle)


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
    """Extracts data from a single pikepdf field into a Python dictionary."""
    # 1. Basic Identity
    ts_in, ts_out = _get_field_type_strings(field)
    data = {
        "FieldName": field.fully_qualified_name,
        "FieldType": ts_out,
    }

    if extra_info:
        data["FieldSubType"] = ts_in

    # 2. Add Optional Attributes
    if hasattr(field.obj, "Ff"):
        data["FieldFlags"] = int(field.obj.Ff)

    # 3. Use specialized helpers for complex logic
    data["FieldValue"] = _extract_field_value(field)

    if hasattr(field.obj, "Opt"):
        data["FieldStateOption"] = _extract_field_options(field.obj.Opt)

    data["FieldJustification"] = _extract_field_justification(field, ts_out)

    return data


def _extract_field_value(field) -> str | None:
    """Extracts the current value or appearance state of a field."""
    import pikepdf

    # Standard value
    if hasattr(field.obj, "V"):
        val = field.obj.V
        if isinstance(val, pikepdf.Name):
            return str(val).lstrip("/")
        return str(val)

    # Checkbox/Radio appearance fallback
    if hasattr(field.obj, "AS"):
        return str(field.obj.AS).lstrip("/")

    return None


def _extract_field_options(opt_array) -> list:
    """Parses PDF choice field options into strings or (export, display) tuples."""
    import pikepdf

    opts: list[str | tuple] = []
    for opt in opt_array:
        if isinstance(opt, pikepdf.Array):
            # Format: (export_value, display_value)
            opts.append((str(opt[0]), str(opt[1])))
        else:
            opts.append(str(opt))
    return opts


def _extract_field_justification(field, field_type_out: str) -> str | None:
    """Determines the text alignment of a field."""
    if hasattr(field.obj, "Q"):
        align_map = ("Left", "Center", "Right")
        try:
            return align_map[int(field.obj.Q)]
        except (IndexError, ValueError):
            return "Left"

    if field_type_out in ("Text", "Button"):
        return "Left"

    return None


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
