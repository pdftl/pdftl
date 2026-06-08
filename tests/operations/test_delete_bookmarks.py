# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/test_delete_bookmarks.py

import logging

import pikepdf

from pdftl.operations.delete_bookmarks import delete_bookmarks
from pdftl.utils.destinations import get_named_destinations, get_page_map, resolve_dest_to_page_num

# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------


def setup_test_outline(pdf: pikepdf.Pdf) -> None:
    with pdf.open_outline() as outline:
        outline.root.clear()

        i1 = pikepdf.OutlineItem("Page 1", 0)
        i2 = pikepdf.OutlineItem("Page 2", 1)
        i3 = pikepdf.OutlineItem("Page 3", 2)
        i1.children.extend([i2, i3])

        i4 = pikepdf.OutlineItem("Page 4", 3)
        i5 = pikepdf.OutlineItem("Page 5", 4)
        i6 = pikepdf.OutlineItem("Page 6", 5)
        i5.children.append(i6)
        i4.children.append(i5)  # i5 is a child of i4, not a root sibling

        outline.root.extend([i1, i4])  # only two root-level nodes


def setup_destinationless_outline(pdf: pikepdf.Pdf) -> None:
    """Constructs a tree containing a destination-less structural bookmark."""
    # Step 1: write the outline (pikepdf requires a valid page ref to serialize)
    with pdf.open_outline() as outline:
        outline.root.clear()
        i_nodest = pikepdf.OutlineItem("Structural Parent", 0)
        i2 = pikepdf.OutlineItem("Page 2", 1)
        i_nodest.children.append(i2)
        outline.root.append(i_nodest)

    # Step 2: now the outline dict is live in the PDF object tree —
    # remove /Dest directly so the node has no page destination.
    outlines_obj = pdf.Root.get("/Outlines")
    if outlines_obj is not None:
        first_item = outlines_obj.get("/First")
        if first_item is not None:
            if "/Dest" in first_item:
                del first_item["/Dest"]
            if "/A" in first_item:
                del first_item["/A"]


def get_outline_structure(pdf: pikepdf.Pdf) -> list:
    """Maps the outline tree back to a nested list of 1-based page numbers."""
    page_map = get_page_map(pdf.pages)
    named_dests = get_named_destinations(pdf) or {}

    with pdf.open_outline() as outline:
        return _node_to_list(outline.root, page_map, named_dests)


def _node_to_list(nodes, page_map, named_dests) -> list:
    res = []
    for node in nodes:
        resolved = resolve_dest_to_page_num(node, page_map, named_dests)
        pnum = resolved.page_num if resolved else None

        if node.children:
            children_res = _node_to_list(node.children, page_map, named_dests)
            res.append(pnum)
            res.append(children_res)
        else:
            res.append(pnum)
    return res


# ---------------------------------------------------------------------------
# Unit Tests
# ---------------------------------------------------------------------------


def test_delete_all_bookmarks_when_no_specs(six_page_pdf):
    """Omitting page specs should completely clear the outline tree."""
    pdf = pikepdf.open(six_page_pdf)
    setup_test_outline(pdf)

    res = delete_bookmarks(pdf, [])

    assert res.success is True
    assert get_outline_structure(pdf) == []


def test_delete_bookmarks_worked_example_1(six_page_pdf):
    """delete_bookmarks 2  =>  [1, [3], 4, [5, [6]]]"""
    pdf = pikepdf.open(six_page_pdf)
    setup_test_outline(pdf)

    res = delete_bookmarks(pdf, ["2"])

    assert res.success is True
    assert get_outline_structure(pdf) == [1, [3], 4, [5, [6]]]


def test_delete_bookmarks_worked_example_2(six_page_pdf, caplog):
    """delete_bookmarks 1 (safe mode)  =>  Unchanged + warning emitted."""
    pdf = pikepdf.open(six_page_pdf)
    setup_test_outline(pdf)

    with caplog.at_level(logging.WARNING):
        delete_bookmarks(pdf, ["1"])

    assert get_outline_structure(pdf) == [1, [2, 3], 4, [5, [6]]]
    assert "was not deleted because it has descendants" in caplog.text


def test_delete_bookmarks_worked_example_3(six_page_pdf):
    """delete_bookmarks 1-3  =>  [4, [5, [6]]]"""
    pdf = pikepdf.open(six_page_pdf)
    setup_test_outline(pdf)

    res = delete_bookmarks(pdf, ["1-3"])

    assert res.success is True
    assert get_outline_structure(pdf) == [4, [5, [6]]]


def test_delete_bookmarks_worked_example_4(six_page_pdf):
    """delete_bookmarks 1 recursive  =>  [4, [5, [6]]]"""
    pdf = pikepdf.open(six_page_pdf)
    setup_test_outline(pdf)

    res = delete_bookmarks(pdf, ["1", "recursive"])

    assert res.success is True
    assert get_outline_structure(pdf) == [4, [5, [6]]]


def test_delete_bookmarks_worked_example_5(six_page_pdf, caplog):
    """delete_bookmarks 5 (safe mode)  =>  Unchanged + warning emitted."""
    pdf = pikepdf.open(six_page_pdf)
    setup_test_outline(pdf)

    with caplog.at_level(logging.WARNING):
        delete_bookmarks(pdf, ["5"])

    assert get_outline_structure(pdf) == [1, [2, 3], 4, [5, [6]]]
    assert "was not deleted because it has descendants" in caplog.text


