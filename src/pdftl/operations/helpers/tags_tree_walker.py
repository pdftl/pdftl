# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/helpers/tags_tree_walker.py

"""Navigating the logical Structure Tree and resolving structural types."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pdftl.operations.helpers.tags_stream_parser import (
    _build_mcid_stream_map,
    parse_stream_bytes_for_mcids,
)

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Page-index helpers
# ---------------------------------------------------------------------------


def _build_page_objgen_index(pdf: pikepdf.Pdf) -> dict:
    """objgen -> 1-based page number."""
    return {page.obj.objgen: i + 1 for i, page in enumerate(pdf.pages)}


# ---------------------------------------------------------------------------
# Role map helpers
# ---------------------------------------------------------------------------


def _build_role_map(pdf: pikepdf.Pdf) -> dict[str, str]:
    """
    Return a dict mapping custom tag name -> standard PDF tag name,
    from /StructTreeRoot/RoleMap.  Returns {} if absent or unreadable.
    """
    try:
        root = pdf.Root.StructTreeRoot
        if root is None or not hasattr(root, "get"):
            return {}
        role_map = root.get("/RoleMap")
        if role_map is None:
            return {}
        result = {}
        for k, v in role_map.items():
            try:
                result[str(k).lstrip("/")] = str(v).lstrip("/")
            except (AttributeError, TypeError, ValueError):
                # Skip any malformed role map entry that can't be coerced to strings.
                pass
        return result
    except (AttributeError, TypeError, ValueError):
        return {}


def _format_role(role: str) -> str:
    """Format a role= fragment, quoting if the name contains whitespace or quotes."""
    if any(c in role for c in (" ", "\t", '"', "'")):
        return f'role="{role}"'
    return f"role={role}"


# Standard PDF tag types (PDF 1.7 + 2.0).
_STANDARD_TAGS = {
    "Document",
    "Part",
    "Art",
    "Sect",
    "Div",
    "BlockQuote",
    "Caption",
    "TOC",
    "TOCI",
    "Index",
    "NonStruct",
    "Private",
    "H",
    "H1",
    "H2",
    "H3",
    "H4",
    "H5",
    "H6",
    "P",
    "L",
    "LI",
    "Lbl",
    "LBody",
    "Table",
    "TR",
    "TH",
    "TD",
    "THead",
    "TBody",
    "TFoot",
    "Span",
    "Quote",
    "Note",
    "Reference",
    "BibEntry",
    "Code",
    "Link",
    "Annot",
    "Ruby",
    "Warichu",
    "RB",
    "RT",
    "RP",
    "WT",
    "WP",
    "Figure",
    "Formula",
    "Form",
    # PDF 2.0 additions
    "Title",
    "FENote",
    "Sub",
    "Em",
    "Strong",
    "Artifact",
}


def _resolve_tag(raw_tag: str, role_map: dict[str, str]) -> tuple[str, str | None]:
    """
    Return (standard_tag, role_or_None).

    - If raw_tag is already a standard type: (raw_tag, None)
    - If raw_tag maps to a standard type via role_map: (standard_type, raw_tag)
    - If raw_tag has no mapping at all: ("unknown", raw_tag)
    """
    if raw_tag in _STANDARD_TAGS:
        return raw_tag, None
    mapped = role_map.get(raw_tag)
    if mapped is not None:
        return mapped, raw_tag
    return "unknown", raw_tag


# ---------------------------------------------------------------------------
# Structure tree node extraction utilities
# ---------------------------------------------------------------------------


def _resolve_page_num(elem, page_objgen_index: dict) -> int | None:
    """Return the 1-based page number for a struct element or MCR dict, or None."""
    if not hasattr(elem, "get"):
        return None
    pg = elem.get("/Pg")
    if pg is None:
        return None
    try:
        return page_objgen_index.get(pg.objgen)
    except (AttributeError, ValueError):
        return None


def _elem_tag(elem) -> str:
    """Return the raw tag string (no leading slash) for a struct element."""
    if not hasattr(elem, "get"):
        return "(unknown)"
    s = elem.get("/S")
    return str(s).lstrip("/") if s is not None else "(unknown)"


def _elem_text_attrs(elem) -> dict:
    """Extract Alt, ActualText, T (title/tooltip), Lang from a struct element."""
    import pikepdf

    out: dict[str, str] = {}
    if not hasattr(elem, "get"):
        return out
    try:
        for pdf_key, out_key in (
            ("/Alt", "alt"),
            ("/ActualText", "actual_text"),
            ("/T", "title"),
            ("/Lang", "lang"),
        ):
            val = elem.get(pdf_key)
            if val is not None:
                try:
                    out[out_key] = str(val)
                except (AttributeError, KeyError, ValueError, TypeError, pikepdf.PdfError):
                    # Swallowing silently here is intentional. If a single attribute in a
                    # StructElem is malformed or cannot be coerced to a string (for instance,
                    # due to unexpected nested PDF objects), we should still gracefully proceed
                    # with extracting any other valid attributes rather than failing completely.
                    pass
    except (AttributeError, TypeError, ValueError):
        # elem does not support .get() (e.g. a raw int or float). Return whatever
        # attributes we managed to collect before the error (typically none).
        pass
    return out


def _iter_k(elem) -> list:
    """
    Iterate over the /K children of a struct element.

    /K can be:
        - an int (direct MCID)
        - a dict (StructElem or MCR)
        - an array of any of the above
    Returns a flat list of items.
    """
    import pikepdf

    if not hasattr(elem, "get"):
        return []
    k = elem.get("/K")
    if k is None:
        return []
    if isinstance(k, pikepdf.Array):
        return list(k)
    return [k]


def _is_mcr(item) -> bool:
    """True if *item* is a Marked Content Reference dict (not a StructElem)."""
    try:
        return str(item.get("/Type", "")) == "/MCR"
    except (AttributeError, TypeError, ValueError):
        # If item is not a dictionary or doesn't have a get method,
        # it cannot be an MCR dictionary. We safely return False.
        return False


def _mcid_from_item(item) -> int | None:
    """Extract an MCID integer from a direct int or MCR dict."""
    if isinstance(item, int):
        return item
    try:
        from pikepdf import Integer

        if isinstance(item, Integer):
            return int(item)
    except ImportError:
        pass  # pikepdf.Integer unavailable; fall through to dict-based extraction

    try:
        mcid = item.get("/MCID")
    except (AttributeError, TypeError, ValueError):
        return None

    if mcid is not None:
        try:
            return int(mcid)
        except (TypeError, ValueError):
            # MCID value present but not castable to int — malformed entry, skip.
            pass

    return None


def _stm_from_item(item) -> pikepdf.Object | None:
    """Extract the /Stm (XObject stream) reference from an MCR dict, if present."""
    if isinstance(item, int):
        return None
    try:
        stm = item.get("/Stm")
        if stm is not None:
            return stm
    except (AttributeError, TypeError, ValueError):
        # If the item does not support get() operations, it does not carry an
        # active XObject stream reference. We safely return None.
        pass
    return None


def _is_struct_elem(item) -> bool:
    try:
        t = item.get("/Type")
        if t is not None and str(t) == "/StructElem":
            return True
        return item.get("/S") is not None
    except (AttributeError, TypeError, ValueError):
        # If the item is not a dictionary or does not have the expected keys,
        # it is not a structural element. We safely return False.
        return False


def _collect_tree_roots(pdf: pikepdf.Pdf) -> list:
    """Return the top-level StructElem(s) under /StructTreeRoot/K."""
    import pikepdf

    try:
        root = pdf.Root.StructTreeRoot
    except (AttributeError, ValueError):
        return []

    if root is None or not hasattr(root, "get"):
        return []

    k = root.get("/K")
    if k is None:
        return []
    if isinstance(k, pikepdf.Array):
        return list(k)
    return [k]


# ---------------------------------------------------------------------------
# Mode: tree
# ---------------------------------------------------------------------------

# pikepdf returns (0, 0) as the objgen for any inline (non-indirect) object.
_INLINE_OBJGEN = (0, 0)


def _build_mcr_child_node(
    item, mcid: int, page_objgen_index: dict, inherited_pg: int | None = None
) -> dict:
    """Construct tree node child block for marked content references (MCR)."""
    child: dict = {"mcid": mcid}
    # Capture /Pg from the MCR dict itself if present (common in
    # hand-tagged PDFs where /Pg is not on every StructElem).
    item_pg = _resolve_page_num(item, page_objgen_index) if hasattr(item, "get") else None
    effective_pg = item_pg if item_pg is not None else inherited_pg
    if effective_pg is not None:
        child["page"] = effective_pg
    stm = _stm_from_item(item)
    if stm is not None:
        try:
            child["xobject_objgen"] = f"{stm.objgen[0]}:{stm.objgen[1]}"
        except (AttributeError, ValueError):
            # If the stream reference lacks standard objgen identification,
            # we safely skip appending the objgen identifier to the child.
            pass
    return child


def _tree_node(
    elem,
    page_objgen_index: dict,
    role_map: dict | None = None,
    seen: set | None = None,
    inherited_pg: int | None = None,
) -> dict:
    """Recursively build a plain-dict tree from a struct element."""
    if role_map is None:
        role_map = {}
    if seen is None:
        seen = set()

    if hasattr(elem, "objgen") and elem.objgen and elem.objgen != _INLINE_OBJGEN:
        if elem.objgen in seen:
            return {"type": "CircularReference", "id": f"{elem.objgen[0]}:{elem.objgen[1]}"}
        seen.add(elem.objgen)

    raw_tag = _elem_tag(elem)
    standard_tag, role = _resolve_tag(raw_tag, role_map)

    node: dict = {"tag": standard_tag}
    if role is not None:
        node["role"] = role
    node.update(_elem_text_attrs(elem))

    pg = _resolve_page_num(elem, page_objgen_index)
    effective_pg = pg if pg is not None else inherited_pg
    if effective_pg is not None:
        node["page"] = effective_pg

    children_out = []
    for item in _iter_k(elem):
        mcid = _mcid_from_item(item)
        if mcid is not None:
            children_out.append(_build_mcr_child_node(item, mcid, page_objgen_index, effective_pg))
        elif _is_struct_elem(item):
            children_out.append(
                _tree_node(
                    item,
                    page_objgen_index,
                    role_map=role_map,
                    seen=seen.copy(),
                    inherited_pg=effective_pg,
                )
            )

    if children_out:
        node["children"] = children_out

    return node


def _run_tree(
    pdf: pikepdf.Pdf,
    json_output: bool,
    output_file: str | None,
) -> list:
    """Build and return the tree data; CLI hook handles serialization."""
    page_objgen_index = _build_page_objgen_index(pdf)
    role_map = _build_role_map(pdf)
    roots = _collect_tree_roots(pdf)
    tree = [_tree_node(r, page_objgen_index, role_map=role_map) for r in roots]
    return tree


# ---------------------------------------------------------------------------
# Mode: reading_order helpers
# ---------------------------------------------------------------------------


def _get_mcid_map(
    pdf: pikepdf.Pdf, page_num: int, annotate: bool, page_mcid_maps: dict
) -> tuple[dict, list[str]]:
    if page_num not in page_mcid_maps:
        page_mcid_maps[page_num] = _build_mcid_stream_map(pdf, page_num, annotate)
    return page_mcid_maps[page_num]


def _annotate_xobject_stream(
    stream_bytes: bytes, stm: Any, mcid_blocks: dict, lines: list[str], pikepdf
) -> list[str]:
    """Helper method to inject debug coordinates or comments into stream lines."""
    try:
        from pdftl.operations.helpers.stream_annotator import annotate_stream

        annotated_bytes = annotate_stream(stream_bytes, stm.get("/Resources"), 40, 80)
        annotated_lines = annotated_bytes.decode("latin-1").splitlines()
        if len(annotated_lines) == len(lines):
            for block in mcid_blocks.values():
                block["lines"] = annotated_lines[block["start_line"] - 1 : block["end_line"]]
            return annotated_lines
    except (AttributeError, ValueError, TypeError, pikepdf.PdfError):
        # If annotating the stream breaks due to malformed font/resource
        # structures, we fall back silently to parsing the unannotated bytes.
        pass
    return lines


def _parse_and_build_xobj_map(stm: Any, annotate: bool, pikepdf) -> tuple[dict, list[str]]:
    """Helper orchestrator to read content streams, parse coordinates, and apply annotation."""
    from pdftl.operations.helpers.xobject_helpers import read_xobject_stream

    stream_bytes = read_xobject_stream(stm, normalize=True)
    mcid_blocks, lines = parse_stream_bytes_for_mcids(stream_bytes)

    if annotate:
        lines = _annotate_xobject_stream(stream_bytes, stm, mcid_blocks, lines, pikepdf)

    return mcid_blocks, lines


def _get_xobj_mcid_map(stm, annotate: bool, xobj_mcid_maps: dict) -> tuple[dict, list[str]]:
    import pikepdf

    try:
        objgen = stm.objgen
        if objgen == _INLINE_OBJGEN:
            return {}, []
    except (AttributeError, ValueError):
        return {}, []

    if objgen not in xobj_mcid_maps:
        try:
            mcid_blocks, lines = _parse_and_build_xobj_map(stm, annotate, pikepdf)
            xobj_mcid_maps[objgen] = (mcid_blocks, lines)
        except (AttributeError, ValueError, TypeError, pikepdf.PdfError) as e:
            logger.debug("Failed to parse XObject stream %s: %s", objgen, e)
            xobj_mcid_maps[objgen] = ({}, [])

    return xobj_mcid_maps[objgen]


def _objgen_str(stm) -> str:
    """Return a stable display string for an XObject stream's object identity."""
    try:
        og = stm.objgen
        return "unknown" if og == _INLINE_OBJGEN else f"{og[0]}:{og[1]}"
    except (AttributeError, ValueError):
        return "unknown"


