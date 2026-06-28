# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/helpers/test_tags_linter.py

"""Tests for accessibility rules and quality linter helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pikepdf

from pdftl.operations.helpers.tags_linter import (
    _process_figure,
    _process_heading,
    _run_issues,
)


def test_process_heading_unit_variations() -> None:
    """Verify heading level parser extracts integer suffixes and handles ValueErrors safely."""
    heading_levels: list[tuple[int, str, str]] = []

    # 1. Standard heading mapping
    _process_heading("H1", "/Document/H1", heading_levels, {})
    assert heading_levels == [(1, "/Document/H1", "H1")]

    # 2. Custom mapped heading mapping
    heading_levels.clear()
    _process_heading("chapter", "/Document/chapter", heading_levels, {"chapter": "H2"})
    assert heading_levels == [(2, "/Document/chapter", "chapter")]

    # 3. Heading with invalid suffix (H_bad) -> ValueError caught in lines 86-89
    heading_levels.clear()
    _process_heading("custom-bad", "/Document/custom-bad", heading_levels, {"custom-bad": "H_bad"})
    assert heading_levels == []  # ValueError swallowed gracefully


def test_process_figure_unit_variations() -> None:
    """Verify figure tracking identifies standard and role-mapped figures with missing alt texts."""
    seen_figures: list[dict] = []

    # 1. Standard Figure missing Alt text
    _process_figure("Figure", "Figure", {}, 1, "/Document/Figure", seen_figures)
    assert len(seen_figures) == 1
    assert seen_figures[0] == {
        "path": "/Document/Figure",
        "page": 1,
        "tag": "Figure",
        "raw_tag": "Figure",
    }

    # 2. Role-mapped Figure (e.g. standard_tag="Figure" for raw_tag="img") missing Alt text
    seen_figures.clear()
    _process_figure("Figure", "img", {}, 2, "/Document/img", seen_figures)
    assert len(seen_figures) == 1
    assert seen_figures[0] == {
        "path": "/Document/img",
        "page": 2,
        "tag": "Figure",
        "raw_tag": "img",
    }

    # 3. Figure with Alt text (should not be flagged)
    seen_figures.clear()
    _process_figure(
        "Figure", "Figure", {"alt": "A beautiful view"}, 1, "/Document/Figure", seen_figures
    )
    assert len(seen_figures) == 0


def test_run_issues_no_struct_tree() -> None:
    """Check linter catches general root markings and absent structures."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.Root = MagicMock()

    # Mock /MarkInfo structure
    mock_pdf.Root.get.side_effect = lambda key: None

    issues = _run_issues(mock_pdf, target_page_nums={1})

    codes = [iss["code"] for iss in issues]
    assert "NO_MARKINFO" in codes
    assert "NO_STRUCT_TREE" in codes


def test_run_issues_marked_invalid() -> None:
    """Test checks raise issues if marked context is declared but empty."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.Root = MagicMock()

    mark_info = MagicMock()
    mark_info.get.return_value = False

    def root_getter(key):
        if key == "/MarkInfo":
            return mark_info
        return None

    mock_pdf.Root.get.side_effect = root_getter

    issues = _run_issues(mock_pdf, target_page_nums={1})
    codes = [iss["code"] for iss in issues]
    assert "NOT_MARKED" in codes


@patch("pdftl.operations.helpers.tags_linter._collect_tree_roots")
@patch("pdftl.operations.helpers.tags_linter._build_page_objgen_index")
def test_run_issues_empty_roots(mock_index, mock_roots) -> None:
    """Hits the empty roots loop branch (coverage for lines checking empty child hierarchies)."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.Root = MagicMock()
    mock_pdf.Root.get.side_effect = (
        lambda k: MagicMock(get=lambda x: True) if k == "/MarkInfo" else MagicMock()
    )
    mock_roots.return_value = []
    mock_index.return_value = {}

    issues = _run_issues(mock_pdf, target_page_nums={1})
    assert isinstance(issues, list)
    # Since there are no roots, no structure violations occur
    codes = [iss["code"] for iss in issues]
    assert "NON_STANDARD_TAG" not in codes


