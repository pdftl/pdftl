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
from pdftl.utils.pikepdf_compatibility_utils import set_outline_item_style_compat

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
    "action_lossy",
    "launch",
    "goto_remote",
    "named_action",
    "new_window",
}


def _to_clean_string(s: "pikepdf.String"):
    return str(s).split("\x00", 1)[0]


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
        # Safely truncate string objects at the first null byte (handles padding)
        return _to_clean_string(obj)
    elif isinstance(obj, (int, float, bool)):
        return obj
    elif obj is None:
        return None
    elif isinstance(obj, pikepdf.Stream):
        # No text representation attempted here — see _extract_action for
        # the one case (a /JS action's script) where this actually matters.
        return {"__stream__": True}
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


def _sanitize_dest_and_action_conflicts(pdf: "pikepdf.Pdf") -> None:
    """Pre-sanitizes outline items carrying both /Dest and /A.

    ISO 32000-2 Table 153 forbids setting both on the same outline item
    -- but real-world PDFs do it anyway, and pikepdf's OutlineItem
    constructor raises ValueError unconditionally when it sees both
    (ignoring its own strict=False default), which aborts
    pdf.open_outline()'s tree walk for the *entire* document over one
    malformed item rather than just that item. So we fix the conflict up
    in the raw dictionary, before pikepdf ever loads it.

    We keep /Dest and drop /A: ISO 32000-2 12.6.4.2's NOTE says a /GoTo
    action and an equivalent direct /Dest "have the same effect", but
    that using the direct destination "is preferable" -- and a sibling
    project (pdfcer's resolve_item_destination, which documents this
    exact spec ambiguity as OL-A1) independently reached the same
    /Dest-wins precedent for the identical mutual-exclusion rule.

    Walked iteratively (not recursively) so a merely-deep-but-legal
    outline (thousands of levels) can't turn this pre-pass itself into a
    RecursionError, and guarded by objgen so a cyclic /Next or /First
    chain terminates instead of looping forever.
    """
    outlines = pdf.Root.get("/Outlines")
    if outlines is None:
        return

    visited: set[tuple[int, int]] = set()
    stack = [outlines.get("/First")]
    while stack:
        item = stack.pop()
        while item is not None:
            if _already_visited_outline_item(item, visited):
                break

            _drop_conflicting_action(item)

            child = item.get("/First")
            if child is not None:
                stack.append(child)

            item = item.get("/Next")


def _already_visited_outline_item(item, visited: set[tuple[int, int]]) -> bool:
    """Cycle guard for the walk above: records item's objgen, returns
    True if it's already been seen (a cyclic /Next or /First chain).
    """
    try:
        key = item.objgen
    except AttributeError:
        return False
    if key in visited:
        return True
    visited.add(key)
    return False


def _drop_conflicting_action(item) -> None:
    """Drops /A from an outline item that illegally carries both /Dest
    and /A, warning as it does. No-op if the conflict isn't present.
    """
    if "/Dest" not in item or "/A" not in item:
        return
    title = item.get("/Title")
    logger.warning(
        "Outline item %r has both /Dest and /A (spec-illegal "
        "per ISO 32000-2 Table 153); keeping /Dest, dropping /A.",
        str(title) if title is not None else "<untitled>",
    )
    del item["/A"]


def extract_toc_tree(pdf: "pikepdf.Pdf") -> list[dict]:
    """Extracts the entire outline tree from a PDF into a list of dictionaries."""
    _sanitize_dest_and_action_conflicts(pdf)
    page_map = get_page_map(pdf.pages)
    named_dests = get_named_destinations(pdf)

    # Safely traverse using the official API context manager
    with pdf.open_outline() as outline:
        return [_extract_item(item, pdf, page_map, named_dests) for item in outline.root]


def _extract_item(item: "pikepdf.OutlineItem", pdf, page_map, named_dests) -> dict:
    if not item.obj:
        raise OperationError("Invalid item (no obj)")

    # Safely extract and clean the outline item title of any trailing/embedded null bytes
    clean_title = _to_clean_string(item.title) if item.title is not None else ""
    node: dict[str, Any] = {"title": clean_title}

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


