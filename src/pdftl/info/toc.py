# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/info/toc.py

"""Core logic for extracting and building PDF Table of Contents (Outlines)."""

import logging
from typing import TYPE_CHECKING, Any

from pdftl.exceptions import OperationError
from pdftl.utils.destinations import (
    _dest_from_outline_item,
    get_named_destinations,
    get_page_map,
    resolve_dest_to_page_num,
)

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)

_ALLOWED_BOOKMARK_KEYS = {
    "title",
    "page",
    "dest",
    "uri",
    "view",
    "color",
    "bold",
    "italic",
    "children",
    "action",
}


def _to_python_types(obj):
    """Recursively converts pikepdf Objects to JSON/YAML-safe Python types."""
    import pikepdf

    if isinstance(obj, pikepdf.Dictionary):
        return {str(k).lstrip("/"): _to_python_types(v) for k, v in obj.items()}
    elif isinstance(obj, pikepdf.Array):
        return [_to_python_types(v) for v in obj]
    elif isinstance(obj, pikepdf.Name):
        # Unambiguous tagging to prevent strings from accidentally becoming PDF Names
        return {"__name__": str(obj)}
    elif isinstance(obj, pikepdf.String):
        return str(obj)
    elif isinstance(obj, (int, float, bool)):
        return obj
    elif obj is None:
        return None
    return str(obj)


def _from_python_types(obj, pdf):
    """Recursively reconstructs pikepdf Objects from native Python types."""
    import pikepdf

    if isinstance(obj, dict):
        if "__name__" in obj:
            return pikepdf.Name(obj["__name__"])
        return pikepdf.Dictionary(
            {
                f"/{k}" if not str(k).startswith("/") else k: _from_python_types(v, pdf)
                for k, v in obj.items()
            }
        )
    elif isinstance(obj, list):
        return pikepdf.Array([_from_python_types(v, pdf) for v in obj])
    elif isinstance(obj, str):
        return pikepdf.String(obj)
    return obj


def extract_toc_tree(pdf: "pikepdf.Pdf") -> list[dict]:
    """Extracts the entire outline tree from a PDF into a list of dictionaries."""
    page_map = get_page_map(pdf.pages)
    named_dests = get_named_destinations(pdf)

    # Safely traverse using the official API context manager
    with pdf.open_outline() as outline:
        return [_extract_item(item, pdf, page_map, named_dests) for item in outline.root]


def _extract_item(item: "pikepdf.OutlineItem", pdf, page_map, named_dests) -> dict:
    if not item.obj:
        raise OperationError("Invalid item (no obj)")
    node: dict[str, Any] = {"title": item.title}
    if color_array := item.obj.get("/C"):
        node["color"] = [float(c) for c in list(color_array)]
    if flags_obj := item.obj.get("/F"):
        flags = int(flags_obj)
        if flags & 1:
            node["italic"] = True
        if flags & 2:
            node["bold"] = True

    # 1. Action Extraction (URI, Launch, Named, JavaScript, etc.)
    action_obj = item.obj.get("/A")
    if action_obj is not None:
        _extract_action(action_obj, node)

    # 2. Destination Extraction
    # We only process standard destinations if there's no Action OR if it's a GoTo Action.
    if action_obj is None or str(action_obj.get("/S", "")) == "/GoTo":
        _extract_destination(item, node, page_map, named_dests)

    if item.children:
        node["children"] = [
            _extract_item(child, pdf, page_map, named_dests) for child in item.children
        ]

    return node


def _extract_action(action_obj, node):
    action_type = str(action_obj.get("/S", ""))
    if action_type == "/URI":
        node["uri"] = str(action_obj.get("/URI"))
    elif action_type != "/GoTo":
        # For non-GoTo/URI actions, perfectly preserve the ISO action dict
        node["action"] = _to_python_types(action_obj)


def _extract_destination(item, node, page_map, named_dests):
    dest = _dest_from_outline_item(item)
    import pikepdf

    if isinstance(dest, (pikepdf.Name, pikepdf.String, str)):
        node["dest"] = str(dest).lstrip("/")
    elif isinstance(dest, pikepdf.Array):
        node.update(_get_node_dest_data(dest, page_map, named_dests))


def _get_node_dest_data(dest, page_map, named_dests):
    resolved = resolve_dest_to_page_num(dest, page_map, named_dests)
    if not resolved:
        return {}

    update = {"page": resolved.page_num}

    # Reconstruct view list: ["XYZ", 0, 500, null]
    view_list = [resolved.dest_type]

    logger.debug("resolved=%s", resolved)
    logger.debug("resolved.args=%s", resolved.args)
    import decimal

    for arg in resolved.args:
        if isinstance(arg, (decimal.Decimal, float)):
            view_list.append(float(arg))
        elif arg is None or str(arg) == "null":
            view_list.append(None)
        else:
            try:
                view_list.append(int(arg))
            except (ValueError, TypeError):
                logger.warning("Ignoring unknown destination argument: %s", arg)

    # Only append view if it's more complex than standard Fit
    if view_list != ["Fit"]:
        update["view"] = view_list

    return update