def test_check_stream_cross_references_out_of_bounds() -> None:
    """Hits the page_num > len(pdf.pages) continue branch."""
    from pdftl.operations.helpers.tags_linter import _check_stream_cross_references

    mock_pdf = MagicMock()
    mock_pdf.pages = [MagicMock()]  # len == 1
    issues = []

    _check_stream_cross_references(mock_pdf, target_page_nums={2}, tree_mcids={}, issues=issues)
    assert len(issues) == 0


@patch("pdftl.operations.helpers.tags_linter._collect_tree_roots")
@patch("pdftl.operations.helpers.tags_linter._build_page_objgen_index")
def test_run_issues_figure_alt_missing(mock_index, mock_roots) -> None:
    """Test linter reports figures with missing alt tags or actual texts."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.Root = MagicMock()

    mark_info = MagicMock()
    mark_info.get.return_value = True
    struct_tree_root = MagicMock()

    def root_getter(key):
        if key == "/MarkInfo":
            return mark_info
        if key == "/StructTreeRoot":
            return struct_tree_root
        return None

    mock_pdf.Root.get.side_effect = root_getter

    # Mock single Figure node child lacking /Alt
    fig_node = MagicMock()
    fig_node.get.side_effect = lambda key: "/Figure" if key == "/S" else None
    mock_roots.return_value = [fig_node]
    mock_index.return_value = {}

    issues = _run_issues(mock_pdf, target_page_nums={1})
    codes = [iss["code"] for iss in issues]
    assert "FIGURE_NO_ALT" in codes

    # Assert standard string without role mapping appears
    fig_issue = next(iss for iss in issues if iss["code"] == "FIGURE_NO_ALT")
    assert "Figure at /Figure has no /Alt text" in fig_issue["message"]


@patch("pdftl.operations.helpers.tags_linter._build_role_map")
@patch("pdftl.operations.helpers.tags_linter._collect_tree_roots")
@patch("pdftl.operations.helpers.tags_linter._build_page_objgen_index")
def test_run_issues_role_mapped_figure(mock_index, mock_roots, mock_role_map) -> None:
    """Verify custom tag mapped to standard Figure triggers FIGURE_NO_ALT and surfaces role."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.Root = MagicMock()

    mark_info = MagicMock()
    mark_info.get.return_value = True
    struct_tree_root = MagicMock()

    def root_getter(key):
        if key == "/MarkInfo":
            return mark_info
        if key == "/StructTreeRoot":
            return struct_tree_root
        return None

    mock_pdf.Root.get.side_effect = root_getter

    # Role map custom 'img' tag -> standard 'Figure'
    mock_role_map.return_value = {"img": "Figure"}

    # Mock single custom 'img' tag node lacking /Alt
    img_node = MagicMock()
    img_node.get.side_effect = lambda key: "/img" if key == "/S" else None
    mock_roots.return_value = [img_node]
    mock_index.return_value = {}

    issues = _run_issues(mock_pdf, target_page_nums={1})
    codes = [iss["code"] for iss in issues]
    assert "FIGURE_NO_ALT" in codes

    # Assert the correct role string appears in the message
    fig_issue = next(iss for iss in issues if iss["code"] == "FIGURE_NO_ALT")
    assert "Figure (role='img') at /img has no /Alt text" in fig_issue["message"]


