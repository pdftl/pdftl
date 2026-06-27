# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/delete_actions.py

"""Delete actions from a PDF file, supporting deep nested action paths and form objects"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation

logger = logging.getLogger(__name__)

_DELETE_ACTIONS_LONG_DESC = """
The `delete_actions` operation deletes interactive actions and event triggers from the PDF.
This is particularly useful for sanitizing files by removing executable scripts, external URL
requests, or dynamic triggers, reducing the document's security attack surface.

Without selectors, all actions are cleanly purged across the entire document.

### Supported Target Areas
All action-bearing elements and trees are fully swept:
  - **Catalog Level**: Removes `/OpenAction` and `/AA` entries in the document catalog.
  - **Names Tree**: Completely purges and drops `/Names -> /JavaScript` libraries.
  - **Page Objects**: Removes `/AA` dictionaries from all matching pages.
  - **Annotations**: Removes activation `/A` and additional action `/AA` triggers from page
    annotations.
  - **Outlines (Bookmarks)**: Clears action `/A` entries inside the outline tree.
  - **AcroForm Fields**: Wipes out `/AA` formatting, validation, keystroke, and calculation
    event blocks from interactive form elements.
  - **Sequential Sub-chains (`/Next` Trees)**: Recursively detaches or removes matched actions
    nested inside hierarchical action paths.

### Filtering Syntax
Actions can be filtered using the same selector syntax as `dump_annots` and `modify_annots`.
The general format is:
`[page-range]/[ActionType]([Property=Value], ...)`

  - **Page Range**: Page specs (e.g., `1-3`, `odd`, `even`). Only page-specific actions will be
    deleted.
  - **Action Type**: Subtype of the action to target (e.g. `/JavaScript`, `/Launch`, `/URI`,
    `/SubmitForm`, `/GoTo`).
  - **Properties**: Matches key-value targets inside action dictionaries (e.g., targeting a
    specific launch file path or JavaScript snippet).
"""

_DELETE_ACTIONS_EXAMPLES = [
    {
        "cmd": "in.pdf delete_actions output out.pdf",
        "desc": "Delete all actions from in.pdf",
    },
    {
        "cmd": "in.pdf delete_actions /JavaScript output out.pdf",
        "desc": "Delete only JavaScript actions",
    },
    {
        "cmd": "in.pdf delete_actions /Launch /SubmitForm output out.pdf",
        "desc": "Strip high-risk launch actions and form submission handlers",
    },
    {
        "cmd": 'in.pdf delete_actions "1/JavaScript" output out.pdf',
        "desc": "Delete JavaScript actions specifically on Page 1",
    },
]


@register_operation(
    "delete_actions",
    tags=["in_place", "actions", "delete"],
    type="single input operation",
    desc="Delete action info",
    long_desc=_DELETE_ACTIONS_LONG_DESC,
    usage="<input> delete_actions [<selector>...] output <output>",
    examples=_DELETE_ACTIONS_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def delete_actions(pdf, specs) -> OpResult:
    """Delete actions from a PDF, optionally filtered by selector specs."""
    from pdftl.operations.parsers.modify_annots_parser import specs_to_selection_rules

    rules = None
    if specs:
        rules = specs_to_selection_rules(specs, len(pdf.pages))

    _perform_delete_actions(pdf, rules, specs)
    return OpResult(success=True, pdf=pdf)


def _perform_delete_actions(pdf, rules, specs):
    from pdftl.operations.dump_actions import (
        _collect_document_actions,
        _collect_javascript_names,
        _collect_page_actions,
        _collect_outline_actions,
        _collect_form_field_actions,
        _collect_chained_actions,
        _get_action_object,
        _action_matches_rules,
    )
    from pdftl.utils.json import pdf_obj_to_json

    collected = []
    _collect_document_actions(pdf, collected)
    _collect_javascript_names(pdf, collected)
    _collect_page_actions(pdf, collected)
    _collect_outline_actions(pdf, collected)
    _collect_form_field_actions(pdf, collected)

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
    to_delete = []
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
            to_delete.append(item)

    _execute_deletions(pdf, to_delete)


def _execute_deletions(pdf, to_delete):
    for item in to_delete:
        t = item["type"]
        if t == "document_open":
            del pdf.Root["/OpenAction"]
        elif t in ("document_aa", "page_aa", "annot_aa", "form_field_aa"):
            _delete_dict_key(item["parent"], item["key"])
        elif t in ("annot_a", "outline"):
            _delete_dict_key(item["parent"], "/A")
        elif t == "js_name":
            _delete_js_name(pdf, item["key"])
        elif t == "next_chain":
            _delete_chain_link(item)

    _clean_empty_parents(pdf)


def _delete_dict_key(parent, key):
    if key in parent:
        del parent[key]


def _delete_js_name(pdf, key):
    if "/Names" in pdf.Root and "/JavaScript" in pdf.Root["/Names"]:
        from pikepdf import NameTree

        js_tree = NameTree(pdf.Root["/Names"]["/JavaScript"])
        if key in js_tree:
            del js_tree[key]


def _delete_chain_link(item):
    """Deletes a node out of a /Next action chain sequence or array."""
    import pikepdf
    from pdftl.operations.dump_actions import _get_action_object

    parent_item = item["parent"]
    try:
        parent_obj = _get_action_object(parent_item)
    except KeyError:
        return

    if not isinstance(parent_obj, pikepdf.Dictionary) or "/Next" not in parent_obj:
        return

    next_field = parent_obj["/Next"]
    target_node = item["obj"]

    if isinstance(next_field, pikepdf.Array) and len(next_field) > 1:
        parent_obj["/Next"] = pikepdf.Array([node for node in next_field if node != target_node])
        return
    del parent_obj["/Next"]


def _clean_empty_parents(pdf):
    _clean_root_aa(pdf)
    _clean_root_names(pdf)
    _clean_page_and_annot_aa(pdf)
    _clean_form_parents(pdf)


def _clean_root_aa(pdf):
    if "/AA" in pdf.Root and len(pdf.Root["/AA"]) == 0:
        del pdf.Root["/AA"]


def _clean_root_names(pdf):
    if "/Names" not in pdf.Root:
        return

    names_dict = pdf.Root["/Names"]
    if "/JavaScript" in names_dict:
        from pikepdf import NameTree

        js_tree = NameTree(names_dict["/JavaScript"])
        if len(js_tree) == 0:
            del names_dict["/JavaScript"]

    if len(names_dict) == 0:
        del pdf.Root["/Names"]


def _clean_page_and_annot_aa(pdf):
    for page in pdf.pages:
        _clean_dict_aa(page)
        if hasattr(page, "Annots"):
            for annot in page.Annots:
                _clean_dict_aa(annot)


def _clean_dict_aa(obj):
    if "/AA" in obj and len(obj["/AA"]) == 0:
        del obj["/AA"]


def _clean_form_parents(pdf):
    """Recursively sweep and clean empty AcroForm trigger parents."""
    if "/AcroForm" not in pdf.Root or "/Fields" not in pdf.Root["/AcroForm"]:
        return
    for field in pdf.Root["/AcroForm"]["/Fields"]:
        _clean_form_field_aa(field)


def _clean_form_field_aa(field):
    import pikepdf

    _clean_dict_aa(field)
    if "/Kids" in field:
        for kid in field["/Kids"]:
            if isinstance(kid, pikepdf.Dictionary):
                _clean_form_field_aa(kid)