def _check_circular_reference(elem, seen: set, depth: int, output: list[str]) -> bool:
    try:
        objgen = getattr(elem, "objgen", None)
        if objgen and objgen != _INLINE_OBJGEN:
            if objgen in seen:
                output.append(f"{'  ' * depth}[CircularReference id={objgen[0]}:{objgen[1]}]")
                return True
            seen.add(objgen)
    except (AttributeError, TypeError, ValueError):
        # If the element lacks structural identification properties,
        # we safely skip tracking circular references for it and proceed.
        pass
    return False


def _build_header_parts(
    standard_tag: str,
    role: str | None,
    pg: int | None,
    attrs: dict,
    is_leaf: bool,
    struct_child_count: int,
) -> list[str]:
    header_parts = [f"[{standard_tag}]"]
    if pg is not None:
        header_parts.append(f"page={pg}")
    if role is not None:
        header_parts.append(_format_role(role))
    if attrs.get("alt"):
        header_parts.append(f'alt="{attrs["alt"]}"')
    if attrs.get("actual_text"):
        header_parts.append(f'actual="{attrs["actual_text"]}"')
    if attrs.get("lang"):
        header_parts.append(f"lang={attrs['lang']}")
    if not is_leaf and struct_child_count > 0:
        header_parts.append(f"({struct_child_count} children)")
    return header_parts