@patch("pdftl.operations.helpers.tags_linter._collect_tree_roots")
@patch("pdftl.operations.helpers.tags_linter._build_page_objgen_index")
def test_run_issues_heading_level_checks(mock_index, mock_roots) -> None:
    """Test linter flags incorrect initial heading sequences and heading skips."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.Root = MagicMock()

    mark_info = MagicMock()
    mark_info.get.return_value = True
    struct_tree_root = MagicMock()

    def root_getter(key):
        if key == "/MarkInfo":
            return mark_info
        if key == "/StructTreeRoot":
            return struct_tree_root
        return None

    mock_pdf.Root.get.side_effect = root_getter

    # Setup H2 as the first heading (triggering HEADING_NOT_H1_FIRST)
    h2_node = MagicMock()
    h2_node.get.side_effect = lambda key: "/H2" if key == "/S" else None

    # Child element is H4 (triggering HEADING_LEVEL_SKIP)
    h4_node = MagicMock()
    h4_node.get.side_effect = lambda key: "/H4" if key == "/S" else None

    class MockArray(list):
        pass

    with patch("pikepdf.Array", MockArray):
        h2_node.get.side_effect = (
            lambda key: "/H2" if key == "/S" else (MockArray([h4_node]) if key == "/K" else None)
        )

        mock_roots.return_value = [h2_node]
        mock_index.return_value = {}

        issues = _run_issues(mock_pdf, target_page_nums={1})

    codes = [iss["code"] for iss in issues]

    assert "HEADING_NOT_H1_FIRST" in codes
    assert "HEADING_LEVEL_SKIP" in codes

    # Assert standard string without role mapping appears
    skip_issue = next(iss for iss in issues if iss["code"] == "HEADING_LEVEL_SKIP")
    assert "Heading level skip: H2 → H4 at /H2/H4" in skip_issue["message"]


@patch("pdftl.operations.helpers.tags_linter._build_role_map")
@patch("pdftl.operations.helpers.tags_linter._collect_tree_roots")
@patch("pdftl.operations.helpers.tags_linter._build_page_objgen_index")
def test_run_issues_role_mapped_headings(mock_index, mock_roots, mock_role_map) -> None:
    """Verify role-mapped custom headings correctly trigger checks and surface roles."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.Root = MagicMock()

    mark_info = MagicMock()
    mark_info.get.return_value = True
    struct_tree_root = MagicMock()

    def root_getter(key):
        if key == "/MarkInfo":
            return mark_info
        if key == "/StructTreeRoot":
            return struct_tree_root
        return None

    mock_pdf.Root.get.side_effect = root_getter

    # Custom mapping 'chapter' -> 'H2', 'subchapter' -> 'H4'
    mock_role_map.return_value = {"chapter": "H2", "subchapter": "H4"}

    # Mock custom 'chapter' as H2 first (triggers HEADING_NOT_H1_FIRST)
    chapter_node = MagicMock()
    chapter_node.get.side_effect = lambda key: "/chapter" if key == "/S" else None

    # Mock custom 'subchapter' as H4 (causing skip: H2 -> H4)
    subchapter_node = MagicMock()
    subchapter_node.get.side_effect = lambda key: "/subchapter" if key == "/S" else None

    class MockArray(list):
        pass

    with patch("pikepdf.Array", MockArray):
        chapter_node.get.side_effect = (
            lambda key: "/chapter"
            if key == "/S"
            else (MockArray([subchapter_node]) if key == "/K" else None)
        )

        mock_roots.return_value = [chapter_node]
        mock_index.return_value = {}

        issues = _run_issues(mock_pdf, target_page_nums={1})

    codes = [iss["code"] for iss in issues]
    assert "HEADING_NOT_H1_FIRST" in codes
    assert "HEADING_LEVEL_SKIP" in codes

    # Assert the correct role string appears in the message for HEADING_NOT_H1_FIRST
    first_issue = next(iss for iss in issues if iss["code"] == "HEADING_NOT_H1_FIRST")
    assert (
        "First heading in document is H2 (role='chapter'), expected H1" in first_issue["message"]
    )

    # Assert the correct role string appears in the message for HEADING_LEVEL_SKIP
    skip_issue = next(iss for iss in issues if iss["code"] == "HEADING_LEVEL_SKIP")
    assert (
        "Heading level skip: H2 → H4 (role='subchapter') at /chapter/subchapter"
        in skip_issue["message"]
    )


