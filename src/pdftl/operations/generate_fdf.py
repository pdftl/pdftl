# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/generate_fdf.py

"""Generate FDF (fillable form data, or something) for a PDF file"""

import os

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.utils.hooks import from_result_meta
from pdftl.utils.io_helpers import smart_open
from pdftl.utils.user_input import filename_completer

_GENERATE_FDF_LONG_DESC = """

Generate an FDF file containing PDF form data.
`<output>` can be a filename, or `-` to output on stdout,
or `PROMPT` to prompt for a filename.

"""

_GENERATE_FDF_EXAMPLES = [
    {
        "cmd": "in.pdf generate_fdf output -",
        "desc": "Dump FDF data for in.pdf to standard output",
    },
]


def generate_fdf_cli_hook(result: OpResult, stage, _pipeline):
    """
    CLI Hook for generate_fdf.
    Writes the FDF data bytes to the output file.
    """
    if not result.success:
        return

    output_file = from_result_meta(result, c.META_OUTPUT_FILE)
    if output_file == "-":
        output_file = None

    # Open in binary mode ('wb') to write the raw bytes directly
    with smart_open(output_file, mode="wb") as f:
        import shutil

        # result.data is an io.BytesIO object.
        # shutil.copyfileobj should be memory efficient
        shutil.copyfileobj(result.data, f)


@register_operation(
    "generate_fdf",
    tags=["info", "forms"],
    cli_hook=generate_fdf_cli_hook,
    type="single input operation",
    desc="Generate an FDF file containing PDF form data",
    long_desc=_GENERATE_FDF_LONG_DESC,
    usage="<input> generate_fdf [output <output>]",
    examples=_GENERATE_FDF_EXAMPLES,
    args=([c.INPUT_PDF, c.GET_INPUT], {"output_file": c.OUTPUT}),
    skip_pipeline_save=True,
)
def generate_fdf(pdf, get_input, output_file, status=None) -> OpResult:
    """Output FDF data for the given PDF"""
    from pikepdf.form import Form

    if output_file == "PROMPT":
        output_file = None
    while not output_file or (
        os.path.exists(output_file)
        and get_input(f"File '{output_file}' exists. Overwrite? [y/N]: ").lower() != "y"
    ):
        output_file = get_input("Enter a filename for FDF output: ", completer=filename_completer)

    import io

    buffer = io.BytesIO()
    buffer.write(c.FDF_START)  # FDF_START is bytes

    # ISO 32000-2 Table 246: Optional status string displaying transaction results
    if status is not None:
        buffer.write(f"\n  /Status ({status})".encode("latin-1"))

    form = Form(pdf)
    for field_name, field in form.items():
        _write_field_as_fdf_to_file(field_name, field, buffer)
    buffer.write(c.FDF_END)
    buffer.seek(0)

    return OpResult(success=True, data=buffer, meta={c.META_OUTPUT_FILE: output_file})


def _write_string_to_binary_file(x, file):
    """
    Encodes as latin-1 to match pdftk's default behavior.
    Fails over to utf-8 only if absolutely necessary.
    """
    try:
        file.write(x.encode("latin-1"))
    except UnicodeEncodeError:
        # If the string contains chars latin-1 can't handle,
        # pdftk usually uses UTF-16BE with a BOM, but UTF-8 is a safer fallback
        file.write(x.encode("utf-8"))


def _write_field_as_fdf_to_file(field_name, field, file):
    """Write FDF data for a single field to a file"""

    def _write(x):
        _write_string_to_binary_file(x, file)

    _write(f"\n  %%% {type(field).__name__}")
    _write("\n  <<")
    _write(f"\n    /T ({field_name})")

    val_as_string = _get_val_as_string(field)

    if val_as_string is not None:
        _write(f"\n    /V {val_as_string}")

    _write("\n  >>")


def _get_val_as_string(field):
    from pikepdf import Array, Name, String
    from pikepdf.form import CheckboxField, RadioButtonGroup

    val = getattr(field, "value", None)
    if val is None:
        val = getattr(field, "default_value", None)

    # Bypass pikepdf stringification for Arrays
    if hasattr(field, "obj"):
        raw_v = field.obj.get("/V")
        if isinstance(raw_v, Array):
            val = raw_v

    val_as_string = None

    if val is None:
        val_as_string = _val_string_from_none(field)

    # ISO 32000-2 §12.7.5.4 Choice Multi-Select Array support
    elif isinstance(val, (list, Array)):
        val_as_string = _val_string_from_array(val)

    elif isinstance(field, (RadioButtonGroup, CheckboxField)):
        # State-based fields use Name format (/Value)
        s_val = str(val)
        val_as_string = s_val if s_val.startswith("/") else f"/{s_val}"

    elif isinstance(val, (String, Name)):
        val_as_string = _val_string_from_stringy(val)
    else:
        # Standard fallback for basic types
        val_as_string = f"({val})"

    return val_as_string


def _val_string_from_array(val):
    from pikepdf import Name, String

    items_str = []
    for v in val:
        if isinstance(v, (String, str)):
            items_str.append(f"({str(v)})")
        elif isinstance(v, Name):
            s_val = str(v)
            items_str.append(s_val if s_val.startswith("/") else f"/{s_val}")
        else:
            items_str.append(f"({str(v)})")
    return f"[{' '.join(items_str)}]"


def _val_string_from_none(field):
    from pikepdf.form import RadioButtonGroup

    # Robustness: getattr protects against mocks or non-standard objects
    field_obj = getattr(field, "obj", {})
    is_radio = isinstance(field, RadioButtonGroup)
    is_merged_widget = field_obj.get("/Subtype") == "/Widget"

    if is_radio and not is_merged_widget:
        return "/Off"

    return "/"


def _val_string_from_stringy(val):
    from pikepdf import Name, String

    # Robustness: try/except catches binary data that can't be stringified
    try:
        s_val = str(val)
        # Maintain pdftk style: Strings get parens, Names get slashes
        if isinstance(val, String):
            val_as_string = f"({s_val})"
        elif isinstance(val, Name):
            # Ensure we don't double up slashes (e.g. //Yes)
            val_as_string = s_val if s_val.startswith("/") else f"/{s_val}"
        else:
            # Fallback for the theoretical edge case where the tuple check passed
            # but individual checks failed (defensive coding)
            val_as_string = f"({val})"
    except (ValueError, UnicodeDecodeError):
        # Fallback to pikepdf's hex encoding <...> for binary safety
        val_as_string = val.unparse()

    return val_as_string
