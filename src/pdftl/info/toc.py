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
}


def extract_toc_tree(pdf: "pikepdf.Pdf") -> list[dict]:
    """Extracts the entire outline tree from a PDF into a list of dictionaries."""
    page_map = get_page_map(pdf.pages)
    named_dests = get_named_destinations(pdf)

    # Safely traverse using the official API context manager
    with pdf.open_outline() as outline:
        return [_extract_item(item, pdf, page_map, named_dests) for item in outline.root]


def _extract_item(item: "pikepdf.OutlineItem", pdf, page_map, named_dests) -> dict:
    import pikepdf

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

    # 1. Handle URI Actions
    if item.obj.get(pikepdf.NamePath.A.S) == pikepdf.Name("/URI"):
        node["uri"] = str(item.obj.get(pikepdf.NamePath.A.URI))
    else:
        # 2. Extract Destination
        dest = _dest_from_outline_item(item)

        if isinstance(dest, (pikepdf.Name, pikepdf.String, str)):
            node["dest"] = str(dest).lstrip("/")
        elif isinstance(dest, pikepdf.Array):
            node.update(_get_node_dest_data(dest, page_map, named_dests))

    if item.children:
        node["children"] = [
            _extract_item(child, pdf, page_map, named_dests) for child in item.children
        ]

    return node


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
                logger.warn("Ignoring unknown destination argument: %s", arg)

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

    if "dest" in node:
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