@patch("pdftl.operations.helpers.tags_linter._build_mcid_stream_map")
@patch("pdftl.operations.helpers.tags_linter._collect_tree_roots")
@patch("pdftl.operations.helpers.tags_linter._build_page_objgen_index")
def test_run_issues_mcid_stream_cross_reference(mock_index, mock_roots, mock_stream_map) -> None:
    """Test linter flags MCIDs missing from stream and orphan MCIDs."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.Root = MagicMock()

    mark_info = MagicMock()
    mark_info.get.return_value = True
    struct_tree_root = MagicMock()

    def root_getter(key):
        if key == "/MarkInfo":
            return mark_info
        if key == "/StructTreeRoot":
            return struct_tree_root
        return None

    mock_pdf.Root.get.side_effect = root_getter

    # Mock page index mapping pointing to page 1
    mock_index.return_value = {(10, 0): 1}

    # Mock stream map to have MCIDs 0 and 1 explicitly in the stream
    mock_stream_map.return_value = ({0: {}, 1: {}}, ["line"])

    # Setup structural node referring to Page 1 and storing MCIDs 1 and 2
    pg_obj = MagicMock()
    pg_obj.objgen = (10, 0)

    p_node = MagicMock()
    p_node.objgen = (20, 0)

    class MockArray(list):
        pass

    with patch("pikepdf.Array", MockArray):

        def p_node_get(key):
            if key == "/S":
                return "/P"
            if key == "/Pg":
                return pg_obj
            if key == "/K":
                return MockArray([1, 2])
            return None

        p_node.get.side_effect = p_node_get
        mock_roots.return_value = [p_node]

        # Add a single page to mock_pdf so it processes page_num=1 correctly
        mock_pdf.pages = [MagicMock()]

        issues = _run_issues(mock_pdf, target_page_nums={1})

    codes = [iss["code"] for iss in issues]
    assert "MCID_NOT_IN_STREAM" in codes  # MCID 2 is in tree, not in stream
    assert "ORPHAN_MCID" in codes  # MCID 0 is in stream, not in tree


@patch("pdftl.operations.helpers.tags_linter._build_mcid_stream_map")
@patch("pdftl.operations.helpers.tags_linter._collect_tree_roots")
@patch("pdftl.operations.helpers.tags_linter._build_page_objgen_index")
def test_run_issues_mcr_pg_cross_reference(mock_index, mock_roots, mock_stream_map) -> None:
    """Verify MCID validation tracks page mappings through MCR /Pg elements even with parent-level omission."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.Root = MagicMock()

    mark_info = MagicMock()
    mark_info.get.return_value = True
    struct_tree_root = MagicMock()

    def root_getter(key):
        if key == "/MarkInfo":
            return mark_info
        if key == "/StructTreeRoot":
            return struct_tree_root
        return None

    mock_pdf.Root.get.side_effect = root_getter

    # Page index mapping points (10, 0) -> page 1
    mock_index.return_value = {(10, 0): 1}

    # Stream maps only MCID 1
    mock_stream_map.return_value = ({1: {}}, ["line"])

    # Setup parent element (P) without /Pg
    p_node = MagicMock()
    p_node.objgen = (20, 0)

    # Setup MCR child element with its own /Pg mapping to page 1
    pg_obj = MagicMock()
    pg_obj.objgen = (10, 0)

    mcr_child = MagicMock()
    mcr_child.get.side_effect = (
        lambda key: 1 if key == "/MCID" else (pg_obj if key == "/Pg" else None)
    )

    class MockArray(list):
        pass

    with patch("pikepdf.Array", MockArray):
        p_node.get.side_effect = (
            lambda key: "/P" if key == "/S" else (MockArray([mcr_child]) if key == "/K" else None)
        )
        mock_roots.return_value = [p_node]
        mock_pdf.pages = [MagicMock()]

        issues = _run_issues(mock_pdf, target_page_nums={1})

    codes = [iss["code"] for iss in issues]
    # If the page resolves properly, MCID 1 matches page 1 stream correctly (no MCID_NOT_IN_STREAM / ORPHAN_MCID errors)
    assert "MCID_NOT_IN_STREAM" not in codes
    assert "ORPHAN_MCID" not in codes


