# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/dump_data_fields.py

"""Dump form data from a PDF file"""

import logging
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.utils.io_helpers import smart_open
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

    with smart_open(output_file) as f:
        for idx, field in enumerate(result.data):
            # pdftk prints the separator *before* the stanza
            print("---", file=f)
            _write_field_stanza(f, field, escape_xml)


def _map_type_to_pdftk_compat(pike_type_name):
    # pdftk calls all /Btn fields "Button", whereas pikepdf distinguishes them
    if pike_type_name.lower() in ("checkbox", "radiobtn", "pushbtn"):
        return "Button"
    # pdftk uses "Text" for /Tx, "Choice" for /Ch, etc.
    return pike_type_name


def _write_field_stanza(file_handle, field: dict, escape_xml: bool):
    """Writes the key-value pairs for a single field stanza."""

    def fmt(val):
        """Internal formatter to handle XML escaping."""
        s_val = str(val) if val is not None else ""
        return xml_encode_for_info(s_val) if escape_xml else s_val

    # 1. Identity & Names
    print(
        f"FieldType: {fmt(_map_type_to_pdftk_compat(field.get('FieldType', '')))}",
        file=file_handle,
    )
    if "FieldSubType" in field:
        print(f"FieldSubType: {fmt(field['FieldSubType'])}", file=file_handle)

    print(f"FieldName: {fmt(field.get('FieldName', ''))}", file=file_handle)
    if "FieldNameAlt" in field and field["FieldNameAlt"]:
        print(f"FieldNameAlt: {fmt(field.get('FieldNameAlt', ''))}", file=file_handle)

    # 2. Simple Attributes
    print(f"FieldFlags: {field.get('FieldFlags', 0)}", file=file_handle)

    if "FieldValue" in field and field["FieldValue"] is not None:
        print(f"FieldValue: {fmt(field['FieldValue'])}", file=file_handle)
    if "FieldValueDefault" in field and field["FieldValueDefault"] is not None:
        print(f"FieldValueDefault: {fmt(field['FieldValueDefault'])}", file=file_handle)

    # 3. Layout
    if "FieldJustification" in field:
        print(f"FieldJustification: {fmt(field['FieldJustification'])}", file=file_handle)

    # 4. Complex List Attributes (Options)
    if "FieldStateOption" in field:
        _write_field_options(file_handle, field["FieldStateOption"], fmt)


def _write_field_options(file_handle, options: list, fmt_func):
    """Handles the unique dual-printing of PDF field options."""
    lines: list[str | tuple] = []
    for opt in options:
        if isinstance(opt, (list, tuple)) and len(opt) == 2:
            # Case: (export_value, display_name)
            lines.append(
                (
                    f"FieldStateOption: {fmt_func(opt[0])}",
                    f"FieldStateOptionDisplay: {fmt_func(opt[1])}",
                )
            )
        else:
            # Case: Simple string option
            lines.append(f"FieldStateOption: {fmt_func(opt)}")

    # sorting breaks the strict PHP test, so let's not do it
    # lines.sort(key=lambda x: x[0] if isinstance(x, tuple) else x)

    for line in lines:
        if isinstance(line, tuple):
            print(line[0], file=file_handle)
            print(line[1], file=file_handle)
        else:
            print(line, file=file_handle)


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


def _extract_field_value(field, use_key=None) -> str | None:
    """Extracts the current value or appearance state of a field.

    use_key defaults to 'V', for the value.
    """
    import pikepdf

    # Standard value
    first_key = use_key or "V"
    if hasattr(field.obj, first_key):
        val = field.obj.get("/" + first_key)
        if isinstance(val, pikepdf.Name):
            return str(val).lstrip("/")
        return str(val)

    # Checkbox/Radio appearance fallback
    if hasattr(field.obj, "AS") and use_key is None:
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


def _extract_button_options(field) -> list:
    """
    Extracts options for Checkboxes and Radio groups by inspecting
    the Appearance (/AP) dictionaries of the field's widgets.

    Crucially, this uses a list to preserve the *discovery order* of options
    (traversing Kids then AP keys), which matches pdftk's behavior (e.g. "1", "Off", "2").
    """
    import pikepdf

    options_list = []
    seen = set()

    def add_opt(opt_name):
        s_opt = str(opt_name).lstrip("/")
        if s_opt not in seen:
            seen.add(s_opt)
            options_list.append(s_opt)

    def get_states_from_node(node):
        """Helper to safely extract keys from /AP/N, /AP/D, and /AP/R."""
        # Dictionary access via .get() is safer/more robust than getattr for raw pdf objs
        ap = node.get("/AP")
        if not ap:
            return

        # Check Normal (N), Down (D), and Rollover (R) appearances.
        for appearance_type in ["/N", "/D", "/R"]:
            sub_dict = ap.get(appearance_type)
            if isinstance(sub_dict, pikepdf.Dictionary):
                # SORTING FIX: Ensure deterministic output order by sorting keys.
                # This often yields "1", then "Off" (if "1" < "Off" alphabetically is false...
                # wait, "1" < "O". So "1" comes first. This matches pdftk behavior.)
                for k in sorted(sub_dict.keys()):
                    add_opt(k)

    # 1. Try children widgets (Radio Groups)
    # pdftk traverses Kids depth-first, collecting states as it finds them.
    if hasattr(field.obj, "Kids") and field.obj.Kids:
        for kid in field.obj.Kids:
            get_states_from_node(kid)

    # 2. Try the field itself (Checkboxes or single-widget buttons)
    # If it was a leaf node (Checkbox), or a parent holding the AP (rare for radios),
    # we check this. We only check this if we haven't found options yet, or to supplement.
    # For a standard Checkbox, this is where the options are found.
    get_states_from_node(field.obj)

    return options_list


