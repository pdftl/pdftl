# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/fill_form.py

"""Fill in forms in a PDF"""

import logging
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Pdf

import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.types import OpResult
from pdftl.exceptions import UserCommandLineError
from pdftl.utils.fdf import wrap_fdf_data_in_pdf_bytes
from pdftl.utils.io_helpers import smart_open
from pdftl.utils.user_input import filename_completer

_FILL_FORM_LONG_DESC = """

The `fill_form` operation is used to fill in a form in a PDF.
The `<form_data>` can be the path to a file in FDF or XFDF format,
or `-`, or `PROMPT`.

**Warning** This is not very well tested; beware! Please
report bugs on GitHub, ideally with files to reproduce the
bugs.

"""

# _FILL_FORM_EXAMPLES = [
#     {
#         "cmd": "in.pdf fill_form data.fdf output out.pdf",
#         "desc": "Complete a form in in.pdf using data from data.fdf"
#     }
# ]


@register_operation(
    "fill_form",
    tags=["in_place", "forms", "alpha"],
    type="single input operation",
    desc="Fill a PDF form",
    long_desc=_FILL_FORM_LONG_DESC,
    usage="<input> fill_form <form_data> output <file> [<option>...]",
    #    examples=_FILL_FORM_EXAMPLES,  # FIXME
    args=([c.INPUT_PDF, c.OPERATION_ARGS, c.GET_INPUT], {}),
)
def fill_form(pdf: "Pdf", args: list[str], get_input: Callable = filename_completer) -> OpResult:
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

    try:
        with smart_open(data_file, "rb") as f:
            _fill_form_from_data(pdf, f.read())
    except OSError as exc:
        raise UserCommandLineError(exc) from exc

    return OpResult(success=True, pdf=pdf)


def _fill_form_from_data(pdf, data):
    """
    Fill in a form, using given data
    """
    from pikepdf.exceptions import PdfError
    from pikepdf.form import Form

    form = Form(pdf)

    try:
        _fill_form_from_fdf_data(form, data)
    except (PdfError, AttributeError, ValueError) as fdf_exc:
        try:
            logger.debug(
                "Got %s while trying to read data as FDF: %s", type(fdf_exc).__name__, fdf_exc
            )
            _fill_form_from_xfdf_data(form, data)
        except (PdfError, AttributeError, ValueError, NotImplementedError) as xfdf_exc:
            raise UserCommandLineError(
                "Errors encountered while processing FDF/XFDF data.\n"
                f"[FDF] {type(fdf_exc).__name__}: {fdf_exc}\n"
                f"[XFDF] {type(xfdf_exc).__name__}: {xfdf_exc}"
            ) from xfdf_exc


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
        value = fdf_field.V
        logger.debug("Got a hit")

        # Keep FDF specific logic for RadioButtons with 'Opt' lists
        if isinstance(field, RadioButtonGroup) and hasattr(field.obj, "Opt"):
            idx = next(x for x, y in enumerate(field.obj.Opt) if value == y)
            field.value = pikepdf.Name("/" + str(idx))
        else:
            # Use shared helper for everything else
            _set_form_field_value(field, value)


def _fill_form_from_xfdf_data(form, data):
    """Fill in a form, using given XFDF data"""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise ValueError(f"Invalid XFDF XML: {e}") from e

    # 1. Parse XFDF into a flat dictionary
    xfdf_data = {}

    def _recurse_xfdf(element, parent_name=""):
        for child in element:
            tag = child.tag.split("}", 1)[-1]
            if tag == "field":
                name = child.get("name")
                if not name:
                    continue
                full_name = f"{parent_name}.{name}" if parent_name else name

                # value_found = False
                for subchild in child:
                    if subchild.tag.split("}", 1)[-1] == "value":
                        xfdf_data[full_name] = subchild.text or ""
                        # value_found = True
                        break

                _recurse_xfdf(child, full_name)

    fields_element = None
    for child in root:
        if child.tag.split("}", 1)[-1] == "fields":
            fields_element = child
            break
    if fields_element is not None:
        _recurse_xfdf(fields_element)

    # 2. Fill fields
    for field in form:
        fq_name = field.fully_qualified_name

        if fq_name in xfdf_data:
            value = xfdf_data[fq_name]
            logger.debug("Filling %s = %s", fq_name, value)
            _set_form_field_value(field, value)


def _set_form_field_value(field, value):
    from pikepdf import Name

    if field.is_text:
        field.value = str(value)

    elif field.is_checkbox:
        # Map XFDF "Yes"/"On" string to boolean
        # Common valid "true" values in XFDF: "Yes", "On", "true", "1"
        is_checked = str(value).lower() in ("yes", "on", "true", "1")

        try:
            # Try the high-level pikepdf wrapper first (safe, verifies keys)
            field.checked = is_checked
        except AttributeError:
            # FALLBACK: The PDF is missing the /AP (Appearance) dictionary.
            # pikepdf fails because it can't lookup the "On" state name.
            # We must set the raw object values blindly.
            # Standard PDF checkboxes use "/Yes" for on and "/Off" for off.

            val = Name("/Yes") if is_checked else Name("/Off")

            # Update the Value (/V) and Appearance State (/AS)
            field.obj.V = val
            field.obj.AS = val

    elif field.is_radio_button:
        # Existing logic for radio buttons...
        # (You might want to apply similar try/except here if you encounter issues)
        val_str = str(value)
        if not val_str.startswith("/"):
            val_str = "/" + val_str
        # Workaround for pikepdf/qpdf crash on RadioGroups without /Kids
        # Maybe related? Not sure. https://github.com/qpdf/qpdf/issues/1449
        if "/Kids" not in field.obj:
            field.obj.V = Name(val_str)
        else:
            field.value = Name(val_str)

    else:
        # Fallback for other types
        field.value = str(value)


def fully_qualified_name(x, ancestors):
    """Return the fully qualified name (dot-separated
    coordinates starting from FDF object root) of an FDF object"""
    # FIXME! Is this good enough?
    return ".".join(map(str, [*ancestors, x.T]))
