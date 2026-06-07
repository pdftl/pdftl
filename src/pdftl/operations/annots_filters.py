# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/dump_annots.py

"""Dump annotations info, in JSON, or delete annotations"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikepdf import Pdf

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.utils.io_helpers import smart_open
from pdftl.utils.json import pdf_obj_to_json
from pdftl.utils.string_utils import compact_json_string, xml_encode_for_info

logger = logging.getLogger(__name__)

_DELETE_ANNOTS_LONG_DESC = """

The `delete_annots` operation deletes annotations in a PDF file.

Without selectors, all annotations are removed from all pages.

### Filtering

Annotations can be filtered using the same selector syntax as
`dump_annots` and `modify_annots`. Selectors are optional.

The syntax is `selector` or `selector(Key=Value, ...)`, where:
  - `selector` is a page range (e.g., `1-5`, `odd`, see [[`page_specs`]]) and/or an
    annotation type (e.g., `/Link`, `/Highlight`).
  - `Key=Value` pairs filter annotations by property value.

### Value Syntax
  - PDF Names: `/Name`
  - PDF Strings: `(My String)`
  - PDF Arrays: `[0 0 1]`
  - PDF Booleans: `true` / `false`
  - Numbers: `1.5`, `10`
  - Plain strings are treated as PDF Strings: `Value` is interpreted as `(Value)`

"""

_DELETE_ANNOTS_EXAMPLES = [
    {
        "cmd": "in.pdf delete_annots output out.pdf",
        "desc": "Delete all annotations from in.pdf",
    },
    {
        "cmd": "in.pdf delete_annots 1-5/Link output out.pdf",
        "desc": "Delete only Link annotations on pages 1-5",
    },
    {
        "cmd": "in.pdf delete_annots odd/Highlight output out.pdf",
        "desc": "Delete only Highlight annotations on odd pages",
    },
    {
        "cmd": 'in.pdf delete_annots "/Link(Border=[0 0 0])" output out.pdf',
        "desc": "Delete only Link annotations with a zero border",
    },
]


_DUMP_ANNOTS_LONG_DESC = """

Extracts all annotations from the PDF and dumps them in a
structured JSON format.

Unlike `dump_data_annots`, this operation provides a raw,
comprehensive view of the annotation dictionaries, including
all properties (e.g., Rect, Contents, Colors, Flags).

The output is a JSON list where each entry represents an
annotation, grouped by page. This is useful for debugging,
analysis, or processing annotation data in other tools that
consume JSON.

### Filtering

Annotations can be filtered using the same selector syntax as
`modify_annots`. Selectors are optional; without them, all
annotations are dumped.

The syntax is `selector` or `selector(Key=Value, ...)`, where:
  - `selector` is a page range (e.g., `1-5`, `odd`, see [[`page_specs`]]) and/or an
    annotation type (e.g., `/Link`, `/Highlight`).
  - `Key=Value` pairs filter annotations by property value.

### Value Syntax
  - PDF Names: `/Name`
  - PDF Strings: `(My String)`
  - PDF Arrays: `[0 0 1]`
  - PDF Booleans: `true` / `false`
  - Numbers: `1.5`, `10`
  - Plain strings are treated as PDF Strings: `Value` is interpreted as `(Value)`

