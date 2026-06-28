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


def _process_heading(
    raw_tag: str,
    current_path: str,
    heading_levels_by_order: list[tuple[int, str, str]],
    role_map: dict,
) -> None:
    """Extract and track heading levels for hierarchy checks.

    Uses the *resolved* standard tag so that role-mapped heading names
    (e.g. "chapter" -> "H1") are correctly recognised.
    """
    standard_tag, _ = _resolve_tag(raw_tag, role_map)
    if standard_tag.startswith("H") and len(standard_tag) >= 2:
        try:
            level = int(standard_tag[1:])
            heading_levels_by_order.append((level, current_path, raw_tag))
        except ValueError:
            # If the heading tag suffix is not an integer (e.g., '/Head' or '/H'),
            # we skip recording it as a numbered heading level and continue.
            pass


def _process_figure(
    standard_tag: str,
    raw_tag: str,
    attrs: dict,
    pg: int | None,
    current_path: str,
    seen_figures_without_alt: list[dict],
) -> None:
    """Track Figures that are missing alt or actual text.

    Receives the already-resolved standard tag so role-mapped figures
    (e.g. "img" -> "Figure") are correctly caught.
    """
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
    # Prefer /Pg from the MCR dict itself (hand-tagged PDFs)
    item_pg = _resolve_page_num(item, page_objgen_index) if hasattr(item, "get") else None
    effective_pg = item_pg if item_pg is not None else parent_pg
    if effective_pg is not None:
        if effective_pg not in tree_mcids:
            tree_mcids[effective_pg] = set()
        tree_mcids[effective_pg].add(mcid)


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
    inherited_pg: int | None = None,
) -> None:
    """Recursively walk the structure tree to collect elements and references."""
    try:
        objgen = getattr(elem, "objgen", None)
        if objgen and objgen in seen:
            return
        if objgen:
            seen.add(objgen)
    except (AttributeError, TypeError):
        # If the element lacks an 'objgen' attribute or is unhashable,
        # we safely skip tracking circular references for it and proceed.
        pass

    raw_tag = _elem_tag(elem)
    standard_tag, _ = _resolve_tag(raw_tag, role_map)
    pg = _resolve_page_num(elem, page_objgen_index)
    effective_pg = pg if pg is not None else inherited_pg
    attrs = _elem_text_attrs(elem)
    current_path = f"{path}/{raw_tag}"

    _process_heading(raw_tag, current_path, heading_levels_by_order, role_map)
    _process_figure(
        standard_tag, raw_tag, attrs, effective_pg, current_path, seen_figures_without_alt
    )

    for item in _iter_k(elem):
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
                inherited_pg=effective_pg,
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
                "but not found in page content stream (may be in an XObject)",
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
        )

    _check_figure_issues(seen_figures_without_alt, target_page_nums, issues)
    _check_heading_issues(heading_levels_by_order, issues)
    _check_stream_cross_references(pdf, target_page_nums, tree_mcids, issues)

    return issues
