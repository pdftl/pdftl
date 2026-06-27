# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/dump_actions.py

"""Dump actions info from a PDF in JSON, handling nested action trees and form triggers"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.utils.io_helpers import smart_open
from pdftl.utils.json import pdf_obj_to_json
from pdftl.utils.string_utils import compact_json_string

logger = logging.getLogger(__name__)

_DUMP_ACTIONS_LONG_DESC = """
The `dump_actions` operation extracts interactive actions and trigger events from
a PDF file and outputs them in JSON format.

This includes actions associated with:
  - Document events (e.g., scripts that run when opening, saving, or printing)
  - Document-level JavaScript libraries
  - Page events (e.g., actions triggered when opening or closing a page)
  - Annotations (e.g., links, hover events, focus events)
  - Bookmarks (e.g., actions triggered when clicking a bookmark node)
  - Interactive form fields (e.g., keystroke, formatting, validation, or calculation scripts)
  - Chained action sequences (subsequent actions triggered in a chain)

### Filtering
Actions can be filtered using the same selector syntax as `dump_annots` and `modify_annots`.

The syntax is `selector` or `selector(Key=Value, ...)`, where:
  - `selector` is a page range (e.g., `1-5`, `odd`, see [[`page_specs`]]) and/or an
    action type (e.g., `/JavaScript`, `/GoTo`, `/URI`, `/Launch`).
  - `Key=Value` pairs filter actions by property values (e.g. matching a script body).

