# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/generate_fdf.py

"""Generate FDF (fillable form data, or something) for a PDF file"""

import os

from pdftl.core.constants import FDF_END, FDF_START
from pdftl.core.registry import register_operation
from pdftl.utils.io_helpers import smart_open_output
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


@register_operation(
    "generate_fdf",
    tags=["info", "forms"],
    type="single input operation",
    desc="Generate an FDF file containing PDF form data",
    long_desc=_GENERATE_FDF_LONG_DESC,
    usage="<input> generate_fdf [output <output>]",
    examples=_GENERATE_FDF_EXAMPLES,
    args=(["input_pdf", "get_input"], {"output_file": "output"}),
)
def generate_fdf(pdf, get_input, output_file):
    """Output FDF data for the given PDF"""
    from pikepdf.form import Form

    if output_file == "PROMPT":
        output_file = None
    while not output_file or (
        os.path.exists(output_file)
        and get_input(f"File '{output_file}' exists. Overwrite? [y/N]: ").lower() != "y"
    ):
        output_file = get_input("Enter a filename for FDF output: ", completer=filename_completer)

    with smart_open_output(output_file, mode="wb") as file:
        file.write(FDF_START)

        form = Form(pdf)
        for field_name, field in form.items():
            _write_field_as_fdf_to_file(field_name, field, file)

        file.write(FDF_END)


def _write_field_as_fdf_to_file(field_name, field, file):
    """Write FDF data for a single field to a file"""

    from pikepdf import Name, String
    from pikepdf.form import ChoiceField, RadioButtonGroup

    def _write(x):
        _write_string_to_binary_file(x, file)

    _write(f"\n  %%% {type(field).__name__}")
    _write("\n  <<")
    _write(f"\n    /T ({field_name})")

    val = field.value
    if val is None:
        val = field.default_value
    if val is None and isinstance(field, ChoiceField):
        val = ""

    # pdftk seems to take an omitted value and insert '/V /'.
    # This feels like a bug...? Let's not do that.
    # # if val is None:
    # #     val = '/'
    # # else:
    # #     val = f"({val})"
    # # file.write(bytes(f"\n/V {val}\n>>", 'utf-8'))

    if isinstance(field, RadioButtonGroup) and isinstance(val, Name):
        val = field.obj.Opt[int(str(val)[1:])]

    val_as_string = None
    if isinstance(val, (String, str)):
        try:
            val_as_string = "(" + str(val) + ")"
        except ValueError:
            val_as_string = val.unparse()
    elif val is not None:
        val_as_string = str(val)

    if val_as_string is not None:
        _write(f"\n    /V {val_as_string}")

    _write("\n  >>")


def _write_string_to_binary_file(x, file):
    """Write a string to a binary file"""
    file.write(bytes(x, "utf-8"))