def _extract_simple_filespec(fs) -> "str | None":
    """Reduces a file specification (ISO 32000-2 7.11) to a display filename,
    but only for the "safe to simplify" shapes: a bare string, or a
    dictionary containing nothing but F/UF/Type where F and UF (if both
    present) agree. /UF is preferred when present (it has a defined text
    encoding; /F is a raw platform path "without interpretation" per
    7.11.2.1). Anything richer -- embedded-file references, differing
    F/UF, other keys -- returns None so the caller preserves the whole
    action losslessly instead of guessing which name is "the" filename.
    """
    import pikepdf

    if fs is None:
        return None
    if isinstance(fs, (pikepdf.String, str)):
        return str(fs)
    if isinstance(fs, pikepdf.Dictionary):
        keys = {str(k).lstrip("/") for k in fs.keys()}
        if not keys <= {"F", "UF", "Type"}:
            return None
        f_val = str(fs.get("/F")) if "/F" in fs else None
        uf_val = str(fs.get("/UF")) if "/UF" in fs else None
        if f_val is not None and uf_val is not None and f_val != uf_val:
            return None
        return uf_val or f_val
    return None


def _extract_launch_filename(action_obj) -> "str | None":
    """Resolves a /Launch action's target filename (ISO 32000-2 12.6.4.6,
    Table 207), but only for the two shapes safe to collapse to a plain
    filename: a lone /F entry, or (when /F is entirely absent) a lone
    /Win platform dictionary with nothing but a plain-string /F and an
    /O of "open" (the default) or absent. /Mac and /Unix are never read
    (undocumented structure, deprecated); a /Win with a /P parameter
    string or an /O of "print" is left alone too, since collapsing either
    would silently change what the action does. Anything outside these
    shapes returns None so the caller falls back to full preservation.
    """
    import pikepdf

    top_keys = {str(k).lstrip("/") for k in action_obj.keys()}

    if "F" in top_keys and not (top_keys & {"Win", "Mac", "Unix"}):
        return _extract_simple_filespec(action_obj.get("/F"))

    if "Win" in top_keys and not (top_keys & {"F", "Mac", "Unix"}):
        win = action_obj.get("/Win")
        if not isinstance(win, pikepdf.Dictionary):
            return None
        win_keys = {str(k).lstrip("/") for k in win.keys()}
        if not win_keys <= {"F", "O"}:
            return None
        op = win.get("/O")
        if op is not None and str(op) != "open":
            return None
        f_val = win.get("/F")
        return str(f_val) if isinstance(f_val, (pikepdf.String, str)) else None

    return None


def _extract_goto_remote(action_obj) -> "dict | None":
    """Resolves a /GoToR action (ISO 32000-2 12.6.4.3, Table 203) to a
    friendly dict, but only when its filename is unambiguous (see
    _extract_simple_filespec) and any action keys this function doesn't
    know about are absent.

    Its /D can name either an explicit page *number* in the remote
    document (the first array element is already an absolute integer,
    unlike a same-document /GoTo destination -- no named-destination
    lookup needed) -- giving {file, page, [view], [new_window]} -- or a
    name/string in the *remote* file's own namespace, which can't be
    resolved to a page without opening that file. That case isn't a
    fallback: showing {file, dest, [new_window]} loses nothing (the
    remote file's own page number was never available to us either
    way), it's just the exact same "dest vs page" split the top-level
    same-document bookmark schema already makes, applied one file over.
    """
    import pikepdf

    top_keys = {str(k).lstrip("/") for k in action_obj.keys()}
    if not top_keys <= {"S", "F", "D", "NewWindow"}:
        return None

    filename = _extract_simple_filespec(action_obj.get("/F"))
    if filename is None:
        return None

    result: dict = {"file": filename}

    dest = action_obj.get("/D")
    if isinstance(dest, (pikepdf.Name, pikepdf.String, str)):
        result["dest"] = str(dest).lstrip("/")
    elif isinstance(dest, pikepdf.Array) and len(dest) > 0:
        page_arg = dest[0]
        if not isinstance(page_arg, (int, pikepdf.Integer)) or int(page_arg) < 0:
            return None
        result["page"] = int(page_arg) + 1
        dest_type = str(dest[1]).lstrip("/") if len(dest) > 1 else "Fit"
        view_list = _view_list_from_args(
            dest_type, list(dest)[2:]
        )  # no Array slicing before pikepdf 10.10
        if view_list != ["Fit"]:
            result["view"] = view_list
    else:
        return None

    if "/NewWindow" in action_obj:
        result["new_window"] = bool(action_obj.get("/NewWindow"))
    return result