Page-specific selectors (like `1/JavaScript`) only return actions associated with that page.
Document-level and bookmark actions are excluded when page range filters are used.
"""

_DUMP_ACTIONS_EXAMPLES = [
    {
        "cmd": "in.pdf dump_actions",
        "desc": "Show all action data for a file",
    },
    {
        "cmd": "in.pdf dump_actions /JavaScript",
        "desc": "Show only JavaScript actions",
    },
    {
        "cmd": 'in.pdf dump_actions "1/JavaScript"',
        "desc": "Show JavaScript actions located on Page 1",
    },
    {
        "cmd": "in.pdf dump_actions \"/JavaScript(JS=(console.log('wc');))\"",
        "desc": "Show JavaScript actions matching an exact script",
    },
]


def dump_actions_cli_hook(result: OpResult, stage, _pipeline):
    """CLI Hook for dump_actions."""
    import json
    from pdftl.utils.hooks import from_result_meta

    output_file = from_result_meta(result, c.META_OUTPUT_FILE)
    json_string = json.dumps({"actions": result.data}, indent=2)
    compacted_string = compact_json_string(json_string)

    with smart_open(output_file) as f:
        f.write(compacted_string)
        f.write("\n")


@register_operation(
    "dump_actions",
    tags=["in_place", "actions", "info"],
    cli_hook=dump_actions_cli_hook,
    type="single input operation",
    desc="Dump action info",
    long_desc=_DUMP_ACTIONS_LONG_DESC,
    usage="<input> dump_actions [<selector>...] [output <output>]",
    examples=_DUMP_ACTIONS_EXAMPLES,
    args=([c.INPUT_PDF], {"specs": c.OPERATION_ARGS, "output_file": c.OUTPUT}),
    skip_pipeline_save=True,
)
def dump_actions(pdf, specs=None, output_file=None) -> OpResult:
    """Dumps all actions from a PDF in JSON format, optionally filtered."""
    from pdftl.operations.parsers.modify_annots_parser import specs_to_selection_rules

    rules = None
    if specs:
        rules = specs_to_selection_rules(specs, len(pdf.pages))

    all_actions = _get_all_actions_data(pdf, rules, specs)
    return OpResult(
        success=True, data=all_actions, meta={c.META_OUTPUT_FILE: output_file}, pdf=pdf
    )


def _get_all_actions_data(pdf, rules, specs=None):
    collected = []
    _collect_document_actions(pdf, collected)
    _collect_javascript_names(pdf, collected)
    _collect_page_actions(pdf, collected)
    _collect_outline_actions(pdf, collected)
    _collect_form_field_actions(pdf, collected)

    # Process recursively to resolve `/Next` sub-actions for everything collected so far
    resolved_collected = []
    for item in collected:
        resolved_collected.append(item)
        _collect_chained_actions(item, resolved_collected)

    page_object_to_num_map = {p.obj.objgen: i + 1 for i, p in enumerate(pdf.pages)}
    from pikepdf import Name, NameTree

    named_dests = {}
    if Name.Names in pdf.Root and Name.Dests in pdf.Root.Names:
        named_dests = NameTree(pdf.Root.Names.Dests)

    num_pages = len(pdf.pages)
    results = []
    for item in resolved_collected:
        action_obj = _get_action_object(item)
        props = pdf_obj_to_json(action_obj, page_object_to_num_map, named_dests, compat=False)
        if "/S" in props:
            props["/Subtype"] = props["/S"]
        elif item["type"] == "js_name":
            props["/Subtype"] = "/JavaScript"
            props["/S"] = "/JavaScript"

        entry = {"Location": item["location"], "Page": item["page"], "Properties": props}
        if _action_matches_rules(entry, rules, specs, num_pages):
            results.append(entry)
    return results


def _get_action_object(item):
    if item["type"] == "js_name":
        return item["obj"]
    if item["type"] == "outline":
        return item["parent"]["/A"]
    if item["type"] == "document_open":
        return item["parent"]["/OpenAction"]
    if item["type"] == "next_chain":
        return item["obj"]
    return item["parent"][item["key"]]


def _action_matches_rules(action_entry, rules, specs, num_pages):
    if not rules:
        return True
    return any(
        _action_passes_single_rule(action_entry, r, s, num_pages) for r, s in zip(rules, specs)
    )


def _has_page_specifier(spec: str) -> bool:
    if not spec:
        return False
    if "/" in spec:
        return spec.index("/") > 0
    if spec.startswith("("):
        return False
    return True


def _action_passes_single_rule(action_entry, rule, spec, num_pages):
    page = action_entry["Page"]
    if page is not None:
        if page not in rule.page_numbers:
            return False
    else:
        if _has_page_specifier(spec):
            return False
    return _annot_passes_rule(action_entry, rule)


def _annot_passes_rule(annot: dict, rule) -> bool:
    props = annot["Properties"]
    if rule.type_selector and props.get("/Subtype") != rule.type_selector:
        return False
    if rule.value_selectors:
        return _annot_matches_filters(props, rule.value_selectors)
    return True


def _annot_matches_filters(annot_props: dict, value_selectors: list) -> bool:
    from pdftl.operations.modify_annots import _parse_value_to_python

    for key_str, val_str in value_selectors:
        try:
            py_value = _parse_value_to_python(val_str)
        except (ValueError, TypeError, KeyError):
            return False
        actual = annot_props.get(f"/{key_str}")
        if actual is None or not _values_equal(actual, py_value):
            return False
    return True


def _values_equal(actual, expected) -> bool:
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return False
        return all(_values_equal(a, e) for a, e in zip(actual, expected))
    if isinstance(expected, bool) or isinstance(actual, bool):
        return actual is expected
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        return float(actual) == float(expected)
    if isinstance(expected, str) and isinstance(actual, str):
        return actual.lstrip("/") == expected.lstrip("/")
    return actual == expected


def _collect_chained_actions(item, resolved_collected):
    """Traverses chained '/Next' actions recursively according to PDF 1.2+ spec."""
    import pikepdf

    action_obj = _get_action_object(item)
    if not isinstance(action_obj, pikepdf.Dictionary) or "/Next" not in action_obj:
        return

    next_field = action_obj["/Next"]
    if isinstance(next_field, pikepdf.Array):
        for idx, sub_action in enumerate(next_field, 1):
            _append_next_item(sub_action, item, f"Chain Link {idx}", resolved_collected)
    elif isinstance(next_field, pikepdf.Dictionary):
        _append_next_item(next_field, item, "Chain Link", resolved_collected)


def _append_next_item(action_node, parent_item, label, resolved_collected):
    import pikepdf

    if isinstance(action_node, pikepdf.Dictionary):
        new_item = {
            "type": "next_chain",
            "parent": parent_item,
            "key": "/Next",
            "obj": action_node,
            "page": parent_item["page"],
            "location": f"{parent_item['location']} -> {label}",
        }
        resolved_collected.append(new_item)
        _collect_chained_actions(new_item, resolved_collected)


def _collect_document_actions(pdf, collected):
    import pikepdf

    if "/OpenAction" in pdf.Root:
        obj = pdf.Root["/OpenAction"]
        if isinstance(obj, pikepdf.Dictionary) and "/S" in obj:
            collected.append(
                {
                    "type": "document_open",
                    "parent": pdf.Root,
                    "key": "/OpenAction",
                    "page": None,
                    "location": "Document OpenAction",
                }
            )
    if "/AA" in pdf.Root:
        for k in pdf.Root["/AA"].keys():
            collected.append(
                {
                    "type": "document_aa",
                    "parent": pdf.Root["/AA"],
                    "key": k,
                    "page": None,
                    "location": f"Document Additional Action ({k})",
                }
            )


def _collect_javascript_names(pdf, collected):
    if "/Names" in pdf.Root and "/JavaScript" in pdf.Root["/Names"]:
        from pikepdf import NameTree

        js_tree = NameTree(pdf.Root["/Names"]["/JavaScript"])
        for name, obj in js_tree.items():
            collected.append(
                {
                    "type": "js_name",
                    "parent": js_tree,
                    "key": name,
                    "page": None,
                    "location": f"Document JavaScript Name Tree ({name})",
                    "obj": obj,
                }
            )


def _collect_page_actions(pdf, collected):
    for page_num, page in enumerate(pdf.pages, 1):
        if "/AA" in page:
            for k in page["/AA"].keys():
                collected.append(
                    {
                        "type": "page_aa",
                        "parent": page["/AA"],
                        "key": k,
                        "page": page_num,
                        "location": f"Page {page_num} Additional Action ({k})",
                    }
                )
        if hasattr(page, "Annots"):
            _collect_annot_actions(page, page_num, collected)


def _collect_annot_actions(page, page_num, collected):
    for idx, annot in enumerate(page.Annots, 1):
        if "/A" in annot:
            collected.append(
                {
                    "type": "annot_a",
                    "parent": annot,
                    "key": "/A",
                    "page": page_num,
                    "location": f"Page {page_num} Annotation {idx} Action",
                }
            )
        if "/AA" in annot:
            for k in annot["/AA"].keys():
                collected.append(
                    {
                        "type": "annot_aa",
                        "parent": annot["/AA"],
                        "key": k,
                        "page": page_num,
                        "location": (f"Page {page_num} Annotation {idx} Additional Action ({k})"),
                    }
                )


def _collect_outline_actions(pdf, collected):
    if "/Outlines" in pdf.Root and "/First" in pdf.Root["/Outlines"]:
        _walk_outline_node(pdf.Root["/Outlines"]["/First"], collected)


def _walk_outline_node(node, collected):
    for curr in _iter_outline_siblings(node):
        if "/A" in curr:
            collected.append(
                {
                    "type": "outline",
                    "parent": curr,
                    "key": "/A",
                    "page": None,
                    "location": "Outline Item Action",
                }
            )
        if "/First" in curr:
            _walk_outline_node(curr["/First"], collected)


def _iter_outline_siblings(node):
    curr = node
    while curr:
        yield curr
        curr = curr["/Next"] if "/Next" in curr else None


def _collect_form_field_actions(pdf, collected):
    """Traverse PDF AcroForm interactive fields and gather triggered actions (PDF 1.3+)."""
    if "/AcroForm" not in pdf.Root or "/Fields" not in pdf.Root["/AcroForm"]:
        return
    for field in pdf.Root["/AcroForm"]["/Fields"]:
        _walk_form_fields(field, collected)


def _walk_form_fields(field, collected):
    import pikepdf

    if "/AA" in field:
        field_name = str(field.get("/T", "Unnamed Field"))
        for k in field["/AA"].keys():
            collected.append(
                {
                    "type": "form_field_aa",
                    "parent": field["/AA"],
                    "key": k,
                    "page": None,
                    "location": f"Form Field '{field_name}' Trigger ({k})",
                }
            )
    if "/Kids" in field:
        for kid in field["/Kids"]:
            if isinstance(kid, pikepdf.Dictionary):
                _walk_form_fields(kid, collected)
