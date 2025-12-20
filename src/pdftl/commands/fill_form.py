# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/fill_form.py

"""Fill in forms in a PDF"""

import logging
import sys

logger = logging.getLogger(__name__)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Pdf

from pdftl.core.registry import register_operation
from pdftl.exceptions import UserCommandLineError
from pdftl.utils.fdf import wrap_fdf_data_in_pdf_bytes
from pdftl.utils.user_input import filename_completer

_FILL_FORM_LONG_DESC = """

The `fill_form` operation is used to fill in a form in a PDF.
The `<form_data>` can be the path to a file in FDF or XFDF format,
or `-`, or `PROMPT`.

XFDF support is currently somewhere between flaky and non-existent.

"""

_FILL_FORM_EXAMPLES = [
    # {
    #     "cmd": "in.pdf fill_form data.fdf output out.pdf",
    #     "desc": "Complete a form in in.pdf using data from data.fdf"
    # }
]


@register_operation(
    "fill_form",
    tags=["in_place", "forms", "TODO", "alpha"],
    type="single input operation",
    desc="Fill a PDF form",
    long_desc=_FILL_FORM_LONG_DESC,
    usage="<input> fill_form <form_data> output <file> [<option>...]",
    examples=_FILL_FORM_EXAMPLES,
    args=(["input_pdf", "operation_args", "get_input"], {}),
)
def fill_form(pdf: "Pdf", args: [str], get_input: callable):
    """
    Fill in a form, treating the first argument as a filename (or similar) for data
    """
    if not args:
        args = ["PROMPT"]

    data_file = args[0]
    while not data_file or data_file == "PROMPT":
        data_file = get_input(
            "Enter a filename with FDF/XFDF input data: ", completer=filename_completer
        )

    # FIXME also handle xfdf
    try:
        with sys.stdin.buffer if data_file == "-" else open(data_file, "rb") as f:
            _fill_form_from_data(pdf, f.read())
    except OSError as exc:
        raise UserCommandLineError(exc) from exc

    return pdf


def _fill_form_from_data(pdf, data):
    """
    Fill in a form, using given data
    """
    from pikepdf.exceptions import PdfError
    from pikepdf.form import Form

    form = Form(pdf)

    try:
        _fill_form_from_fdf_data(form, data)
    except (PdfError, AttributeError, ValueError) as exc:
        try:
            logger.debug("Got %s while trying to read data as FDF: %s", type(exc).__name__, exc)
            _fill_form_from_xfdf_data(form, data)
        finally:
            raise UserCommandLineError(
                f"Error encountered while processing FDF/XFDF data: {exc}"
            ) from exc


def _fill_form_from_fdf_data(form, data):
    """Fill in a form, using given FDF data"""
    import pikepdf

    with pikepdf.open(wrap_fdf_data_in_pdf_bytes(data)) as wrapper_pdf:
        fdf_fields = wrapper_pdf.Root.FDF.Fields
        # logger.debug(fdf_fields)
        for fdf_field in fdf_fields:
            _fill_form_field_from_fdf_field(form, fdf_field)


def _fill_form_field_from_fdf_field(form, fdf_field, ancestors=None):
    """Fill in a form field, using given FDF field"""
    logger.debug("title=%s", getattr(fdf_field, "T", None))
    if ancestors is None:
        ancestors = []
    if hasattr(fdf_field, "V"):
        _fill_form_value_from_fdf_field(form, fdf_field, ancestors)
    if hasattr(fdf_field, "Kids"):
        logger.debug("title=%s has kids", getattr(fdf_field, "T", None))
        _process_fdf_field_kids(form, fdf_field, ancestors)


def _process_fdf_field_kids(form, fdf_field, ancestors):
    """Process kids of an FDF field recursively"""
    kid_ancestors = ancestors.copy()
    if hasattr(fdf_field, "T"):
        kid_ancestors.append(str(fdf_field.T))
    for fdf_field_kid in fdf_field.Kids:
        _fill_form_field_from_fdf_field(form, fdf_field_kid, kid_ancestors)


def _fill_form_value_from_fdf_field(form, fdf_field, ancestors):
    """Fill in a form value from an FDF field"""
    import pikepdf
    from pikepdf.form import RadioButtonGroup

    fully_qualified_fdf_name = fully_qualified_name(fdf_field, ancestors)
    logger.debug(fully_qualified_fdf_name)
    field = next((x for x in form if x.fully_qualified_name == fully_qualified_fdf_name), None)
    if field is not None:
        logger.debug("Got a hit")
        if isinstance(field, RadioButtonGroup):
            idx = next(x for x, y in enumerate(field.obj.Opt) if fdf_field.V == y)
            field.value = pikepdf.Name("/" + str(idx))
        else:
            field.value = str(fdf_field.V)


def _fill_form_from_xfdf_data(form, data):
    """Fill in a form, using given XFDF data"""
    raise NotImplementedError


def fully_qualified_name(x, ancestors):
    """Return the fully qualified name (dot-separated
    coordinates starting from FDF object root) of an FDF object"""
    # FIXME!
    return ".".join(map(str, [*ancestors, x.T]))