def build_toc_tree(pdf: "pikepdf.Pdf", toc_items: list[dict]) -> None:
    """Replaces the PDF's outline tree with the provided list of dictionaries."""

    valid_toc_items = _filter_and_warn_bookmarks(toc_items)

    # 1. Handle empty inputs by stripping the Outlines entirely
    if not valid_toc_items:
        if "/Outlines" in pdf.Root:
            del pdf.Root.Outlines
        return

    # Use the official context manager to mutate the tree
    with pdf.open_outline() as outline:
        # Clear the existing root items natively
        del outline.root[:]

        for node in valid_toc_items:
            outline.root.append(_build_item(node, pdf))


def _filter_and_warn_bookmarks(items) -> list:
    """Recursively validates bookmarks, warning on typos and dropping malformed entries."""
    valid_items = []
    for item in items:
        if not isinstance(item, dict):
            logger.warning("Ignoring invalid bookmark entry (not a dictionary): %s", item)
            continue

        if "title" not in item:
            logger.warning("Ignoring invalid bookmark entry (missing 'title'): %s", item)
            continue

        # STRICT KEY CHECKING: Catch typos like 'pagee', 'colour', etc.
        unrecognized_keys = set(item.keys()) - _ALLOWED_BOOKMARK_KEYS
        if unrecognized_keys:
            # We raise an error here because a typo in routing (e.g. 'pagee')
            # means the bookmark is fundamentally broken.
            raise OperationError(
                f"Invalid keys found in bookmark '{item['title']}': "
                f"{', '.join(unrecognized_keys)}. "
                f"Allowed keys are: {', '.join(_ALLOWED_BOOKMARK_KEYS)}"
            )

        # Clean up children recursively
        children = item.get("children")
        if children is not None:
            if isinstance(children, list):
                item["children"] = _filter_and_warn_bookmarks(children)
            else:
                logger.warning(
                    "Ignoring invalid 'children' (must be a list) for bookmark: '%s'",
                    item["title"],
                )
                item["children"] = []

        valid_items.append(item)

    return valid_items


def _build_item(node: dict, pdf) -> "pikepdf.OutlineItem":
    import pikepdf

    item = _build_basic_item(node, pdf)
    item.to_dictionary_object(pdf)
    if item.obj is None:
        raise OperationError("Invalid item (no obj)")
    # Apply styling
    if "color" in node and len(node["color"]) == 3:
        item.obj.C = pikepdf.Array(node["color"])
    if node.get("bold") or node.get("italic"):
        item.obj.F = (2 if node.get("bold") else 0) + (1 if node.get("italic") else 0)

    # Process children
    for child_node in node.get("children", []):
        item.children.append(_build_item(child_node, pdf))

    return item


def _build_basic_item(node: dict, pdf) -> "pikepdf.OutlineItem":
    import pikepdf

    title = node.get("title", "Untitled")

    if "action" in node:
        # Handles Launch, GoToR, Named, JavaScript, etc. natively
        action_dict = _from_python_types(node["action"], pdf)
        item = pikepdf.OutlineItem(title, action=action_dict)

    elif "dest" in node:
        # The API accepts a string reference name directly for named destinations
        item = pikepdf.OutlineItem(title, node["dest"])

    elif "uri" in node:
        action = pikepdf.Dictionary(S=pikepdf.Name("/URI"), URI=node["uri"])
        item = pikepdf.OutlineItem(title, action=action)

    elif "page" in node:
        page_num = node["page"]
        if page_num < 1 or page_num > len(pdf.pages):
            raise ValueError(
                f"Validation Error: Bookmark '{title}' points to page {page_num}, "
                f"but the document only has {len(pdf.pages)} pages."
            )

        page_index = page_num - 1  # pikepdf uses 0-based page indexing
        view_args = node.get("view", ["Fit"])

        if view_args == ["Fit"]:
            # Simplest API usage: default Fit destination
            item = pikepdf.OutlineItem(title, page_index)
        else:
            # Create default and then overwrite the destination array for complex views
            item = pikepdf.OutlineItem(title, page_index)
            dest_type = pikepdf.Name(f"/{view_args[0]}")
            item.destination = pikepdf.Array(
                [pdf.pages[page_index].obj, dest_type, *view_args[1:]]
            )

    else:
        # Fallback to the first page if nothing is specified
        item = pikepdf.OutlineItem(title, 0)

    return item