def _extract_field_justification(field, field_type_out: str | None = None) -> str:
    """Determines the text alignment of a field (High Level or Raw)."""
    # If it's a pikepdf Field wrapper, access .obj, otherwise assume it's a raw dict
    obj = field.obj if hasattr(field, "obj") else field

    if hasattr(obj, "Q") or "/Q" in obj:
        align_map = ("Left", "Center", "Right")
        try:
            # Handle both raw dictionary lookup or attribute access
            q_val = int(obj.Q) if hasattr(obj, "Q") else int(obj["/Q"])
            return align_map[q_val]
        except (IndexError, ValueError, KeyError):
            return "Left"

    # default per PDF spec:
    return "Left"


def _extract_field_data_high_level(field, extra_info=False) -> OrderedDict[str, Any]:
    """
    Extracts data using pikepdf's high-level Form API.
    Used when we recognize the field object as a valid widget.
    """
    logger.debug("Extracting high level from %s", field)
    # 1. Basic Identity
    ts_in, ts_out = _get_field_type_strings(field)
    data = OrderedDict()
    data["FieldName"] = field.fully_qualified_name
    if hasattr(field.obj, "TU"):
        data["FieldNameAlt"] = field.obj.TU

    data["FieldType"] = ts_out

    if extra_info:
        data["FieldSubType"] = ts_in

    # 2. Add Optional Attributes
    if hasattr(field.obj, "Ff"):
        data["FieldFlags"] = int(field.obj.Ff)

    # 3. Value
    data["FieldValue"] = _extract_field_value(field)
    if hasattr(field.obj, "DV"):
        data["FieldValueDefault"] = _extract_field_value(field, use_key="DV")

    # 4. Options (Logic depends on type)
    if hasattr(field.obj, "Opt"):
        # Choice Fields (Combo/List) use explicit /Opt array
        data["FieldStateOption"] = _extract_field_options(field.obj.Opt)
    elif ts_out in ("Button", "Checkbox"):
        # Checkboxes/Radios use Appearance states
        btn_opts = _extract_button_options(field)
        if btn_opts:
            data["FieldStateOption"] = btn_opts

    # 5. Justification
    data["FieldJustification"] = _extract_field_justification(field, ts_out)

    return data


def _process_node_recursive(
    raw_obj, parent_name, smart_fields_map, output_list: list[dict[str, Any]], extra_info
):
    """
    Recursively walks the tree.
    Crucial Rule: If we find a High-Level Field, we Dump it and STOP recursing.
    """
    # 1. Determine Name
    # We must handle cases where /T is missing (common in intermediate nodes)
    local_name = str(raw_obj.get("/T", ""))
    if parent_name and local_name:
        full_name = f"{parent_name}.{local_name}"
    else:
        full_name = local_name or parent_name

    # 2. Check: Is this a Known High-Level Field?
    obj_num = getattr(raw_obj, "objgen", None)

    if obj_num is not None and obj_num in smart_fields_map:
        # CASE A: It is a valid Field (e.g., The Radio Group).
        # Action: Dump it using the smart API.
        smart_field = smart_fields_map[obj_num]
        data = _extract_field_data_high_level(smart_field, extra_info=extra_info)
        output_list.append(data)

        # CRITICAL FIX: Do NOT recurse into children.
        # pdftk treats this group as a leaf. We are done with this branch.
        return

    # 3. Recurse (Only if we didn't stop at Case A)
    if "/Kids" in raw_obj:
        for kid in raw_obj.Kids:
            _process_node_recursive(kid, full_name, smart_fields_map, output_list, extra_info)


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
    Extracts form field data from the PDF using a Hybrid Tree Walk.
    """
    from pikepdf.form import Form

    # 1. Initialize High-Level Map
    # Map raw object ID -> High-Level Field Wrapper
    form = Form(pdf)
    smart_fields_map = {}
    for field in form:
        if hasattr(field.obj, "objgen"):
            smart_fields_map[field.obj.objgen] = field

    # 2. Prepare for Walk
    all_fields_data: list[dict[str, Any]] = []

    try:
        acroform = pdf.Root.AcroForm
        fields_root = acroform.Fields
    except AttributeError:
        # No form data
        return OpResult(success=True, data=[], pdf=pdf, is_discardable=True)

    # 3. Start Recursive Walk (DFS)
    # We iterate the root array, passing each into our recursive processor
    for raw_obj in fields_root:
        _process_node_recursive(
            raw_obj,
            parent_name="",
            smart_fields_map=smart_fields_map,
            output_list=all_fields_data,
            extra_info=extra_info,
        )

    # 4. Return Structured Result
    return OpResult(
        success=True,
        data=all_fields_data,
        pdf=pdf,
        is_discardable=True,
    )