def _extract_named_action(action_obj) -> "str | None":
    """Resolves a /Named action (ISO 32000-2 12.6.4.12, Table 216) to its
    bare name (e.g. "NextPage"). Table 216 defines only /S and /N for
    this action type -- no optional extras -- so this is always a
    lossless simplification, not a "safe subset" like the Launch/GoToR
    helpers above.
    """
    top_keys = {str(k).lstrip("/") for k in action_obj.keys()}
    if not top_keys <= {"S", "N"}:
        return None
    name_val = action_obj.get("/N")
    if name_val is None:
        return None
    return str(name_val).lstrip("/")


def _extract_action(action_obj, node):
    import pikepdf

    action_type = str(action_obj.get("/S", ""))
    if action_type == "/URI":
        node["uri"] = str(action_obj.get("/URI"))
    elif action_type == "/JS":
        js = action_obj.get("/JS")
        if isinstance(js, pikepdf.Stream):
            try:
                node["action"] = {"S": {"__name__": "/JS"}, "JS": js.read_bytes().decode("utf-8")}
            except (UnicodeDecodeError, pikepdf.PdfError):
                node["action"] = _to_python_types(action_obj)
                node["action_lossy"] = True
        else:
            node["action"] = _to_python_types(action_obj)
    elif action_type == "/Launch" and (filename := _extract_launch_filename(action_obj)):
        node["launch"] = filename
        if "/NewWindow" in action_obj:
            node["new_window"] = bool(action_obj.get("/NewWindow"))
    elif action_type == "/GoToR" and (goto_remote := _extract_goto_remote(action_obj)):
        node["goto_remote"] = goto_remote
    elif action_type == "/Named" and (named := _extract_named_action(action_obj)):
        node["named_action"] = named
    elif action_type != "/GoTo":
        # For non-GoTo actions we couldn't simplify above, perfectly
        # preserve the ISO action dict instead of guessing.
        node["action"] = _to_python_types(action_obj)


def _extract_destination(item, node, page_map, named_dests):
    dest = _dest_from_outline_item(item)
    import pikepdf

    if isinstance(dest, (pikepdf.Name, pikepdf.String, str)):
        node["dest"] = str(dest).lstrip("/")
    elif isinstance(dest, pikepdf.Array):
        node.update(_get_node_dest_data(dest, page_map, named_dests))


def _view_list_from_args(dest_type: str, args) -> list:
    """Builds a friendly view list (e.g. ["XYZ", 0, 500, null]) from a
    destination type name plus its raw argument objects. Shared by
    same-document destinations and /GoToR's remote destination array,
    which both use the identical [type, ...numeric-or-null args] shape.
    """
    import decimal

    view_list = [dest_type]
    for arg in args:
        if isinstance(arg, (decimal.Decimal, float)):
            view_list.append(float(arg))
        elif arg is None or str(arg) == "null":
            view_list.append(None)
        else:
            try:
                view_list.append(int(arg))
            except (ValueError, TypeError):
                logger.warning("Ignoring unknown destination argument: %s", arg)
    return view_list


def _get_node_dest_data(dest, page_map, named_dests):
    resolved = resolve_dest_to_page_num(dest, page_map, named_dests)
    if not resolved:
        return {}

    update = {"page": resolved.page_num}

    logger.debug("resolved=%s", resolved)
    logger.debug("resolved.args=%s", resolved.args)

    # Reconstruct view list: ["XYZ", 0, 500, null]
    view_list = _view_list_from_args(resolved.dest_type, resolved.args)

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

    # pdf.open_outline() below loads the *existing* tree (so it can be
    # cleared) before we ever get to write the new one -- so a pre-existing
    # /Dest+/A conflict left over from the document being replaced would
    # crash the load just as it does in extract_toc_tree.
    _sanitize_dest_and_action_conflicts(pdf)

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
    item = _build_basic_item(node, pdf)
    item.to_dictionary_object(pdf)
    if item.obj is None:
        raise OperationError("Invalid item (no obj)")
    # Apply styling
    color = node["color"] if "color" in node and len(node["color"]) == 3 else None
    set_outline_item_style_compat(
        item, color=color, bold=bool(node.get("bold")), italic=bool(node.get("italic"))
    )

    # Process children
    for child_node in node.get("children", []):
        item.children.append(_build_item(child_node, pdf))

    return item