"""

_DUMP_ANNOTS_EXAMPLES = [
    {
        "cmd": "in.pdf dump_annots",
        "desc": "Show all annotation data for a file:",
    },
    {
        "cmd": "in.pdf dump_annots 1-5/Link",
        "desc": "Show only Link annotations on pages 1-5:",
    },
    {
        "cmd": "in.pdf dump_annots odd/Highlight",
        "desc": "Show only Highlight annotations on odd pages:",
    },
    {
        "cmd": 'in.pdf dump_annots "/Link(Border=[0 0 0])"',
        "desc": "Show only Link annotations with a zero border:",
    },
]


def dump_annots_cli_hook(result: OpResult, stage, _pipeline):
    """
    CLI Hook for dump_annots.
    Serializes the raw annotation data to a compacted JSON string and outputs it.
    """

    import json

    from pdftl.utils.hooks import from_result_meta

    output_file = from_result_meta(result, c.META_OUTPUT_FILE)

    # Serialize to JSON string
    json_string = json.dumps({"annotations": result.data}, indent=2)
    # Apply custom compaction
    compacted_string = compact_json_string(json_string)

    with smart_open(output_file) as f:
        f.write(compacted_string)
        # Ensure a trailing newline for console output
        f.write("\n")


@register_operation(
    "dump_annots",
    tags=["in_place", "annotations", "info"],
    cli_hook=dump_annots_cli_hook,
    type="single input operation",
    desc="Dump annotation info",
    long_desc=_DUMP_ANNOTS_LONG_DESC,
    usage="<input> dump_annots [<selector>...] [output <output>]",
    examples=_DUMP_ANNOTS_EXAMPLES,
    args=([c.INPUT_PDF], {"specs": c.OPERATION_ARGS, "output_file": c.OUTPUT}),
    skip_pipeline_save=True,
)
def dump_annots(pdf, specs=None, output_file=None) -> OpResult:
    """
    Dumps all annotations from a PDF in JSON format, with compact arrays.
    Optionally filtered by selector specs using the same syntax as modify_annots.
    """
    from pdftl.operations.parsers.modify_annots_parser import specs_to_selection_rules

    logger.debug("Dumping annotations for PDF with %s pages.", len(pdf.pages))

    rules = None
    if specs:
        rules = specs_to_selection_rules(specs, len(pdf.pages))

    all_annots_data = _get_all_annots_data(pdf, compat=False, rules=rules)
    return OpResult(
        success=True, data=all_annots_data, meta={c.META_OUTPUT_FILE: output_file}, pdf=pdf
    )


_DUMP_DATA_ANNOTS_LONG_DESC = """

Extracts annotations and prints them in a text-based stanza
format, designed to be compatible with `pdftk`'s output.

This operation **filters** the output to standard annotation
types (Link, Text, Widget, Popup, FreeText, Square, URI,
FileAttachment). It explicitly excludes JavaScript actions.

### Stanza Format

Entries are separated by `---`. Common keys include:

* `AnnotSubtype`: The type of annotation (e.g., Link, Text).
* `AnnotRect`: The bounding box [x y w h].
* `AnnotPageNumber`: The page number containing the annotation.

Use this operation for legacy compatibility or simple textual
analysis. For a complete dataset, use `dump_annots`.