def test_delete_bookmarks_worked_example_6(six_page_pdf):
    """delete_bookmarks 5-6  =>  [1, [2, 3], 4]"""
    pdf = pikepdf.open(six_page_pdf)
    setup_test_outline(pdf)

    res = delete_bookmarks(pdf, ["5-6"])

    assert res.success is True
    assert get_outline_structure(pdf) == [1, [2, 3], 4]


def test_delete_bookmarks_worked_example_7(six_page_pdf):
    """delete_bookmarks 1-3 5-6  =>  [4]"""
    pdf = pikepdf.open(six_page_pdf)
    setup_test_outline(pdf)

    res = delete_bookmarks(pdf, ["1-3", "5-6"])

    assert res.success is True
    assert get_outline_structure(pdf) == [4]


def test_destinationless_bookmarks_default_safe_mode(six_page_pdf):
    """By default, destination-less nodes survive and block ancestor deletion."""
    pdf = pikepdf.open(six_page_pdf)
    setup_destinationless_outline(pdf)

    # Target page 2 (the child element)
    res = delete_bookmarks(pdf, ["2"])
    assert res.success is True

    # Structural parent has no target page (None), child is deleted, but parent survives
    assert get_outline_structure(pdf) == [None]


def test_destinationless_bookmarks_with_no_dest_flag(six_page_pdf):
    """With 'no_dest', destination-less nodes are evaluated as matched."""
    pdf = pikepdf.open(six_page_pdf)
    setup_destinationless_outline(pdf)

    # Target page 2 along with the no_dest flag
    res = delete_bookmarks(pdf, ["2", "no_dest"])
    assert res.success is True

    # Parent matches because of no_dest and its children are cleanly deleted -> whole tree is cleared
    assert get_outline_structure(pdf) == []


# ---------------------------------------------------------------------------
# Integration Test
# ---------------------------------------------------------------------------


def test_delete_bookmarks_cli_pipeline(runner, six_page_pdf, temp_dir):
    """Verifies end-to-end command line syntax via the pdftl execution harness."""
    in_pdf = temp_dir / "input_tree.pdf"
    out_pdf = temp_dir / "output_tree.pdf"

    # 1. Prepare file with outline structure
    with pikepdf.open(six_page_pdf) as pdf:
        setup_test_outline(pdf)
        pdf.save(in_pdf)

    # 2. Fire the tool via the runner fixture
    runner.run("pdftl", [str(in_pdf), "delete_bookmarks", "2", "output", str(out_pdf)])

    # 3. Assert downstream structural compliance
    with pikepdf.open(out_pdf) as pdf:
        structure = get_outline_structure(pdf)
        assert structure == [1, [3], 4, [5, [6]]]


def test_destinationless_bookmarks_recursive_with_no_dest_flag(six_page_pdf):
    """With 'recursive' + 'no_dest', dest-less nodes are matched and their
    entire subtree is swept. Covers line 268 (_process_item_recursive, no-dest branch)."""
    pdf = pikepdf.open(six_page_pdf)
    setup_destinationless_outline(pdf)

    res = delete_bookmarks(pdf, ["2", "recursive", "no_dest"])
    assert res.success is True

    # Structural Parent matched via no_dest -> entire subtree deleted.
    assert get_outline_structure(pdf) == []


def test_destinationless_bookmarks_recursive_without_no_dest_flag(six_page_pdf):
    """With 'recursive' but no 'no_dest', a dest-less parent is not matched,
    so it survives — but its matched child is deleted, leaving it childless."""
    pdf = pikepdf.open(six_page_pdf)
    setup_destinationless_outline(pdf)

    res = delete_bookmarks(pdf, ["2", "recursive"])
    assert res.success is True

    # Child (page 2) deleted recursively; dest-less parent unmatched -> survives,
    # now with no children.
    assert get_outline_structure(pdf) == [None]


def setup_nested_destinationless_outline(pdf: pikepdf.Pdf) -> None:
    """Tree: Page1 -> StructuralParent(no dest) -> Page2
    i.e. [1, [None, [2]]]
    """
    with pdf.open_outline() as outline:
        outline.root.clear()
        i1 = pikepdf.OutlineItem("Page 1", 0)
        i_nodest = pikepdf.OutlineItem("Structural Parent", 0)  # dest stripped below
        i2 = pikepdf.OutlineItem("Page 2", 1)
        i_nodest.children.append(i2)
        i1.children.append(i_nodest)
        outline.root.append(i1)

    # Strip /Dest from the intermediate node via the live PDF object tree
    outlines_obj = pdf.Root.get("/Outlines")
    if outlines_obj is not None:
        first_item = outlines_obj.get("/First")  # i1
        if first_item is not None:
            intermediate = first_item.get("/First")  # i_nodest (first child of i1)
            if intermediate is not None:
                if "/Dest" in intermediate:
                    del intermediate["/Dest"]
                if "/A" in intermediate:
                    del intermediate["/A"]


def test_destinationless_intermediate_blocks_safe_deletion(six_page_pdf, caplog):
    """A dest-less intermediate node (no_dest=False) is treated as unmatched,
    so it blocks deletion of the matched ancestor in safe mode."""
    pdf = pikepdf.open(six_page_pdf)
    setup_nested_destinationless_outline(pdf)

    with caplog.at_level(logging.WARNING):
        delete_bookmarks(pdf, ["1"])

    # Page 1 is matched but cannot be deleted: its subtree contains unmatched nodes
    # (the dest-less intermediate and Page 2).  Tree is unchanged.
    assert get_outline_structure(pdf) == [1, [None, [2]]]
    assert "was not deleted because it has descendants" in caplog.text