@patch("pdftl.operations.helpers.tags_linter._build_mcid_stream_map")
@patch("pdftl.operations.helpers.tags_linter._collect_tree_roots")
@patch("pdftl.operations.helpers.tags_linter._build_page_objgen_index")
def test_run_issues_inherited_pg(mock_index, mock_roots, mock_stream_map) -> None:
    """Ensure issues walk successfully inherits /Pg from ancestors for missing elements."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.Root = MagicMock()

    mark_info = MagicMock()
    mark_info.get.return_value = True
    struct_tree_root = MagicMock()

    def root_getter(key):
        if key == "/MarkInfo":
            return mark_info
        if key == "/StructTreeRoot":
            return struct_tree_root
        return None

    mock_pdf.Root.get.side_effect = root_getter

    mock_index.return_value = {(10, 0): 1}
    mock_stream_map.return_value = ({1: {}}, ["line"])

    # Setup Sect with /Pg pointing to page 1
    sect_node = MagicMock()
    sect_node.objgen = (20, 0)

    pg_obj = MagicMock()
    pg_obj.objgen = (10, 0)

    # Setup Figure child without /Pg
    fig_node = MagicMock()
    fig_node.objgen = (21, 0)

    # Setup MCR child without /Pg
    mcr_child = MagicMock()
    mcr_child.get.side_effect = lambda key: 1 if key == "/MCID" else None

    class MockArray(list):
        pass

    with patch("pikepdf.Array", MockArray):
        fig_node.get.side_effect = (
            lambda key: "/Figure"
            if key == "/S"
            else (MockArray([mcr_child]) if key == "/K" else None)
        )

        sect_node.get.side_effect = (
            lambda key: "/Sect"
            if key == "/S"
            else (pg_obj if key == "/Pg" else (MockArray([fig_node]) if key == "/K" else None))
        )

        mock_roots.return_value = [sect_node]
        mock_pdf.pages = [MagicMock()]

        issues = _run_issues(mock_pdf, target_page_nums={1})

    # Check that the missing alt text correctly resolved the page to Page 1
    fig_issues = [iss for iss in issues if iss["code"] == "FIGURE_NO_ALT"]
    assert len(fig_issues) == 1
    assert fig_issues[0]["page"] == 1

    # Verify stream resolution worked because MCID inherited page 1
    codes = [iss["code"] for iss in issues]
    assert "MCID_NOT_IN_STREAM" not in codes


@patch("pdftl.operations.helpers.tags_linter._collect_tree_roots")
@patch("pdftl.operations.helpers.tags_linter._build_page_objgen_index")
def test_run_issues_edge_cases(mock_index, mock_roots) -> None:
    """Cover circular references, unhashable objgens, and invalid heading formats."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.Root = MagicMock()

    mark_info = MagicMock()
    mark_info.get.return_value = True
    struct_tree_root = MagicMock()

    def root_getter(key):
        if key == "/MarkInfo":
            return mark_info
        if key == "/StructTreeRoot":
            return struct_tree_root
        return None

    mock_pdf.Root.get.side_effect = root_getter

    # Node 1: Invalid heading suffix (e.g. "Head") tests ValueError handling
    head_node = MagicMock()
    head_node.objgen = (2, 0)
    head_node.get.side_effect = lambda k: "/Head" if k == "/S" else None

    # Node 2: Unhashable objgen -> tests TypeError handling during `in seen` check
    unhashable_node = MagicMock()
    unhashable_node.objgen = [1, 2]  # Truthy and unhashable to hit the except block
    unhashable_node.get.side_effect = lambda k: "/P" if k == "/S" else None

    # Node 3: Circular reference logic validation
    circ_node = MagicMock()
    circ_node.objgen = (3, 0)

    class MockArray(list):
        pass

    with patch("pikepdf.Array", MockArray):
        circ_node.get.side_effect = (
            lambda k: "/P" if k == "/S" else (MockArray([circ_node]) if k == "/K" else None)
        )

        root_node = MagicMock()
        root_node.objgen = (1, 0)
        root_node.get.side_effect = (
            lambda k: "/Document"
            if k == "/S"
            else (MockArray([head_node, unhashable_node, circ_node]) if k == "/K" else None)
        )

        mock_roots.return_value = [root_node]
        mock_index.return_value = {}

        # Execute. Should not crash and should safely bypass these edge cases.
        issues = _run_issues(mock_pdf, target_page_nums={1})

    codes = [iss["code"] for iss in issues]
    assert "NO_HEADINGS" in codes