"""

_DUMP_DATA_ANNOTS_EXAMPLES = [
    {
        "cmd": "in.pdf dump_data_annots",
        "desc": "Show annotation data for a file:",
    }
]


def _generate_pdftk_annots_report(data, string_convert=xml_encode_for_info):
    """Helper to generate the text report from the structured data dict"""
    annots = data.get("Annotations", [])
    data_strings = _data_to_strings(annots, string_convert, compat=True)

    uri_line = ""
    if "PdfUriBase" in data:
        uri_line = f"\nPdfUriBase: {string_convert(data['PdfUriBase'])}"

    # The first block contains global metadata
    first_block = f"NumberOfPages: {data['NumberOfPages']}" + uri_line

    data_strings = [first_block] + data_strings

    return "\n---\n".join(data_strings)


def dump_data_annots_cli_hook(result: OpResult, stage, _pipeline):
    """
    CLI Hook for dump_data_annots.
    Formats the raw structured data into the pdftk style report.
    """
    from pdftl.utils.hooks import from_result_meta

    output_file = from_result_meta(result, c.META_OUTPUT_FILE)

    if not result.data:
        logger.warning("No data available to generate full dump_data_annots report.")
        return

    text_report = _generate_pdftk_annots_report(result.data)

    with smart_open(output_file) as f:
        f.write(text_report)
        f.write("\n")


@register_operation(
    "dump_data_annots",
    tags=["in_place", "annotations", "info"],
    cli_hook=dump_data_annots_cli_hook,
    type="single input operation",
    desc="Dump annotation info in pdftk style",
    long_desc=_DUMP_DATA_ANNOTS_LONG_DESC,
    usage="<input> dump_data_annots [output <output>]",
    examples=_DUMP_DATA_ANNOTS_EXAMPLES,
    args=([c.INPUT_PDF], {"output_file": c.OUTPUT}),
)
def dump_data_annots(pdf, output_file=None, string_convert=xml_encode_for_info) -> OpResult:
    """
    Dumps annotation data from a PDF in pdftk style
    """
    logger.debug("Dumping pdftk-style annotations data for PDF with %s pages.", len(pdf.pages))
    all_annots_data = _get_all_annots_data(pdf, compat=True)
    data = {"NumberOfPages": len(pdf.pages), "Annotations": all_annots_data}
    # Extract URI Base if present in the PDF Root
    if hasattr(pdf.Root, "URI") and hasattr(pdf.Root.URI, "Base"):
        data["PdfUriBase"] = str(pdf.Root.URI.Base)
    return OpResult(success=True, data=data, pdf=pdf, meta={c.META_OUTPUT_FILE: output_file})


##################################################


@register_operation(
    "delete_annots",
    tags=["in_place", "annotations", "delete"],
    type="single input operation",
    desc="Delete annotation info",
    long_desc=_DELETE_ANNOTS_LONG_DESC,
    usage="<input> delete_annots [<selector>...] output <output>",
    examples=_DELETE_ANNOTS_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def delete_annots(pdf, specs) -> OpResult:
    """
    Delete annotations from a PDF, optionally filtered by selector specs.
    Without selectors, deletes all annotations from all pages.
    """
    from pdftl.operations.parsers.modify_annots_parser import specs_to_selection_rules

    if not specs:
        # Original behaviour: wipe everything
        for page in pdf.pages:
            if hasattr(page, "Annots"):
                page.Annots = []
        return OpResult(success=True, pdf=pdf)

    num_pages = len(pdf.pages)
    rules = specs_to_selection_rules(specs, num_pages)
    included_pages = {pn for rule in rules for pn in rule.page_numbers}

    # Build the same JSON view dump_annots uses, so _annot_passes_rule works
    page_object_to_num_map = {p.obj.objgen: i + 1 for i, p in enumerate(pdf.pages)}

    for page_num, page in enumerate(pdf.pages, 1):
        if page_num not in included_pages:
            continue
        if not hasattr(page, "Annots"):
            continue
        _delete_annots_in_page(page_num, page, rules, page_object_to_num_map)
    return OpResult(success=True, pdf=pdf)


def _delete_annots_in_page(page_num, page, rules, page_object_to_num_map):
    page_rules = [r for r in rules if page_num in r.page_numbers]

    # Fast path: if any rule matches all annotations on this page, wipe it
    if any(not r.type_selector and not r.value_selectors for r in page_rules):
        page.Annots = []
        return
    annots = list(page.Annots)

    # Iterate backwards so deletion doesn't shift indices
    for i in range(len(annots) - 1, -1, -1):
        from pdftl.utils.json import pdf_obj_to_json

        props = pdf_obj_to_json(annots[i], page_object_to_num_map, {}, compat=False)
        annot_entry = {"Page": page_num, "AnnotationIndex": i + 1, "Properties": props}
        if any(_annot_passes_rule(annot_entry, r) for r in page_rules):
            del page.Annots[i]


##################################################


def _annot_matches_filters(annot_props: dict, value_selectors: list[tuple[str, str]]) -> bool:
    """Returns True if annotation properties match all K=V filter criteria."""
    from pdftl.operations.modify_annots import _parse_value_to_python

    for key_str, val_str in value_selectors:
        try:
            py_value = _parse_value_to_python(val_str)
        except ValueError:
            return False
        actual = annot_props.get(f"/{key_str}")
        if actual is None or not _values_equal(actual, py_value):
            return False
    return True


def _values_equal(actual, expected) -> bool:
    """Compares a JSON-deserialized annotation value against a parsed filter value."""
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return False
        return all(_values_equal(a, e) for a, e in zip(actual, expected))
    # Numeric: compare as float to handle int/float mismatch
    if isinstance(expected, bool) or isinstance(actual, bool):
        return actual is expected
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        return float(actual) == float(expected)
    # Name: strip leading slash for comparison
    if isinstance(expected, str) and isinstance(actual, str):
        return actual.lstrip("/") == expected.lstrip("/")
    return actual == expected


def _annot_passes_rule(annot: dict, rule) -> bool:
    """Returns True if an annotation matches the page/type/value criteria of a rule."""
    props = annot["Properties"]
    if rule.type_selector and props.get("/Subtype") != rule.type_selector:
        return False
    if rule.value_selectors:
        return _annot_matches_filters(props, rule.value_selectors)
    return True


def _get_all_annots_data(pdf: "Pdf", compat=True, rules=None):
    """Get all annotations data for a PDF, optionally filtered by selection rules."""
    from pikepdf import Name, NameTree

    page_object_to_num_map = {p.obj.objgen: i + 1 for i, p in enumerate(pdf.pages)}
    named_dests = {}
    if Name.Names in pdf.Root and Name.Dests in pdf.Root.Names:
        named_dests = NameTree(pdf.Root.Names.Dests)

    included_pages = {pn for rule in rules for pn in rule.page_numbers} if rules else None

    all_annots_data = []
    for page_num, page in enumerate(pdf.pages, 1):
        if included_pages is not None and page_num not in included_pages:
            continue
        page_annots = _annots_json_for_page(
            page, page_num, page_object_to_num_map, named_dests, compat
        )
        if rules:
            page_rules = [r for r in rules if page_num in r.page_numbers]
            page_annots = [
                a for a in page_annots if any(_annot_passes_rule(a, r) for r in page_rules)
            ]
        all_annots_data.extend(page_annots)
    return all_annots_data


def _data_to_strings(data, string_convert, compat=False):
    """Convert data to strings for dump_data"""
    logger.debug(data)
    data_strings = []
    for datum in data:
        new_lines = _lines_from_datum(datum, string_convert, compat)
        if new_lines:  # Fixes the empty stanza output
            data_strings.append("\n".join(new_lines))
    return data_strings


##################################################


def _annots_json_for_page(page, page_num, page_object_to_num_map, named_dests, compat=False):
    """Return annotations info for one page, in JSON"""
    return [
        {
            "Page": page_num,
            "AnnotationIndex": i + 1,
            "Properties": pdf_obj_to_json(annot, page_object_to_num_map, named_dests, compat),
        }
        for i, annot in enumerate(getattr(page, "Annots", []))
    ]


def _lines_from_datum(datum, string_convert, compat=False):
    """Get lines from one data entry, for dump_annots. If compat
    is True, only output pdftk compatible lines."""
    new_lines = []
    prefix = "Annot"
    props = datum["Properties"]
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
    props_lines = []
    for key, value in props.items():
        if not compat or key in (
            "/Subtype",
            "/Rect",
        ):
            props_lines.extend(_key_value_lines(key, value, prefix, string_convert, compat))
    props_lines.sort()
    props_lines.reverse()
    new_lines.extend(props_lines)

    new_lines.append(
        _data_item_to_string_helper("PageNumber", datum["Page"], prefix, string_convert)
    )
    if not compat:
        new_lines.append(
            _data_item_to_string_helper(
                "IndexInPage", datum["AnnotationIndex"], prefix, string_convert
            )
        )
    return new_lines


def _key_value_lines(key, value, prefix, string_convert, compat=False):
    """Convert a key-value pair to strings for dump_annots"""
    if key == "/A":
        return [
            _data_item_to_string_helper(
                key2, value2, prefix + "Action", string_convert, compat=compat
            )
            for key2, value2 in value.items()
        ]
    if key in ("/Type", "/Border") or len(key) < 4:
        return []
    try:
        return [_data_item_to_string_helper(key, value, prefix, string_convert, compat=compat)]
    except NotImplementedError as exc:
        logger.warning("Skipping unsupported annotation key '{%s}' due to: {%s}", key, exc)
        return []


def _data_item_to_string_helper(key, value, prefix, string_convert_maybe, compat=False):
    """Helper method to convert a data item to a string"""

    if string_convert_maybe is None:

        def string_convert(x):
            return x

    else:
        string_convert = string_convert_maybe

    if isinstance(value, str) and value.startswith("/"):
        value = value[1:]
    if isinstance(key, str) and key.startswith("/"):
        key = key[1:]
    if key == "S":
        key = "Subtype"

    value_string = str(value)
    value_string = (
        value_string.replace("'", "").replace("[", "").replace("]", "").replace(", ", " ")
    )
    return f"{prefix}{key}: {string_convert(value_string)}"