def _handle_xobj_mcid(
    mcid: int,
    stm,
    indent: str,
    annotate: bool,
    show_streams: bool,
    xobj_mcid_maps: dict,
    output: list[str],
) -> None:
    obj_str = _objgen_str(stm)
    if show_streams:
        xobj_map, _all_lines = _get_xobj_mcid_map(stm, annotate, xobj_mcid_maps)
        block = xobj_map.get(mcid)
        output_start = f"{indent}  [MCID {mcid} in XObject {obj_str}]"
        if block is None:
            output.append(f"{output_start}  (not found in XObject {obj_str} stream)")
        else:
            line_range = f"stream_lines={block['start_line']}-{block['end_line']}"
            output.append(f"{output_start}  {line_range}")
            for stream_line in block["lines"]:
                output.append(f"{indent}    {stream_line}")
    else:
        output.append(f"{indent}  [MCID {mcid} in XObject {obj_str}]")


def _handle_page_mcid(
    mcid: int,
    pg: int,
    indent: str,
    annotate: bool,
    show_streams: bool,
    pdf: pikepdf.Pdf,
    page_mcid_maps: dict,
    output: list[str],
) -> None:
    mcid_map, _all_lines = _get_mcid_map(pdf, pg, annotate, page_mcid_maps)
    block = mcid_map.get(mcid)

    if show_streams:
        if block is None:
            output.append(
                f"{indent}  [MCID {mcid}]  (not found in page {pg} stream — may be in an XObject)"
            )
        else:
            line_range = f"stream_lines={block['start_line']}-{block['end_line']}"
            output.append(f"{indent}  [MCID {mcid}]  {line_range}")
            for stream_line in block["lines"]:
                output.append(f"{indent}    {stream_line}")
    else:
        if block:
            output.append(
                f"{indent}  [MCID {mcid}]  stream_lines={block['start_line']}-{block['end_line']}"
            )
        else:
            output.append(f"{indent}  [MCID {mcid}]  (not in page stream)")