@patch("pdftl.operations.helpers.tags_linter._collect_tree_roots")
@patch("pdftl.operations.helpers.tags_linter._build_page_objgen_index")
def test_run_issues_standard_tag_validation(mock_index, mock_roots) -> None:
    """Covers lines verifying standard tag completeness per ISO 32000-2 §14.8."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.Root = MagicMock()
    mock_pdf.Root.get.side_effect = (
        lambda k: MagicMock(get=lambda x: True) if k == "/MarkInfo" else MagicMock()
    )
    mock_index.return_value = {}

    # Node 1: Custom non-standard tag
    node_bad = MagicMock()
    node_bad.objgen = (1, 0)
    node_bad.get.side_effect = lambda k: "/CustomTag" if k == "/S" else None

    # Node 2: Valid extended H tag
    node_h7 = MagicMock()
    node_h7.objgen = (2, 0)
    node_h7.get.side_effect = lambda k: "/H7" if k == "/S" else None

    class MockArray(list):
        pass

    root_node = MagicMock()
    root_node.objgen = (3, 0)

    with patch("pikepdf.Array", MockArray):
        root_node.get.side_effect = (
            lambda k: "/Document"
            if k == "/S"
            else (MockArray([node_bad, node_h7]) if k == "/K" else None)
        )
        mock_roots.return_value = [root_node]

        issues = _run_issues(mock_pdf, target_page_nums={1})

    codes = [iss["code"] for iss in issues]

    assert "NON_STANDARD_TAG" in codes
    bad_issue = next(iss for iss in issues if iss["code"] == "NON_STANDARD_TAG")
    assert "Tag 'CustomTag' is a non-standard tag" in bad_issue["message"]
    assert bad_issue["tag"] == "CustomTag"


@patch("pdftl.operations.helpers.tags_linter._build_role_map")
@patch("pdftl.operations.helpers.tags_linter._collect_tree_roots")
@patch("pdftl.operations.helpers.tags_linter._build_page_objgen_index")
def test_run_issues_non_standard_role_mapped_tag(mock_index, mock_roots, mock_role_map) -> None:
    """Covers lines 295-296: custom tag role-mapped to another non-standard tag."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.Root = MagicMock()
    mock_pdf.Root.get.side_effect = (
        lambda k: MagicMock(get=lambda x: True) if k == "/MarkInfo" else MagicMock()
    )
    mock_index.return_value = {}

    # Map "CustomRaw" -> "CustomMapped" (neither is standard)
    mock_role_map.return_value = {"CustomRaw": "CustomMapped"}

    node = MagicMock()
    node.objgen = (1, 0)
    node.get.side_effect = lambda k: "/CustomRaw" if k == "/S" else None

    mock_roots.return_value = [node]

    issues = _run_issues(mock_pdf, target_page_nums={1})
    codes = [iss["code"] for iss in issues]

    assert "NON_STANDARD_TAG" in codes
    issue = next(iss for iss in issues if iss["code"] == "NON_STANDARD_TAG")
    assert "Tag 'CustomRaw' maps to non-standard tag 'CustomMapped'" in issue["message"]
    assert issue["tag"] == "CustomMapped"


