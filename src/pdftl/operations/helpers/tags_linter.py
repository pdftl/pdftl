# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/helpers/tags_linter.py

"""Accessibility rules compliance checker for the PDF structure tree."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pdftl.operations.helpers.tags_stream_parser import _build_mcid_stream_map
from pdftl.operations.helpers.tags_tree_walker import (
    _build_page_objgen_index,
    _build_role_map,
    _collect_tree_roots,
    _elem_tag,
    _elem_text_attrs,
    _is_struct_elem,
    _iter_k,
    _mcid_from_item,
    _resolve_page_num,
    _resolve_tag,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Union of standard structure namespaces across all PDF versions (PDF 1.7 & PDF 2.0)
# per ISO 32000-2 Tables 366 to 375 and Annex M.
# Note: "H1" through "H6" are listed here for completeness but are technically redundant
# because the dynamic "_validate_standard_tag" function automatically accepts any "Hn"
# heading tags where "n" is a positive integer (supporting PDF 2.0's unbounded "Hn" roles).
_STANDARD_TAGS = frozenset(
    [
        # Grouping
        "Document",
        "DocumentFragment",
        "Part",
        "Art",
        "Sect",
        "Div",
        "Aside",
        "BlockQuote",
        "Caption",
        "TOC",
        "TOCI",
        "Index",
        "NonStruct",
        "Private",
        "Title",
        "Artifact",
        # Block-Level
        "P",
        "H",
        "H1",
        "H2",
        "H3",
        "H4",
        "H5",
        "H6",
        # List
        "L",
        "LI",
        "Lbl",
        "LBody",
        # Table
        "Table",
        "TR",
        "TH",
        "TD",
        "THead",
        "TBody",
        "TFoot",
        # Inline
        "Span",
        "Quote",
        "Note",
        "Reference",
        "BibEntry",
        "Code",
        "Link",
        "Annot",
        "Ruby",
        "RB",
        "RT",
        "RP",
        "Warichu",
        "WT",
        "WP",
        "FENote",
        "Sub",
        "Em",
        "Strong",
        # Illustration
        "Figure",
        "Formula",
        "Form",
    ]
)

# Standard Attribute Owners and their attributes per ISO 32000-2 Tables 378 to 386
_STANDARD_ATTR_OWNERS = {
    "Layout": frozenset(
        [
            # Layout Attributes (Table 379)
            "Placement",
            "WritingMode",
            "BackgroundColor",
            "BorderColor",
            "BorderStyle",
            "BorderThickness",
            "Color",
            "Padding",
            "SpaceBefore",
            "SpaceAfter",
            "StartIndent",
            "EndIndent",
            "TextIndent",
            "TextAlign",
            "BBox",
            "Width",
            "Height",
            "BlockAlign",
            "InlineAlign",
            "TBorderStyle",
            "TPadding",
            "BaselineShift",
            "LineHeight",
            "TextDecorationColor",
            "TextDecorationThickness",
            "TextDecorationType",
            "RubyAlign",
            "RubyPosition",
            "GlyphOrientationVertical",
            "TextPosition",
            # Column Attributes (Table 381)
            "ColumnCount",
            "ColumnWidths",
            "ColumnGap",
        ]
    ),
    "List": frozenset(
        [
            # List Attributes (Table 382)
            "ListNumbering",
            "ContinuedList",
            "ContinuedFrom",
        ]
    ),
    "PrintField": frozenset(
        [
            # PrintField Attributes (Table 385)
            "Role",
            "checked",
            "Checked",
            "Desc",
        ]
    ),
    "Table": frozenset(
        [
            # Table Attributes (Table 383/384)
            "RowSpan",
            "ColSpan",
            "Headers",
            "Scope",
            "Summary",
            "Short",
        ]
    ),
    "Artifact": frozenset(
        [
            # Artifact Attributes (Table 386)
            "Type",
            "Subtype",
            "BBox",
            "Attached",
        ]
    ),
}


# ---------------------------------------------------------------------------
# Issue collection helpers
# ---------------------------------------------------------------------------


def _add_issue(issues: list[dict], severity: str, code: str, message: str, **kw) -> None:
    """Helper to uniformly append issue dictionaries."""
    rec = {"severity": severity, "code": code, "message": message}
    rec.update(kw)
    issues.append(rec)


def _check_document_root(pdf, issues: list[dict]) -> bool:
    """
    Perform document-level checks.
    Returns True if a Structure Tree exists and we should continue, False otherwise.
    """
    mark_info = pdf.Root.get("/MarkInfo")
    if mark_info is None:
        _add_issue(
            issues, "error", "NO_MARKINFO", "Document root has no /MarkInfo — PDF is not marked"
        )
    elif not mark_info.get("/Marked"):
        _add_issue(issues, "error", "NOT_MARKED", "/MarkInfo exists but /Marked is not True")

    if pdf.Root.get("/StructTreeRoot") is None:
        _add_issue(issues, "error", "NO_STRUCT_TREE", "Document root has no /StructTreeRoot")
        return False

    lang = pdf.Root.get("/Lang")
    if lang is None:
        _add_issue(issues, "warning", "NO_LANG", "Document root has no /Lang attribute")

    return True


def _validate_attr_dict(attr_dict, current_path: str, issues: list[dict]) -> None:
    """Helper to validate a single attribute dictionary against allowed ISO keys."""
    import pikepdf

    if not isinstance(attr_dict, pikepdf.Dictionary):
        return

    owner = attr_dict.get("/O")
    if owner is None:
        return

    owner_str = str(owner).lstrip("/")
    if owner_str not in _STANDARD_ATTR_OWNERS:
        return

    allowed_keys = _STANDARD_ATTR_OWNERS[owner_str]
    for key in attr_dict.keys():
        key_str = str(key).lstrip("/")
        if key_str in ("O", "Type", "Revision"):
            continue
        if key_str not in allowed_keys:
            _add_issue(
                issues,
                "warning",
                "INVALID_STANDARD_ATTRIBUTE",
                f"Attribute '{key_str}' is not a valid standard attribute "
                f"for owner '{owner_str}' at {current_path}",
            )


def _check_attributes(elem, current_path: str, issues: list[dict]) -> None:
    """Validate attribute objects against standard owner keys per ISO 32000-2 §14.8.5."""
    import pikepdf

    attrs_obj = elem.get("/A")
    if attrs_obj is None:
        return

    attr_dicts = attrs_obj if isinstance(attrs_obj, pikepdf.Array) else [attrs_obj]
    for attr_dict in attr_dicts:
        _validate_attr_dict(attr_dict, current_path, issues)


def _process_heading(
    raw_tag: str,
    current_path: str,
    heading_levels_by_order: list[tuple[int, str, str]],
    role_map: dict,
) -> None:
    """Extract and track heading levels for hierarchy checks."""
    standard_tag, _ = _resolve_tag(raw_tag, role_map)
    if standard_tag.startswith("H") and len(standard_tag) >= 2:
        try:
            level = int(standard_tag[1:])
            heading_levels_by_order.append((level, current_path, raw_tag))
        except ValueError:
            pass


def _process_figure(
    standard_tag: str,
    raw_tag: str,
    attrs: dict,
    pg: int | None,
    current_path: str,
    seen_figures_without_alt: list[dict],
) -> None:
    """Track Figures that are missing alt or actual text."""
    if standard_tag == "Figure":
        if not attrs.get("alt") and not attrs.get("actual_text"):
            seen_figures_without_alt.append(
                {"path": current_path, "page": pg, "tag": standard_tag, "raw_tag": raw_tag}
            )


def _record_mcid_ref(
    item,
    mcid: int,
    parent_pg: int | None,
    page_objgen_index: dict,
    tree_mcids: dict[int, set[int]],
) -> None:
    """Track MCID references by identifying the correct page association."""
    item_pg = _resolve_page_num(item, page_objgen_index) if hasattr(item, "get") else None
    effective_pg = item_pg if item_pg is not None else parent_pg
    if effective_pg is not None:
        if effective_pg not in tree_mcids:
            tree_mcids[effective_pg] = set()
        tree_mcids[effective_pg].add(mcid)


def _is_node_seen(elem, seen: set) -> bool:
    """Helper to detect circular references and unhashable elements safely."""
    try:
        objgen = getattr(elem, "objgen", None)
        if objgen and objgen in seen:
            return True
        if objgen:
            seen.add(objgen)
    except (AttributeError, TypeError):
        pass
    return False


def _validate_standard_tag(
    raw_tag: str, standard_tag: str, current_path: str, issues: list[dict]
) -> None:
    """Helper to check resolved tags against the allowed ISO standard tag set."""
    if standard_tag in _STANDARD_TAGS or (
        standard_tag.startswith("H") and standard_tag[1:].isdigit()
    ):
        return

    # Handle internal fallback behavior of _resolve_tag gracefully
    if standard_tag == "unknown" or standard_tag == raw_tag:
        msg = f"Tag '{raw_tag}' is a non-standard tag at {current_path}"
        reported_tag = raw_tag
    else:
        msg = f"Tag '{raw_tag}' maps to non-standard tag '{standard_tag}' at {current_path}"
        reported_tag = standard_tag

    _add_issue(issues, "warning", "NON_STANDARD_TAG", msg, tag=reported_tag)


def _process_child_item(
    item,
    depth: int,
    current_path: str,
    seen: set,
    page_objgen_index: dict,
    heading_levels_by_order: list,
    seen_figures_without_alt: list,
    tree_mcids: dict,
    role_map: dict,
    issues: list,
    effective_pg: int | None,
) -> None:
    """Helper to process individual children nodes and their MCIDs."""
    mcid = _mcid_from_item(item)
    if mcid is not None:
        _record_mcid_ref(item, mcid, effective_pg, page_objgen_index, tree_mcids)
    elif _is_struct_elem(item):
        _walk_for_issues(
            item,
            depth + 1,
            current_path,
            seen,
            page_objgen_index,
            heading_levels_by_order,
            seen_figures_without_alt,
            tree_mcids,
            role_map,
            issues,
            inherited_pg=effective_pg,
        )


def _walk_for_issues(
    elem,
    depth: int,
    path: str,
    seen: set,
    page_objgen_index: dict,
    heading_levels_by_order: list[tuple[int, str, str]],
    seen_figures_without_alt: list[dict],
    tree_mcids: dict[int, set[int]],
    role_map: dict,
    issues: list[dict],
    inherited_pg: int | None = None,
) -> None:
    """Recursively walk the structure tree to collect elements and references."""
    if _is_node_seen(elem, seen):
        return

    raw_tag = _elem_tag(elem)
    standard_tag, _ = _resolve_tag(raw_tag, role_map)
    pg = _resolve_page_num(elem, page_objgen_index)
    effective_pg = pg if pg is not None else inherited_pg
    attrs = _elem_text_attrs(elem)
    current_path = f"{path}/{raw_tag}"

    _validate_standard_tag(raw_tag, standard_tag, current_path, issues)
    _check_attributes(elem, current_path, issues)
    _process_heading(raw_tag, current_path, heading_levels_by_order, role_map)
    _process_figure(
        standard_tag, raw_tag, attrs, effective_pg, current_path, seen_figures_without_alt
    )

    for item in _iter_k(elem):
        _process_child_item(
            item,
            depth,
            current_path,
            seen,
            page_objgen_index,
            heading_levels_by_order,
            seen_figures_without_alt,
            tree_mcids,
            role_map,
            issues,
            effective_pg,
        )


def _check_figure_issues(
    seen_figures_without_alt: list[dict], target_page_nums: set[int], issues: list[dict]
) -> None:
    """Process collected figures and report missing alt text errors."""
    for fig in seen_figures_without_alt:
        pg = fig["page"]
        if pg is None or pg in target_page_nums:
            standard_tag = fig.get("tag", "Figure")
            raw_tag = fig.get("raw_tag", standard_tag)

            if standard_tag != raw_tag:
                msg = f"{standard_tag} (role='{raw_tag}') at {fig['path']} has no /Alt text"
            else:
                msg = f"{standard_tag} at {fig['path']} has no /Alt text"

            _add_issue(
                issues,
                "error",
                "FIGURE_NO_ALT",
                msg,
                page=pg,
                tag=standard_tag,
            )


def _check_heading_issues(
    heading_levels_by_order: list[tuple[int, str, str]], issues: list[dict]
) -> None:
    """Analyse the sequence of headings for skips or missing root levels."""
    if not heading_levels_by_order:
        _add_issue(
            issues, "info", "NO_HEADINGS", "Document has no heading elements in structure tree"
        )
        return

    first_level, first_path, first_raw = heading_levels_by_order[0]
    if first_level != 1:
        msg = f"First heading in document is H{first_level}, expected H1"
        if first_raw != f"H{first_level}":
            msg = f"First heading in document is H{first_level} (role='{first_raw}'), expected H1"

        _add_issue(
            issues,
            "warning",
            "HEADING_NOT_H1_FIRST",
            msg,
            tag=f"H{first_level}",
        )
    prev = first_level
    for level, path, raw_tag in heading_levels_by_order[1:]:
        if level > prev + 1:
            msg = f"Heading level skip: H{prev} → H{level} at {path}"
            if raw_tag != f"H{level}":
                msg = f"Heading level skip: H{prev} → H{level} (role='{raw_tag}') at {path}"

            _add_issue(
                issues,
                "warning",
                "HEADING_LEVEL_SKIP",
                msg,
                tag=f"H{level}",
            )
        prev = level


def _check_stream_cross_references(
    pdf, target_page_nums: set[int], tree_mcids: dict[int, set[int]], issues: list[dict]
) -> None:
    """Compare MCIDs in the logical tree against MCIDs present in the page streams."""
    for page_num in sorted(target_page_nums):
        if page_num > len(pdf.pages):
            continue
        mcid_map, _lines = _build_mcid_stream_map(pdf, page_num)

        tree_set = tree_mcids.get(page_num, set())
        stream_set = set(mcid_map.keys())

        for mcid in sorted(tree_set - stream_set):
            _add_issue(
                issues,
                "warning",
                "MCID_NOT_IN_STREAM",
                f"Page {page_num}: MCID {mcid} referenced in structure tree "
                "but not found in page content stream (may be in XObject)",
                page=page_num,
            )

        for mcid in sorted(stream_set - tree_set):
            _add_issue(
                issues,
                "warning",
                "ORPHAN_MCID",
                f"Page {page_num}: MCID {mcid} in page stream "
                "is not referenced by any structure tree node",
                page=page_num,
            )


# ---------------------------------------------------------------------------
# Main Linter Hook
# ---------------------------------------------------------------------------


def _run_issues(
    pdf,
    target_page_nums: set[int],
) -> list[dict]:
    """
    Walk the tree and collect structural accessibility issues.
    Returns a list of issue dicts: {severity, code, message, page?, tag?}
    """
    issues: list[dict] = []

    if not _check_document_root(pdf, issues):
        return issues

    page_objgen_index = _build_page_objgen_index(pdf)
    role_map = _build_role_map(pdf)
    seen: set = set()

    tree_mcids: dict[int, set[int]] = {}
    heading_levels_by_order: list[tuple[int, str, str]] = []
    seen_figures_without_alt: list[dict] = []

    roots = _collect_tree_roots(pdf)
    for root in roots:
        _walk_for_issues(
            root,
            0,
            "",
            seen,
            page_objgen_index,
            heading_levels_by_order,
            seen_figures_without_alt,
            tree_mcids,
            role_map,
            issues,
        )

    _check_figure_issues(seen_figures_without_alt, target_page_nums, issues)
    _check_heading_issues(heading_levels_by_order, issues)
    _check_stream_cross_references(pdf, target_page_nums, tree_mcids, issues)

    return issues