def _process_child_item(
    item,
    pg: int | None,
    depth: int,
    seen: set,
    pdf: pikepdf.Pdf,
    target_page_nums: set[int],
    annotate: bool,
    show_streams: bool,
    page_objgen_index: dict,
    page_mcid_maps: dict,
    xobj_mcid_maps: dict,
    output: list[str],
    role_map: dict | None = None,
) -> None:
    mcid = _mcid_from_item(item)
    stm = _stm_from_item(item)
    indent = "  " * depth

    if mcid is not None:
        # Prefer /Pg from the MCR dict itself — hand-tagged PDFs (e.g. the
        # LaTeX tagging project) store /Pg on the MCR rather than every
        # StructElem, so the parent's pg may be None.
        item_pg = _resolve_page_num(item, page_objgen_index) if hasattr(item, "get") else None
        effective_pg = item_pg if item_pg is not None else pg

        if stm is not None:
            _handle_xobj_mcid(mcid, stm, indent, annotate, show_streams, xobj_mcid_maps, output)
        elif effective_pg is not None:
            _handle_page_mcid(
                mcid, effective_pg, indent, annotate, show_streams, pdf, page_mcid_maps, output
            )
        else:
            output.append(f"{indent}  [MCID {mcid}]  (page unknown)")
    elif _is_struct_elem(item):
        _walk_node(
            item,
            depth + 1,
            seen.copy(),
            pdf,
            target_page_nums,
            annotate,
            show_streams,
            page_objgen_index,
            page_mcid_maps,
            xobj_mcid_maps,
            output,
            role_map=role_map,
            inherited_pg=pg,
        )