@patch("pdftl.operations.helpers.tags_linter._collect_tree_roots")
@patch("pdftl.operations.helpers.tags_linter._build_page_objgen_index")
def test_run_issues_attribute_validation(mock_index, mock_roots) -> None:
    """Covers lines verifying standard attribute objects per ISO 32000-2 §14.8.5."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.Root = MagicMock()
    mock_pdf.Root.get.side_effect = (
        lambda k: MagicMock(get=lambda x: True) if k == "/MarkInfo" else MagicMock()
    )
    mock_index.return_value = {}

    # Create dicts representing various attribute combinations
    # 1. Valid Dict
    valid_attr = pikepdf.Dictionary({"/O": pikepdf.Name("/Table"), "/RowSpan": 2, "/Revision": 1})
    # 2. Invalid Standard Dict
    invalid_attr = pikepdf.Dictionary({"/O": pikepdf.Name("/Table"), "/WrongKey": 1})
    # 3. Ignored custom owner
    custom_owner = pikepdf.Dictionary({"/O": pikepdf.Name("/MyXML"), "/WeirdKey": 1})
    # 4. Dict missing /O
    no_owner = pikepdf.Dictionary({"/RowSpan": 2})

    node = MagicMock()
    node.objgen = (1, 0)

    # Test array containing string, valid dict, invalid dict, custom owner, no owner
    attr_array = pikepdf.Array(
        [pikepdf.String("not_a_dict"), valid_attr, invalid_attr, custom_owner, no_owner]
    )

    node.get.side_effect = lambda k: "/P" if k == "/S" else (attr_array if k == "/A" else None)
    mock_roots.return_value = [node]

    issues = _run_issues(mock_pdf, target_page_nums={1})
    codes = [iss["code"] for iss in issues]

    assert "INVALID_STANDARD_ATTRIBUTE" in codes
    bad_issue = next(iss for iss in issues if iss["code"] == "INVALID_STANDARD_ATTRIBUTE")
    assert (
        "Attribute 'WrongKey' is not a valid standard attribute for owner 'Table'"
        in bad_issue["message"]
    )


@patch("pdftl.operations.helpers.tags_linter._collect_tree_roots")
@patch("pdftl.operations.helpers.tags_linter._build_page_objgen_index")
def test_run_issues_attribute_validation_single_dict(mock_index, mock_roots) -> None:
    """Covers ternary branch for single attribute Dictionary per ISO 32000-2 §14.8.5."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.Root = MagicMock()
    mock_pdf.Root.get.side_effect = (
        lambda k: MagicMock(get=lambda x: True) if k == "/MarkInfo" else MagicMock()
    )
    mock_index.return_value = {}

    node = MagicMock()
    node.objgen = (1, 0)

    # Test single pikepdf.Dictionary direct assignment (not wrapped in Array)
    single_dict = pikepdf.Dictionary({"/O": pikepdf.Name("/Table"), "/BadKey": 1})
    node.get.side_effect = lambda k: "/P" if k == "/S" else (single_dict if k == "/A" else None)
    mock_roots.return_value = [node]

    issues = _run_issues(mock_pdf, target_page_nums={1})
    codes = [iss["code"] for iss in issues]

    assert "INVALID_STANDARD_ATTRIBUTE" in codes