def _build_goto_remote_item(title: str, goto_remote, pdf) -> "pikepdf.OutlineItem":
    import pikepdf

    has_target = isinstance(goto_remote, dict) and (
        ("page" in goto_remote) ^ ("dest" in goto_remote)
    )
    if not isinstance(goto_remote, dict) or "file" not in goto_remote or not has_target:
        raise OperationError(
            f"Invalid keys found in bookmark '{title}': 'goto_remote' must be a "
            "dict with 'file' and exactly one of 'page' or 'dest'."
        )

    if "dest" in goto_remote:
        dest_value: object = pikepdf.String(goto_remote["dest"])
    else:
        remote_page = goto_remote["page"] - 1
        if remote_page < 0:
            raise ValueError(
                f"Validation Error: Bookmark '{title}' has goto_remote.page "
                f"{goto_remote['page']}, but page numbers must be >= 1."
            )
        view_args = goto_remote.get("view", ["Fit"])
        dest_value = pikepdf.Array([remote_page, pikepdf.Name(f"/{view_args[0]}"), *view_args[1:]])

    action = pikepdf.Dictionary(
        S=pikepdf.Name("/GoToR"), F=pikepdf.String(goto_remote["file"]), D=dest_value
    )
    if "new_window" in goto_remote:
        action.NewWindow = bool(goto_remote["new_window"])
    return pikepdf.OutlineItem(title, action=action)


def _drop_lossy_action(node: dict, title: str) -> dict:
    """Strips a lossy 'action' (e.g. an undecodable /JS stream, which
    extraction could only capture as a marker) before it's rebuilt as
    though it were real data. Warns and falls through to whatever other
    target the node carries (dest/uri/page/none), exactly as if no
    action had ever been present. No-op if the node isn't lossy.
    """
    if not (node.get("action_lossy") and "action" in node):
        return node
    logger.warning(
        "Bookmark '%s' had a lossy action on extraction (e.g. an "
        "undecodable /JS stream); dropping it rather than writing "
        "back a placeholder.",
        title,
    )
    return {k: v for k, v in node.items() if k not in ("action", "action_lossy")}


def _build_launch_item(title: str, node: dict) -> "pikepdf.OutlineItem":
    import pikepdf

    action = pikepdf.Dictionary(S=pikepdf.Name("/Launch"), F=pikepdf.String(node["launch"]))
    if "new_window" in node:
        action.NewWindow = bool(node["new_window"])
    return pikepdf.OutlineItem(title, action=action)


def _build_named_action_item(title: str, node: dict) -> "pikepdf.OutlineItem":
    import pikepdf

    action = pikepdf.Dictionary(
        S=pikepdf.Name("/Named"), N=pikepdf.Name(f"/{node['named_action']}")
    )
    return pikepdf.OutlineItem(title, action=action)


def _build_page_item(title: str, node: dict, pdf) -> "pikepdf.OutlineItem":
    import pikepdf

    page_num = node["page"]
    if page_num < 1 or page_num > len(pdf.pages):
        raise ValueError(
            f"Validation Error: Bookmark '{title}' points to page {page_num}, "
            f"but the document only has {len(pdf.pages)} pages."
        )

    page_index = page_num - 1  # pikepdf uses 0-based page indexing
    item = pikepdf.OutlineItem(title, page_index)

    view_args = node.get("view", ["Fit"])
    if view_args != ["Fit"]:
        # Overwrite the default Fit destination array for complex views
        dest_type = pikepdf.Name(f"/{view_args[0]}")
        item.destination = pikepdf.Array([pdf.pages[page_index].obj, dest_type, *view_args[1:]])

    return item


def _build_basic_item(node: dict, pdf) -> "pikepdf.OutlineItem":
    import pikepdf

    title = node.get("title", "Untitled")
    node = _drop_lossy_action(node, title)

    if "action" in node:
        # Handles Launch, GoToR, Named, JavaScript, etc. natively
        action_dict = _from_python_types(node["action"], pdf)
        return pikepdf.OutlineItem(title, action=action_dict)

    if "dest" in node:
        # The API accepts a string reference name directly for named destinations
        return pikepdf.OutlineItem(title, node["dest"])

    if "uri" in node:
        action = pikepdf.Dictionary(S=pikepdf.Name("/URI"), URI=node["uri"])
        return pikepdf.OutlineItem(title, action=action)

    if "launch" in node:
        return _build_launch_item(title, node)

    if "goto_remote" in node:
        return _build_goto_remote_item(title, node["goto_remote"], pdf)

    if "named_action" in node:
        return _build_named_action_item(title, node)

    if "page" in node:
        return _build_page_item(title, node, pdf)

    # Fallback to the first page if nothing is specified
    return pikepdf.OutlineItem(title, 0)
