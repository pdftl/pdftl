# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/modify_annots.py

"""Modify properties of existing annotations"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Pdf

from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError

from .parsers.modify_annots_parser import specs_to_modification_rules

_MODIFY_ANNOTS_LONG_DESC = """

Modifies properties of existing annotations (e.g., links, highlights)
on specified pages.

This operation allows for targeted, surgical changes to annotation
dictionaries, such as changing a link's border or a highlight's color.

The syntax is `selector(Key=Value, ...)`, where:
  - `selector` is a page range (e.g., `1-5`, `odd`, see [[`page_specs`]]) and/or an
    annotation type (e.g., `/Link`, `/Highlight`).
  - `Key=Value` pairs define the PDF dictionary keys to set and
    their new values.

### Value Syntax
  - PDF Names: `/Name`
  - PDF Strings: `(My String)`
  - PDF Arrays: `[0 0 1]`
  - PDF Booleans: `true` / `false`
  - Null (to delete a key): `null`
  - Numbers: `1.5`, `10`
  - Plain strings are treated as PDF Strings: `Value` is interpreted as `(Value)`

"""

_MODIFY_ANNOTS_EXAMPLES = [
    {
        "cmd": 'in.pdf modify_annots "1-end/Link(Border=[0 0 0])" output out.pdf',
        "desc": "Remove the visible border from all links in the document.",
    },
    {
        "cmd": 'in.pdf modify_annots "odd/Highlight(C=[1 0 0])" output out.pdf',
        "desc": "Change the color of all highlights on odd pages to red.",
    },
    {
        "cmd": "in.pdf modify_annots \"/Text(T='(New Author)')\" output out.pdf",
        "desc": "Set the 'Title' (author) of all text notes to 'New Author'.",
    },
    {
        "cmd": 'in.pdf modify_annots "1-5(MyKey=null)" output out.pdf',
        "desc": "Delete the custom key '/MyKey' from all annotations on pages 1-5.",
    },
]


def _parse_array_value(arr_str: str) -> list:
    """Parses a string like '[0 0 1]' into a list of numbers/strings."""
    items = arr_str[1:-1].strip().split()
    py_items: list[Any] = []
    for item in items:
        try:
            # Try parsing as float, but only if it looks like a number
            # (prevents "1.2.3" from being parsed as "1.2")
            if item.count(".") <= 1 and item.replace(".", "", 1).lstrip("-+").isdigit():
                py_items.append(float(item))
            else:
                py_items.append(item)  # Add as string
        except ValueError:
            py_items.append(item)  # Add as string
    return py_items


def _parse_value_to_python(val_str: str):
    """
    Converts a value string from the parser into a Python type that
    pikepdf can use in its high-level API.
    """
    from pikepdf import Name

    val_str = val_str.strip()

    static_values = {
        "null": None,
        "true": True,
        "false": False,
    }
    if val_str in static_values:
        return static_values[val_str]

    if val_str.startswith("(") and val_str.endswith(")"):
        # Validate balanced parentheses
        if val_str.count("(") != val_str.count(")"):
            raise ValueError(f"Mismatched parentheses in string: '{val_str}'")
        # PDF Literal String -> Python String
        return val_str[1:-1]
    if val_str.startswith("[") and val_str.endswith("]"):
        # Validate balanced brackets
        if val_str.count("[") != val_str.count("]"):
            raise ValueError(f"Mismatched brackets in array: '{val_str}'")
        # PDF Array -> Python List
        return _parse_array_value(val_str)
    if val_str.startswith("/"):
        # PDF Name -> pikepdf.Name
        return Name(val_str)

    try:
        # PDF Number -> Python float
        return float(val_str)
    except ValueError as exc:
        # Final check: if it contains unbalanced special chars, it's invalid
        if (val_str.count("(") != val_str.count(")")) or (
            val_str.count("[") != val_str.count("]")
        ):
            raise ValueError(f"Malformed value string: '{val_str}'") from exc
        # Default: A plain string -> Python String
        return val_str


def _apply_mods_to_annot(annot, modifications: list[tuple[str, str]], page_num: int) -> int:
    """
    Applies a list of (key, value) modifications to a single annotation.
    Returns the count of properties modified.
    """
    from pikepdf import Name

    prop_count = 0
    for key_str, val_str in modifications:
        try:
            py_value = _parse_value_to_python(val_str)
        except ValueError as exc:
            logger.warning(
                "Skipping invalid value for key '%s' on page %s: %s",
                key_str,
                page_num,
                exc,
            )
            continue  # Skip this modification

        # The key string from the parser (e.g., "Border")
        # must be prepended with a '/' to be a valid PDF Name.
        key_as_name = Name(f"/{key_str}")

        if py_value is None:
            # Value is 'null', so delete the key
            if key_as_name in annot:
                del annot[key_as_name]
                logger.debug(
                    "Deleted key '%s' from annot on page %s",
                    key_str,
                    page_num,
                )
                prop_count += 1
        else:
            # Set the key to the new value
            annot[key_as_name] = py_value
            logger.debug(
                "Set key '%s'=%s on annot on page %s",
                key_str,
                py_value,
                page_num,
            )
            prop_count += 1
    return prop_count


@register_operation(
    "modify_annots",
    tags=["in_place", "annotations"],
    type="single input operation",
    desc="Modify properties of existing annotations",
    long_desc=_MODIFY_ANNOTS_LONG_DESC,
    usage="<input> modify_annots <spec(K=V...)>... output <output>",
    examples=_MODIFY_ANNOTS_EXAMPLES,
    args=(["input_pdf", "operation_args"], {}),
)
def modify_annots(pdf: "Pdf", specs: list[str]):
    """
    Modifies properties of existing annotations in a PDF.
    """

    if not specs:
        logger.warning("No modification specs provided. Nothing to do.")
        return pdf
    pdf_page_count = len(pdf.pages)
    try:
        rules = specs_to_modification_rules(specs, len(pdf.pages))
        rules = specs_to_modification_rules(specs, pdf_page_count)
    except (ValueError, TypeError) as exc:
        msg = f"Failed to parse modify_annots arguments: {exc}"
        logger.error(msg)
        raise InvalidArgumentError(msg) from exc

    if not rules:
        logger.warning("No modification rules parsed. Nothing to do.")
        return pdf

    modified_annot_count = 0
    modified_prop_count = 0

    for rule in rules:
        rule_annot_count, rule_prop_count = _apply_rule(pdf, rule, pdf_page_count)
        modified_annot_count += rule_annot_count
        modified_prop_count += rule_prop_count

    logger.info(
        "Modified %d properties across %d annotations.",
        modified_prop_count,
        modified_annot_count,
    )

    return pdf


def _apply_rule(pdf, rule, pdf_page_count):
    from pikepdf import Name

    annot_count = 0
    prop_count = 0
    for page_num in rule.page_numbers:
        if not 1 <= page_num <= pdf_page_count:
            logger.warning(
                "Spec references page %d, but PDF only has %d pages. Skipping.",
                page_num,
                pdf_page_count,
            )
            continue  # Skip this page number

        # page_num is 1-based, list index is 0-based
        page = pdf.pages[page_num - 1]

        # Check if the /Annots key exists.
        if Name.Annots not in page:
            continue  # No annotations on this page

        # Iterate over the page.Annots array
        for annot in page.Annots:
            # Check if the annotation type matches the rule's selector
            if rule.type_selector:
                # Ensure annot.Subtype exists before comparing
                annot_subtype = annot.get(Name.Subtype)
                if annot_subtype != Name(rule.type_selector):
                    continue

            # This annotation is a match. Apply all modifications.
            annot_count += 1
            prop_count += _apply_mods_to_annot(annot, rule.modifications, page_num)

    return annot_count, prop_count