@patch("pdftl.operations.helpers.tags_linter._collect_tree_roots")
@patch("pdftl.operations.helpers.tags_linter._build_page_objgen_index")
def test_run_issues_new_standard_tags_validation(mock_index, mock_roots) -> None:
    """Verify that Title, Artifact, and FENote are accepted as standard, but Sup is flagged."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.Root = MagicMock()
    mock_pdf.Root.get.side_effect = (
        lambda k: MagicMock(get=lambda x: True) if k == "/MarkInfo" else MagicMock()
    )
    mock_index.return_value = {}

    # Node 1: "Title" (standard in PDF 2.0)
    node_title = MagicMock()
    node_title.objgen = (1, 0)
    node_title.get.side_effect = lambda k: "/Title" if k == "/S" else None

    # Node 2: "Artifact" (standard in PDF 2.0)
    node_artifact = MagicMock()
    node_artifact.objgen = (2, 0)
    node_artifact.get.side_effect = lambda k: "/Artifact" if k == "/S" else None

    # Node 3: "FENote" (standard in PDF 2.0)
    node_fenote = MagicMock()
    node_fenote.objgen = (3, 0)
    node_fenote.get.side_effect = lambda k: "/FENote" if k == "/S" else None

    # Node 4: "Sup" (NOT standard, text position attribute value)
    node_sup = MagicMock()
    node_sup.objgen = (4, 0)
    node_sup.get.side_effect = lambda k: "/Sup" if k == "/S" else None

    class MockArray(list):
        pass

    root_node = MagicMock()
    root_node.objgen = (5, 0)

    with patch("pikepdf.Array", MockArray):
        root_node.get.side_effect = (
            lambda k: "/Document"
            if k == "/S"
            else (
                MockArray([node_title, node_artifact, node_fenote, node_sup])
                if k == "/K"
                else None
            )
        )
        mock_roots.return_value = [root_node]

        issues = _run_issues(mock_pdf, target_page_nums={1})

    codes = [iss["code"] for iss in issues]

    # Only "Sup" should be flagged as non-standard; Title, Artifact and FENote are fine!
    assert "NON_STANDARD_TAG" in codes
    bad_issues = [iss for iss in issues if iss["code"] == "NON_STANDARD_TAG"]
    assert len(bad_issues) == 1
    assert bad_issues[0]["tag"] == "Sup"
    assert "Tag 'Sup' is a non-standard tag" in bad_issues[0]["message"]


@patch("pdftl.operations.helpers.tags_linter._collect_tree_roots")
@patch("pdftl.operations.helpers.tags_linter._build_page_objgen_index")
def test_run_issues_new_layout_and_artifact_attributes_validation(mock_index, mock_roots) -> None:
    """Verify that TextPosition, ColumnGap, and Artifact attributes are validated correctly."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.Root = MagicMock()
    mock_pdf.Root.get.side_effect = (
        lambda k: MagicMock(get=lambda x: True) if k == "/MarkInfo" else MagicMock()
    )
    mock_index.return_value = {}

    # Valid Layout attributes: /TextPosition and singular /ColumnGap (previously ColumnGaps)
    valid_layout_attr = pikepdf.Dictionary(
        {"/O": pikepdf.Name("/Layout"), "/TextPosition": pikepdf.Name("/Sub"), "/ColumnGap": 12}
    )

    # Valid Artifact attributes: /Type, /Subtype, /BBox, /Attached (per Table 386)
    valid_artifact_attr = pikepdf.Dictionary(
        {
            "/O": pikepdf.Name("/Artifact"),
            "/Type": pikepdf.Name("/Pagination"),
            "/Subtype": pikepdf.Name("/Header"),
            "/BBox": pikepdf.Array([0, 0, 100, 20]),
            "/Attached": pikepdf.Name("/Top"),
        }
    )

    # Invalid Artifact attribute
    invalid_artifact_attr = pikepdf.Dictionary(
        {"/O": pikepdf.Name("/Artifact"), "/InvalidArtifactKey": 123}
    )

    node = MagicMock()
    node.objgen = (1, 0)

    attr_array = pikepdf.Array([valid_layout_attr, valid_artifact_attr, invalid_artifact_attr])
    node.get.side_effect = lambda k: "/P" if k == "/S" else (attr_array if k == "/A" else None)
    mock_roots.return_value = [node]

    issues = _run_issues(mock_pdf, target_page_nums={1})
    codes = [iss["code"] for iss in issues]

    assert "INVALID_STANDARD_ATTRIBUTE" in codes
    bad_issue = next(iss for iss in issues if iss["code"] == "INVALID_STANDARD_ATTRIBUTE")
    assert (
        "Attribute 'InvalidArtifactKey' is not a valid standard attribute for owner 'Artifact'"
        in bad_issue["message"]
    )