def _walk_node(
    elem,
    depth: int,
    seen: set,
    pdf: pikepdf.Pdf,
    target_page_nums: set[int],
    annotate: bool,
    show_streams: bool,
    page_objgen_index: dict,
    page_mcid_maps: dict,
    xobj_mcid_maps: dict,
    output: list[str],
    role_map: dict | None = None,
    inherited_pg: int | None = None,
) -> None:
    if role_map is None:
        role_map = {}

    if _check_circular_reference(elem, seen, depth, output):
        return

    raw_tag = _elem_tag(elem)
    standard_tag, role = _resolve_tag(raw_tag, role_map)
    attrs = _elem_text_attrs(elem)
    pg = _resolve_page_num(elem, page_objgen_index)
    effective_pg = pg if pg is not None else inherited_pg
    indent = "  " * depth
    kids = _iter_k(elem)

    has_struct_children = any(_is_struct_elem(item) for item in kids)
    has_mcid_refs = any(_mcid_from_item(item) is not None for item in kids)
    is_leaf = has_mcid_refs and not has_struct_children
    struct_child_count = sum(1 for item in kids if _is_struct_elem(item))

    header_parts = _build_header_parts(
        standard_tag, role, effective_pg, attrs, is_leaf, struct_child_count
    )

    # Only emit nodes for pages in scope (or container nodes with no page).
    # Still recurse so we catch in-scope descendants of out-of-scope parents.
    if effective_pg is not None and effective_pg not in target_page_nums:
        for item in kids:
            if _is_struct_elem(item):
                _walk_node(
                    item,
                    depth,
                    seen.copy(),
                    pdf,
                    target_page_nums,
                    annotate,
                    show_streams,
                    page_objgen_index,
                    page_mcid_maps,
                    xobj_mcid_maps,
                    output,
                    role_map=role_map,
                    inherited_pg=effective_pg,
                )
        return

    output.append(f"{indent}{' '.join(header_parts)}")

    for item in kids:
        _process_child_item(
            item,
            effective_pg,
            depth,
            seen,
            pdf,
            target_page_nums,
            annotate,
            show_streams,
            page_objgen_index,
            page_mcid_maps,
            xobj_mcid_maps,
            output,
            role_map=role_map,
        )


# ---------------------------------------------------------------------------
# Mode: reading_order
# ---------------------------------------------------------------------------


def _reading_order_lines(
    pdf: pikepdf.Pdf,
    target_page_nums: set[int],
    annotate: bool,
    show_streams: bool,
) -> list[str]:
    """
    Walk the structure tree in logical order and produce text output lines.
    """
    page_objgen_index = _build_page_objgen_index(pdf)
    role_map = _build_role_map(pdf)

    page_mcid_maps: dict[int, tuple[dict, list[str]]] = {}
    xobj_mcid_maps: dict[tuple, tuple[dict, list[str]]] = {}

    output: list[str] = []
    roots = _collect_tree_roots(pdf)

    if not roots:
        output.append("(no structure tree found)")
    else:
        for root in roots:
            _walk_node(
                elem=root,
                depth=0,
                seen=set(),
                pdf=pdf,
                target_page_nums=target_page_nums,
                annotate=annotate,
                show_streams=show_streams,
                page_objgen_index=page_objgen_index,
                page_mcid_maps=page_mcid_maps,
                xobj_mcid_maps=xobj_mcid_maps,
                output=output,
                role_map=role_map,
            )

    return output
